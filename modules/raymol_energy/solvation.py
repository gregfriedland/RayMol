"""Pinned OBC2+ACE totals and tiled self/ACE/half-pair bookkeeping."""

from concurrent.futures import ThreadPoolExecutor
import numpy as np
import openmm as mm

from .protocol import BaseModelNoExtra
from .prepare import Preparation
from .score import Score
from .runtime import Runtime


class Solvation(BaseModelNoExtra):
    @staticmethod
    def reference_force(force, channel="total", weights=None):
        result = mm.CustomGBForce()
        for i in range(force.getNumPerParticleParameters()): result.addPerParticleParameter(force.getPerParticleParameterName(i))
        if weights is not None: result.addPerParticleParameter("w")
        for i in range(force.getNumGlobalParameters()): result.addGlobalParameter(force.getGlobalParameterName(i), force.getGlobalParameterDefaultValue(i))
        for i in range(force.getNumComputedValues()): result.addComputedValue(*force.getComputedValueParameters(i))
        for i in range(force.getNumEnergyTerms()):
            if (channel == "polar" and i == 1) or (channel == "ace" and i != 1): continue
            expression, kind = force.getEnergyTermParameters(i)
            if weights is not None:
                body, *definitions = expression.split(";")
                mask = "w" if kind == mm.CustomGBForce.SingleParticle else "(w1+w2)/2"
                expression = f"({body})*{mask};" + ";".join(definitions)
            result.addEnergyTerm(expression, kind)
        for i in range(force.getNumParticles()):
            result.addParticle(list(force.getParticleParameters(i)) + ([] if weights is None else [float(weights[i])]))
        return result

    @staticmethod
    def environments(model):
        return {"PL": np.arange(len(model.particles)), "P": np.arange(model.nprotein), "L": np.arange(model.nprotein, len(model.particles))}

    def global_totals(self, model, positions, cancelled):
        result, jobs = {}, []
        for name, indices in self.environments(model).items():
            system = model.subset(indices, solvent=True)
            force = system.getForce(0)
            for channel in ("polar", "ace", "total"):
                Preparation.check_cancel(cancelled)
                selected = mm.System()
                for i in indices: selected.addParticle(model.particles[i]["mass_da"])
                selected.addForce(self.reference_force(force, channel))
                jobs.append((name, channel, selected, positions[indices]))
        # Each independent Reference context has one evaluating thread. Keep
        # CPU minimization outside this pool to avoid nested native parallelism.
        with ThreadPoolExecutor(max_workers=min(Runtime.mm_threads(), len(jobs))) as pool:
            futures = [pool.submit(self.evaluate_channel, system, xyz, cancelled) for _, _, system, xyz in jobs]
            try:
                for (name, channel, _, _), future in zip(jobs, futures):
                    Preparation.check_cancel(cancelled)
                    result.setdefault(name, {})[channel] = future.result()
            finally:
                for future in futures: future.cancel()
        Preparation.check_cancel(cancelled)
        for channels in result.values():
            Score.agree(channels["polar"] + channels["ace"], channels["total"], absolute=4.184e-6, relative=1e-10)
        result["difference"] = {channel: result["PL"][channel] - result["P"][channel] - result["L"][channel] for channel in ("polar", "ace", "total")}
        Score.agree(result["difference"]["polar"] + result["difference"]["ace"], result["difference"]["total"], absolute=4.184e-6, relative=1e-10)
        return result

    @staticmethod
    def evaluate_channel(system, positions, cancelled):
        Preparation.check_cancel(cancelled)
        value = Score.reference(system, positions)
        Preparation.check_cancel(cancelled)
        return value

    def allocate_environment(self, parameters, positions, cancelled, tile=128):
        q, offset_radius, screen_radius = np.asarray(parameters).T
        count = len(q)
        integral = np.empty(count)
        for start in range(0, count, tile):
            Preparation.check_cancel(cancelled)
            stop = min(start + tile, count)
            r = np.linalg.norm(positions[start:stop, None] - positions[None, :], axis=2)
            self_mask = np.arange(start, stop)[:, None] == np.arange(count)[None, :]
            if np.any((r <= 0) & ~self_mask) or not np.isfinite(r).all(): raise ValueError("Overlapping/non-finite OBC2 coordinates")
            r[self_mask] = 1  # Excluded self entries; never soften a physical pair.
            upper = r + screen_radius[None, :]
            lower = np.maximum(offset_radius[start:stop, None], np.abs(r - screen_radius[None, :]))
            value = 0.5 * (1/lower - 1/upper + 0.25 * (r - screen_radius[None, :]**2/r) * (1/upper**2 - 1/lower**2) + 0.5 * np.log(lower/upper)/r)
            active = (r + screen_radius[None, :] >= offset_radius[start:stop, None]) & ~self_mask
            integral[start:stop] = np.where(active, value, 0).sum(axis=1)
        radius = offset_radius + 0.009
        psi = integral * offset_radius
        born = 1 / (1/offset_radius - np.tanh(psi - 0.8*psi**2 + 4.85*psi**3) / radius)
        if not np.isfinite(born).all() or np.any(born <= 0): raise ValueError("Invalid OBC2 Born radii")
        coefficient = 138.935485 * (1 - 1/78.5)
        polar = -0.5 * coefficient * q**2 / born
        ace = 28.3919551 * (radius + 0.14)**2 * (radius/born)**6
        for start in range(0, count, tile):
            Preparation.check_cancel(cancelled)
            stop = min(start + tile, count)
            r2 = np.sum((positions[start:stop, None] - positions[None, :])**2, axis=2)
            product = born[start:stop, None] * born[None, :]
            denominator = np.sqrt(r2 + product * np.exp(-r2/(4*product)))
            pairs = -0.5 * coefficient * q[start:stop, None] * q[None, :] / denominator
            pairs[np.arange(start, stop)[:, None] == np.arange(count)[None, :]] = 0
            polar[start:stop] += pairs.sum(axis=1)
        values = np.column_stack([polar, ace])
        if not np.isfinite(values).all(): raise ValueError("Non-finite OBC2 allocation")
        return values

    def local(self, model, positions, global_totals, cancelled, tile=128):
        environments = {}
        for name, indices in self.environments(model).items():
            values = self.allocate_environment([model.particles[i]["gb"] for i in indices], positions[indices], cancelled, tile)
            sums = [values[:, 0].sum(), values[:, 1].sum(), values.sum()]
            references = [global_totals[name][channel] for channel in ("polar", "ace", "total")]
            Score.agree(np.array(sums)/4.184, np.array(references)/4.184, absolute=1e-3, relative=1e-7)
            environments[name] = values
        isolated = np.vstack([environments["P"], environments["L"]])
        difference = environments["PL"] - isolated
        sums = np.array([difference[:, 0].sum(), difference[:, 1].sum(), difference.sum()])
        references = np.array([global_totals["difference"][channel] for channel in ("polar", "ace", "total")])
        Score.agree(sums/4.184, references/4.184, absolute=1e-3, relative=0)
        heavy = difference.copy()
        for i, particle in enumerate(model.particles):
            if particle["element"] == "H":
                parent = particle["parent"]
                if parent is None or model.particles[parent]["element"] == "H": raise ValueError("Missing heavy H allocation parent")
                heavy[parent] += difference[i]
                heavy[i] = 0
        Score.agree(heavy.sum(axis=0), difference.sum(axis=0), absolute=4.184e-6, relative=1e-10)
        return difference, heavy, {"convention": "self + ACE + symmetric half-pair; H aggregated after energy evaluation", "nonunique_atomic_allocation": True, "units": "kJ/mol", "columns": ["polar", "ACE"], "totals": global_totals}
