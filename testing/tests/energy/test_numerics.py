"""Independent OpenMM comparisons for the four molecular diagnostics."""

import threading
import unittest
from unittest.mock import patch

import numpy as np
import openmm as mm
from openmm import unit

from raymol_energy.score import Score
from raymol_energy.solvation import Solvation
import test_preparation as fixtures


class NumericalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PreparationTests.setUpClass()
        cls.prepare = fixtures.PreparationTests.preparation
        cls.model = fixtures.PreparationTests.model
        cls.cancel = threading.Event()
        cls.positions, cls.endpoint = cls.prepare.endpoint(cls.model, fixtures.PreparationTests.coordinates, cls.cancel)
        cls.score, cls.solvent = Score(), Solvation()

    def test_direct_components_cross_difference_and_bonded_inventory(self):
        model, positions = self.model, self.positions
        arrays, totals = self.score.direct(model, positions, self.cancel)
        references = {}
        for channel in ("coulomb", "lj"):
            values = {}
            for name, indices in self.solvent.environments(model).items():
                vacuum = model.subset(indices)
                force = next(f for f in vacuum.getForces() if isinstance(f, mm.NonbondedForce))
                for i in range(force.getNumParticles()):
                    q, sigma, epsilon = force.getParticleParameters(i)
                    force.setParticleParameters(i, q if channel == "coulomb" else 0, sigma, epsilon if channel == "lj" else 0)
                for i in range(force.getNumExceptions()):
                    a, b, q, sigma, epsilon = force.getExceptionParameters(i)
                    force.setExceptionParameters(i, a, b, q if channel == "coulomb" else 0, sigma, epsilon if channel == "lj" else 0)
                system = mm.System()
                for i in indices: system.addParticle(model.particles[i]["mass_da"])
                system.addForce(mm.XmlSerializer.deserialize(mm.XmlSerializer.serialize(force)))
                values[name] = self.score.reference(system, positions[indices])
            references[channel] = values["PL"] - values["P"] - values["L"]
        self.score.agree(totals["coulomb"]/4.184, references["coulomb"]/4.184)
        self.score.agree(totals["net_lj"]/4.184, references["lj"]/4.184)
        # Full energy = P internal + L internal + all P-by-L direct pairs.
        full = self.score.reference(model.subset(range(len(model.particles))), positions)
        partner_sum = sum(self.score.reference(model.subset(indices), positions[indices]) for name, indices in self.solvent.environments(model).items() if name != "PL")
        self.score.agree(full/4.184, (partner_sum + arrays.sum())/4.184)
        _, internal = self.score.internal(model, positions)
        ligand = model.subset(range(model.nprotein, len(model.particles)))
        for kind, force_type in [("bond", mm.HarmonicBondForce), ("angle", mm.HarmonicAngleForce), ("torsion", mm.PeriodicTorsionForce), ("nonbonded", mm.NonbondedForce)]:
            force = next(f for f in ligand.getForces() if isinstance(f, force_type))
            selected = mm.System()
            for i in range(ligand.getNumParticles()): selected.addParticle(ligand.getParticleMass(i))
            selected.addForce(mm.XmlSerializer.deserialize(mm.XmlSerializer.serialize(force)))
            self.score.agree(internal[kind]/4.184, self.score.reference(selected, positions[model.nprotein:])/4.184)

    def test_direct_tiling_rigid_transform_and_residue_reductions(self):
        a, _ = self.score.direct(self.model, self.positions, self.cancel, tile=3)
        rotation = np.array([[0., -1, 0], [1, 0, 0], [0, 0, 1]])
        b, _ = self.score.direct(self.model, self.positions @ rotation + [3, -2, 1], self.cancel, tile=128)
        np.testing.assert_allclose(a, b, atol=4.184e-6, rtol=1e-10)
        residues = {p["residue"] for p in self.model.particles[:self.model.nprotein]}
        sums = [a[[p["residue"] == r for p in self.model.particles[:self.model.nprotein]]].sum(axis=(0, 1)) for r in residues]
        np.testing.assert_allclose(sum(sums), a.sum(axis=(0, 1)), atol=4.184e-6, rtol=1e-10)

    def test_strain_both_endpoints_and_term_deltas(self):
        result = self.score.strain(self.model, self.positions, self.cancel)
        self.assertGreaterEqual(result["local_strain_kj"], -4.184e-4)
        self.score.agree(sum(r["delta_kj"] for r in result["records"]), result["local_strain_kj"], absolute=4.184e-6, relative=1e-10)
        np.testing.assert_array_equal(self.positions, self.prepare.endpoint(self.model, fixtures.PreparationTests.coordinates, self.cancel)[0])
        self.assertTrue(any(r["delta_kj"] < 0 for r in result["records"]))
        self.assertTrue(result["reference_convergence"]["converged"])
        self.assertEqual(result["reference_convergence"]["iterations_limit"], 5000)

    def test_solvation_raw_channels_difference_and_half_pair_masks(self):
        totals = self.solvent.global_totals(self.model, self.positions, self.cancel)
        atom, heavy, _ = self.solvent.local(self.model, self.positions, totals, self.cancel, tile=7)
        np.testing.assert_allclose(atom.sum(axis=0), heavy.sum(axis=0), atol=4.184e-6, rtol=1e-10)
        for name, indices in self.solvent.environments(self.model).items():
            allocations = self.solvent.allocate_environment([self.model.particles[i]["gb"] for i in indices], self.positions[indices], self.cancel, tile=3)
            other = self.solvent.allocate_environment([self.model.particles[i]["gb"] for i in indices], self.positions[indices], self.cancel, tile=128)
            np.testing.assert_allclose(allocations, other, atol=4.184e-6, rtol=1e-10)
            system = self.model.subset(indices, solvent=True)
            for channel_index, channel in enumerate(("polar", "ace")):
                for chosen in ([0], list(range(0, len(indices), 3))):
                    weights = np.zeros(len(indices))
                    weights[chosen] = 1
                    selected = mm.System()
                    for i in indices: selected.addParticle(self.model.particles[i]["mass_da"])
                    selected.addForce(self.solvent.reference_force(system.getForce(0), channel, weights))
                    reference = self.score.reference(selected, self.positions[indices])
                    self.score.agree(allocations[chosen, channel_index].sum()/4.184, reference/4.184, absolute=1e-4, relative=1e-7)
        bad = {name: dict(channels) for name, channels in totals.items()}
        bad["PL"]["polar"] += 100
        with self.assertRaisesRegex(ValueError, "Numerical disagreement"):
            self.solvent.local(self.model, self.positions, bad, self.cancel)

    def test_overlaps_cancellation_and_favorable_packing_not_clash(self):
        xyz = self.positions.copy()
        xyz[self.model.nprotein] = xyz[0]
        with self.assertRaises(ValueError): self.score.direct(self.model, xyz, self.cancel)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(InterruptedError): self.solvent.global_totals(self.model, self.positions, cancelled)
        values, _ = self.score.direct(self.model, self.positions, self.cancel)
        self.assertTrue(((values[:, :, 2] > 0) & (values[:, :, 1] + values[:, :, 2] < 0)).any())

    def test_parallel_solvent_matches_serial_and_propagates_cancel_and_failure(self):
        with patch("raymol_energy.solvation.Runtime.mm_threads", return_value=1):
            serial = self.solvent.global_totals(self.model, self.positions, self.cancel)
        with patch("raymol_energy.solvation.Runtime.mm_threads", return_value=11):
            parallel = self.solvent.global_totals(self.model, self.positions, self.cancel)
        self.assertEqual(parallel, serial)
        with patch.object(Score, "reference", side_effect=ValueError("Injected energy failure")):
            with self.assertRaisesRegex(ValueError, "Injected energy failure"):
                self.solvent.global_totals(self.model, self.positions, self.cancel)
        cancelled = threading.Event()
        native = Score.reference
        def cancel_during_evaluation(system, positions):
            value = native(system, positions)
            cancelled.set()
            return value
        with patch.object(Score, "reference", side_effect=cancel_during_evaluation):
            with self.assertRaises(InterruptedError):
                self.solvent.global_totals(self.model, self.positions, cancelled)
