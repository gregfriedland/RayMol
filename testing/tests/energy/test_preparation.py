"""Targeted immutable-input/model/preparation regressions, independent of the renderer."""

import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import numpy as np
import openmm as mm
from openmm import app, unit
from pydantic import ValidationError
from rdkit import Chem

from raymol_energy.artifacts import Artifacts
from raymol_energy.prepare import Preparation
from raymol_energy.protocol import MolecularSnapshot
from raymol_energy.runtime import Runtime


class PreparationTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[3]

    @classmethod
    def fixture(cls, keep_h=True):
        fixtures = Path(__file__).resolve().parent / "fixtures"
        protein = app.PDBFile(str(fixtures / "alanine-dipeptide.pdb"))
        xyz = np.asarray(protein.positions.value_in_unit(unit.nanometer))
        atoms = list(protein.topology.atoms())
        selected = [a for a in atoms if keep_h or a.element.symbol != "H"]
        mapping = {a.index: i for i, a in enumerate(selected)}
        receptor = [{"source_id": f"protein/{a.index}", "name": a.name, "element": a.element.symbol, "residue": f"protein/{a.residue.index}", "resname": a.residue.name, "chain": a.residue.chain.id, "resi": a.residue.id, "xyz_nm": xyz[a.index].tolist()} for a in selected]
        rd = Chem.MolFromMolFile(str(fixtures / "acetanilide.sdf"), removeHs=False)
        ligand_positions = rd.GetConformer().GetPositions() / 10 + [1.5, 0, 0]
        rd.GetConformer().SetPositions(ligand_positions * 10)
        heavy = [a.GetIdx() for a in rd.GetAtoms() if a.GetSymbol() != "H"]
        ligand = [{"source_id": f"ligand/{i}", "name": f"{rd.GetAtomWithIdx(i).GetSymbol()}{i+1}", "element": rd.GetAtomWithIdx(i).GetSymbol(), "residue": "ligand/0", "resname": "LIG", "chain": "L", "resi": "1", "xyz_nm": ligand_positions[i].tolist()} for i in heavy]
        values = {"version": 1, "analysis_id": "e" * 32, "scene_revision": "f" * 64, "state": 1, "receptor": receptor, "receptor_bonds": [[mapping[a.index], mapping[b.index]] for a, b in protein.topology.bonds() if a.index in mapping and b.index in mapping], "protein_templates": {"protein/0": "ACE", "protein/1": "ALA", "protein/2": "NME"}, "ligand": ligand, "ligand_sdf": Chem.MolToMolBlock(rd), "ligand_map": heavy, "exclusions": [], "full_receptor_confirmed": True, "chemistry_confirmed": True}
        values["receptor_bond_orders"] = [1] * len(values["receptor_bonds"])
        return MolecularSnapshot.model_validate(values)

    @classmethod
    def setUpClass(cls):
        cls.cancel = threading.Event()
        cls.snapshot = cls.fixture()
        cls.preparation = Preparation(directory=cls.root / "run_261003_energy_feasibility/feature-261004")
        cls.model, cls.coordinates = cls.preparation.construct(cls.snapshot, cls.cancel)

    def test_schema_required_identity_and_finiteness(self):
        values = self.snapshot.model_dump()
        values["ligand_map"][1] = values["ligand_map"][0]
        with self.assertRaises(ValidationError): MolecularSnapshot.model_validate(values)

    def test_explicit_h_and_sdf_order_permutation(self):
        values = self.snapshot.model_dump(mode="json")
        rd = Chem.MolFromMolBlock(values["ligand_sdf"], removeHs=False)
        xyz = rd.GetConformer().GetPositions()/10
        values["ligand"] = [{"source_id": f"ligand/{i}", "name": f"{atom.GetSymbol()}{i+1}", "element": atom.GetSymbol(), "residue": "ligand/0", "resname": "LIG", "chain": "L", "resi": "1", "xyz_nm": xyz[i].tolist()} for i, atom in enumerate(rd.GetAtoms())]
        permutation = list(reversed(range(rd.GetNumAtoms())))
        values["ligand_sdf"] = Chem.MolToMolBlock(Chem.RenumberAtoms(rd, permutation))
        values["ligand_map"] = [permutation.index(i) for i in range(rd.GetNumAtoms())]
        model, coordinates = self.preparation.construct(MolecularSnapshot.model_validate(values), self.cancel)
        self.assertTrue(all(p["source_id"] is not None for p in model.particles[model.nprotein:]))
        source = {a["source_id"]: a["xyz_nm"] for a in values["ligand"]}
        for i, particle in enumerate(model.particles[model.nprotein:], model.nprotein):
            np.testing.assert_array_equal(coordinates[i], source[particle["source_id"]])
        self.assertTrue(any(b["order"] == 4 for b in model.bonds if min(b["atoms"]) >= model.nprotein))
        self.assertTrue(any(b["order"] == 2 for b in model.bonds if min(b["atoms"]) >= model.nprotein))
        for field in ("full_receptor_confirmed", "chemistry_confirmed"):
            invalid = {**values, field: 1}
            with self.assertRaises(ValidationError): MolecularSnapshot.model_validate(invalid)
        values = self.snapshot.model_dump()
        values["receptor"][0]["xyz_nm"] = [float("nan"), 0, 0]
        with self.assertRaises(ValidationError): MolecularSnapshot.model_validate(values)
        values = self.snapshot.model_dump()
        del values["protein_templates"]["protein/1"]
        with self.assertRaises(ValidationError): MolecularSnapshot.model_validate(values)

    def test_model_physical_masses_and_exact_subset_parameters(self):
        source = mm.XmlSerializer.deserialize(self.model.system_xml)
        n = self.model.nprotein
        for indices in [list(range(n)), list(range(n, len(self.model.particles))), list(range(len(self.model.particles)))]:
            for solvent in [False, True]:
                derived = self.model.subset(indices, solvent=solvent)
                self.assertEqual(derived.getNumConstraints(), 0)
                self.assertEqual(derived.getNumParticles(), len(indices))
                for local, original in enumerate(indices):
                    self.assertEqual(derived.getParticleMass(local), source.getParticleMass(original))
                    self.assertGreater(derived.getParticleMass(local).value_in_unit(unit.dalton), 0)
                for force in derived.getForces():
                    original = next(f for f in source.getForces() if type(f) is type(force))
                    if isinstance(force, (mm.NonbondedForce, mm.CustomGBForce)):
                        for local, index in enumerate(indices):
                            self.assertEqual(force.getParticleParameters(local), original.getParticleParameters(index))
        torsions = [t for t in self.model.terms if t.get("kind") == "torsion" and min(t["atoms"]) >= n]
        self.assertEqual({t["family"] for t in torsions}, {"ProperTorsions", "ImproperTorsions"})
        self.assertTrue(all(t["parameter_id"] for t in torsions))

    def test_coordinate_update_reuses_model_and_charges(self):
        from openff.toolkit.utils.nagl_wrapper import NAGLToolkitWrapper
        values = self.snapshot.model_dump(mode="json")
        for atom in values["ligand"]: atom["xyz_nm"][1] += 0.01
        with patch.object(NAGLToolkitWrapper, "assign_partial_charges", side_effect=AssertionError("Repeated inference")):
            model, coordinates = self.preparation.construct(MolecularSnapshot.model_validate(values), self.cancel)
        self.assertTrue(model is self.model, "Coordinate-only update reconstructed the shared model")
        self.assertFalse(np.array_equal(coordinates, self.coordinates))

    def test_hydrogens_converge_without_heavy_motion(self):
        before = self.model.system_xml
        coordinates, metadata = self.preparation.endpoint(self.model, self.coordinates, self.cancel)
        heavy = [p["element"] != "H" for p in self.model.particles]
        np.testing.assert_array_equal(coordinates[heavy], self.coordinates[heavy])
        self.assertEqual(before, self.model.system_xml)
        self.assertLessEqual(metadata["receptor"]["rms_force_kj_mol_nm"], 1)
        self.assertLessEqual(metadata["ligand"]["rms_force_kj_mol_nm"], 1)

    def test_generated_hydrogens_and_missing_heavy_rejection(self):
        snapshot = self.fixture(keep_h=False)
        model, coordinates = self.preparation.construct(snapshot, self.cancel)
        generated = [p for p in model.particles[:model.nprotein] if p["element"] == "H"]
        self.assertTrue(generated)
        self.assertTrue(all(p["source_id"] is None and p["parent"] is not None for p in generated))
        result, _ = self.preparation.endpoint(model, coordinates, self.cancel)
        np.testing.assert_array_equal(result[[p["element"] != "H" for p in model.particles]], coordinates[[p["element"] != "H" for p in model.particles]])
        values = snapshot.model_dump()
        removed = len(values["receptor"]) - 1
        values["receptor"].pop()
        retained = [(b, order) for b, order in zip(values["receptor_bonds"], values["receptor_bond_orders"]) if removed not in b]
        values["receptor_bonds"] = [b for b, _ in retained]
        values["receptor_bond_orders"] = [order for _, order in retained]
        with self.assertRaisesRegex(ValueError, "Missing heavy atoms"):
            self.preparation.construct(MolecularSnapshot.model_validate(values), self.cancel)

    def test_cancel_and_failed_convergence_are_not_success(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(InterruptedError): self.preparation.endpoint(self.model, self.coordinates, cancelled)
        system = mm.System()
        system.addParticle(12)
        system.addParticle(1)
        bond = mm.HarmonicBondForce()
        bond.addBond(0, 1, 0.1, 100000)
        system.addForce(bond)
        with self.assertRaisesRegex(ValueError, "did not converge"):
            self.preparation.minimize(system, np.array([[0., 0, 0], [3., 0, 0]]), [False, True], 1, self.cancel)

    def test_available_mm_cpus_and_reference_polish(self):
        with patch("raymol_energy.runtime.os.cpu_count", return_value=11), patch.dict("os.environ", OPENMM_CPU_THREADS="4"):
            self.assertEqual(Runtime.mm_threads(), 11)
        with patch("raymol_energy.runtime.os.cpu_count", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Cannot determine"):
                Runtime.mm_threads()
        system = mm.System()
        for _ in range(101): system.addParticle(12)
        bond = mm.HarmonicBondForce()
        bond.addBond(0, 1, 0.1, 10000)
        system.addForce(bond)
        xyz = np.zeros((101, 3))
        xyz[:, 0] = np.arange(101)
        xyz[1, 0] = 0.3
        active = np.zeros(101, dtype=bool)
        active[1] = True
        actual = []
        native = mm.LocalEnergyMinimizer.minimize
        def observe(context, tolerance, iterations, reporter):
            platform = context.getPlatform()
            actual.append(platform.getName())
            if platform.getName() == "CPU":
                self.assertEqual(platform.getPropertyValue(context, "Threads"), str(Runtime.mm_threads()))
                self.assertEqual(platform.getPropertyValue(context, "DeterministicForces"), "true")
            return native(context, tolerance, iterations, reporter)
        with patch.object(mm.LocalEnergyMinimizer, "minimize", side_effect=observe):
            result, metadata = self.preparation.minimize(system, xyz, active, 30, self.cancel)
        self.assertEqual(actual, ["CPU", "Reference"])
        self.assertEqual(metadata["stages"][0]["threads"], Runtime.mm_threads())
        self.assertEqual(metadata["stages"][1]["threads"], 1)
        self.assertEqual(metadata["total_iterations_limit"], 60)
        self.assertLessEqual(metadata["rms_force_kj_mol_nm"], 1)
        np.testing.assert_array_equal(result[~active], xyz[~active])
        self.assertTrue(all(system.getParticleMass(i).value_in_unit(unit.dalton) == 12 for i in range(101)))
        cancelled = threading.Event()
        def cancel_after_cpu(context, tolerance, iterations, reporter):
            self.assertEqual(context.getPlatform().getName(), "CPU")
            cancelled.set()
        with patch.object(mm.LocalEnergyMinimizer, "minimize", side_effect=cancel_after_cpu):
            with self.assertRaises(InterruptedError):
                self.preparation.minimize(system, xyz, active, 30, cancelled)

    def test_artifact_integrity_and_unsafe_paths(self):
        store = Artifacts(root=self.preparation.directory / "test-artifacts")
        with self.assertRaises(ValueError): store.path("../escape")
        descriptor = store.array("pair.npy", np.array([[1., -2., 3.]]))
        np.testing.assert_array_equal(store.read_array(descriptor), [[1, -2, 3]])
        descriptor["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash mismatch"): store.read_array(descriptor)
