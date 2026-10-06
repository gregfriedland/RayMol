"""Double-precision direct contacts and exact isolated-ligand local strain records."""

import math
import numpy as np
import openmm as mm
from openmm import unit

from .protocol import BaseModelNoExtra
from .prepare import Preparation


class Score(BaseModelNoExtra):
    # OpenMM 8.6.1 SimTKOpenMMRealType.h constants, verified against isolated forces.
    coulomb: float = 1 / (4 * math.pi * (1e-6 * 8.8541878128e-12 / (1.602176634e-19 ** 2 * 6.02214076e23)))

    @staticmethod
    def agree(actual, reference, absolute=1e-4, relative=1e-6):
        if not np.isfinite(actual).all() or not np.isfinite(reference).all() or np.any(np.abs(np.asarray(actual) - reference) > absolute + relative * np.abs(reference)):
            raise ValueError(f"Numerical disagreement: {actual} vs {reference}")

    @staticmethod
    def reference(system, positions):
        integrator = mm.VerletIntegrator(0.001)
        context = mm.Context(system, integrator, mm.Platform.getPlatformByName("Reference"))
        try:
            context.setPositions(positions * unit.nanometer)
            value = context.getState(getEnergy=True).getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
            if not math.isfinite(value): raise ValueError("Non-finite reference energy")
            return value
        finally:
            del context, integrator

    def direct(self, model, positions, cancelled, tile=128):
        n, count = model.nprotein, len(model.particles)
        q, sigma, epsilon = np.array([[p[k] for p in model.particles] for k in ("q", "sigma_nm", "epsilon_kj")])
        values = np.empty((n, count - n, 3), dtype=np.float64)
        for start in range(0, n, tile):
            Preparation.check_cancel(cancelled)
            stop = min(n, start + tile)
            distance = np.linalg.norm(positions[start:stop, None] - positions[None, n:], axis=2)
            if not np.isfinite(distance).all() or np.any(distance <= 0): raise ValueError("Overlapping/non-finite contact coordinates")
            ratio6 = ((sigma[start:stop, None] + sigma[None, n:]) / (2 * distance)) ** 6
            prefactor = 4 * np.sqrt(epsilon[start:stop, None] * epsilon[None, n:])
            values[start:stop, :, 0] = self.coulomb * q[start:stop, None] * q[None, n:] / distance
            values[start:stop, :, 1] = -prefactor * ratio6
            values[start:stop, :, 2] = prefactor * ratio6 ** 2
        if not np.isfinite(values).all(): raise ValueError("Non-finite direct energies")
        totals = values.sum(axis=(0, 1))
        return values, {"units": "kJ/mol", "coulomb": float(totals[0]), "attraction": float(totals[1]), "repulsion": float(totals[2]), "net_lj": float(totals[1] + totals[2]), "pair_shape": list(values.shape), "pair_id": "protein_index * nligand + ligand_index", "interpretation": "Vacuum unscreened contacts; not binding free energy"}

    @staticmethod
    def geometry(kind, atoms, positions):
        xyz = positions[list(atoms)]
        if kind == "bond": return float(np.linalg.norm(xyz[0] - xyz[1]))
        if kind == "angle":
            a, b = xyz[0] - xyz[1], xyz[2] - xyz[1]
            norm = np.linalg.norm(a) * np.linalg.norm(b)
            if norm <= 0: raise ValueError("Degenerate angle")
            return float(np.arctan2(np.linalg.norm(np.cross(a, b)), np.dot(a, b)))
        b0, b1, b2 = -(xyz[1] - xyz[0]), xyz[2] - xyz[1], xyz[3] - xyz[2]
        length = np.linalg.norm(b1)
        if length <= 0: raise ValueError("Degenerate dihedral axis")
        axis = b1 / length
        v, w = b0 - np.dot(b0, axis) * axis, b2 - np.dot(b2, axis) * axis
        if np.linalg.norm(v) <= 1e-12 or np.linalg.norm(w) <= 1e-12: raise ValueError("Undefined dihedral plane")
        return float(np.arctan2(np.dot(np.cross(axis, v), w), np.dot(v, w)))

    def internal(self, model, positions):
        n, count = model.nprotein, len(model.particles)
        records = []
        for term in model.terms:
            if min(term["atoms"]) < n or term["kind"] == "exception": continue
            measured = self.geometry(term["kind"], term["atoms"], positions)
            parameters = term["parameters"]
            if term["kind"] in {"bond", "angle"}:
                energy = 0.5 * parameters[1] * (measured - parameters[0]) ** 2
            else:
                periodicity, phase, amplitude = parameters
                energy = amplitude * (1 + np.cos(periodicity * measured - phase))
            records.append({**term, "geometry": measured, "energy_kj": float(energy)})
        exceptions = {tuple(sorted(t["atoms"])): t["parameters"] for t in model.terms if t["kind"] == "exception"}
        for a in range(n, count):
            for b in range(a + 1, count):
                pa, pb = model.particles[a], model.particles[b]
                q, sigma, epsilon = exceptions.get((a, b), [pa["q"] * pb["q"], 0.5 * (pa["sigma_nm"] + pb["sigma_nm"]), math.sqrt(pa["epsilon_kj"] * pb["epsilon_kj"])])
                r = float(np.linalg.norm(positions[a] - positions[b]))
                if not math.isfinite(r) or r <= 0: raise ValueError("Overlapping intramolecular coordinates")
                ratio6 = (sigma / r) ** 6
                energy = self.coulomb * q / r + 4 * epsilon * (ratio6**2 - ratio6)
                records.append({"kind": "nonbonded", "atoms": [a, b], "parameters": [q, sigma, epsilon], "exception": (a, b) in exceptions, "geometry": r, "energy_kj": energy})
        totals = {kind: sum(r["energy_kj"] for r in records if r["kind"] == kind) for kind in ("bond", "angle", "torsion", "nonbonded")}
        self.agree(list(totals.values()), list(totals.values()))
        return records, totals

    def strain(self, model, prepared_positions, cancelled):
        n, count = model.nprotein, len(model.particles)
        system = model.subset(range(n, count))
        hydrogen = [p["element"] == "H" for p in model.particles[n:]]
        bound, bound_meta = Preparation.minimize(system, prepared_positions[n:], hydrogen, 200, cancelled)
        reference, reference_meta = Preparation.minimize(system, bound, [True] * len(bound), 5000, cancelled)
        bound_full, reference_full = np.vstack([prepared_positions[:n], bound]), np.vstack([prepared_positions[:n], reference])
        bound_records, bound_totals = self.internal(model, bound_full)
        reference_records, reference_totals = self.internal(model, reference_full)
        for positions, totals in [(bound, bound_totals), (reference, reference_totals)]:
            self.agree(sum(totals.values()) / 4.184, self.reference(system, positions) / 4.184)
        records = [{**a, "bound_geometry": a["geometry"], "reference_geometry": b["geometry"], "bound_kj": a["energy_kj"], "reference_kj": b["energy_kj"], "delta_kj": a["energy_kj"] - b["energy_kj"]} for a, b in zip(bound_records, reference_records)]
        delta = sum(r["delta_kj"] for r in records)
        self.agree(delta, sum(bound_totals.values()) - sum(reference_totals.values()), absolute=4.184e-6, relative=1e-10)
        if delta < -4.184e-4: raise ValueError("Local reference has higher energy than bound endpoint")
        heavy = ~np.asarray(hydrogen)
        a, b = bound[heavy], reference[heavy]
        a, b = a - a.mean(axis=0), b - b.mean(axis=0)
        u, _, vt = np.linalg.svd(a.T @ b)
        sign = np.linalg.det(u @ vt)
        rotation = u @ np.diag([1, 1, sign]) @ vt
        rmsd = float(np.sqrt(np.mean(np.sum((a @ rotation - b)**2, axis=1))))
        return {"records": records, "bound_nm": bound.tolist(), "reference_nm": reference.tolist(), "bound_components_kj": bound_totals, "reference_components_kj": reference_totals, "local_strain_kj": delta, "heavy_rmsd_angstrom": rmsd * 10, "bound_convergence": bound_meta, "reference_convergence": reference_meta, "label": "Local vacuum strain", "interpretation": "Pose-dependent local vacuum reference; not solution/global-minimum strain"}
