"""Receptor preparation, with optional local-only private-structure regressions."""

import json
import threading
import unittest
from unittest.mock import patch

import numpy as np
from openmm import app, unit

from raymol_energy.protocol import MolecularSnapshot
from raymol_energy.protonation import ReceptorProtonation
import test_preparation


class ProtonationTests(unittest.TestCase):
    def test_private_fixture_absence_is_an_explicit_skip(self):
        with patch("pathlib.Path.is_file", return_value=False):
            with self.assertRaisesRegex(unittest.SkipTest, "Private local regression fixture"):
                self.fixture_eight()

    def test_capped_receptor_preserves_all_deposited_atoms_and_records_method(self):
        source = test_preparation.PreparationTests.fixture()
        snapshot = source.model_copy(update={"preparation_recipe": "openmm-ph-v1", "preparation_ph": 7.0})
        original = snapshot.model_dump()
        derived, hydrogens, provenance = ReceptorProtonation(snapshot=snapshot).prepare(threading.Event())
        self.assertEqual(snapshot.model_dump(), original)
        self.assertEqual(derived.protein_templates, source.protein_templates)
        self.assertEqual(provenance["pH"], 7)
        self.assertIn("OpenMM", provenance["method"])
        self.assertTrue(hydrogens)
        preparation = test_preparation.Preparation(directory=test_preparation.PreparationTests.root / "run_261003_energy_feasibility/feature-261004")
        model, coordinates = preparation.construct(snapshot, threading.Event())
        source_coordinates = {a.source_id: a.xyz_nm for a in snapshot.receptor}
        for index, particle in enumerate(model.particles[:model.nprotein]):
            if particle["source_id"] is not None: np.testing.assert_array_equal(coordinates[index], source_coordinates[particle["source_id"]])

    @staticmethod
    def fixture_eight():
        path = test_preparation.PreparationTests.root / "run_261003_energy_feasibility/feature-261004/pse-chemistry-261005/user-inputs.json"
        if not path.is_file():
            raise unittest.SkipTest("Private local regression fixture is not distributed with the source")
        values = json.loads(path.read_text())
        values.pop("smiles")
        values.pop("ligand_bonds")
        # Receptor preparation does not need ligand parameterization.
        ligand = test_preparation.PreparationTests.fixture()
        values.update(ligand_sdf=ligand.ligand_sdf, ligand_map=ligand.ligand_map, ligand=[a.model_dump() for a in ligand.ligand])
        unmodified = MolecularSnapshot.model_validate(values)
        # Explicitly derived TEST fixture: assumes ALA287 is a biological C-terminus.
        # This does not repair or score the user's unmodified scene.
        indices = {a["name"]: i for i, a in enumerate(values["receptor"]) if a["resname"] == "ALA" and a["resi"] == "287"}
        carbon = np.asarray(values["receptor"][indices["C"]]["xyz_nm"])
        oxygen = np.asarray(values["receptor"][indices["O"]]["xyz_nm"])-carbon
        alpha = np.asarray(values["receptor"][indices["CA"]]["xyz_nm"])-carbon
        alpha /= np.linalg.norm(alpha)
        direction = 2*np.dot(oxygen, alpha)*alpha-oxygen
        direction /= np.linalg.norm(direction)
        added = {**values["receptor"][indices["O"]], "source_id": "test-derived/OXT", "name": "OXT", "xyz_nm": (carbon+0.125*direction).tolist()}
        values["receptor_bonds"].append([indices["C"], len(values["receptor"])])
        values["receptor_bond_orders"].append(1)
        values["receptor"].append(added)
        # The PSE also contains distance-inferred cross-residue O bonds. Use
        # OpenMM's standard PDB graph ONLY in this labelled derived test fixture.
        app.PDBFile._loadNameReplacementTables()
        topology = app.Topology()
        chain = topology.addChain("A")
        residues = {}
        for atom in values["receptor"]:
            if atom["residue"] not in residues:
                residues[atom["residue"]] = topology.addResidue(atom["resname"], chain, atom["resi"])
            replacements = app.PDBFile._atomNameReplacements.get(atom["resname"], {})
            topology.addAtom(replacements.get(atom["name"], atom["name"]), app.element.get_by_symbol(atom["element"]), residues[atom["residue"]])
        topology.createStandardBonds()
        topology.createDisulfideBonds(np.asarray([a["xyz_nm"] for a in values["receptor"]])*unit.nanometer)
        values["receptor_bonds"] = [[a.index, b.index] for a, b in topology.bonds()]
        values["receptor_bond_orders"] = [1]*len(values["receptor_bonds"])
        snapshot = MolecularSnapshot.model_validate(values)
        return path, unmodified, snapshot

    def test_actual_eight_histidines_are_assigned_and_heavy_pose_is_unchanged(self):
        path, unmodified, snapshot = self.fixture_eight()
        with self.assertRaisesRegex(ValueError, "ALA287.*OXT"):
            ReceptorProtonation(snapshot=unmodified).prepare(threading.Event())
        original = snapshot.model_dump()
        derived, hydrogens, provenance = ReceptorProtonation(snapshot=snapshot).prepare(threading.Event())
        self.assertEqual(snapshot.model_dump(), original)
        np.testing.assert_array_equal([a.xyz_nm for a in derived.receptor], [a.xyz_nm for a in snapshot.receptor])
        histidines = {a.residue for a in snapshot.receptor if a.resname == "HIS"}
        self.assertEqual(len(histidines), 8)
        rows = {row["residue"]: row for row in provenance["assignments"]}
        for identity in histidines:
            self.assertIn(derived.protein_templates[identity], {"HID", "HIE", "HIP"})
            self.assertIn(rows[identity]["origin"], {"OpenMM pH estimate", "deposited polar hydrogens"})
        ff = app.ForceField("amber14/protein.ff14SB.xml")
        for identity, template in derived.protein_templates.items():
            charge = sum(a.parameters["charge"] for a in ff._templates[template].atoms)
            self.assertTrue(np.isfinite(charge))
            self.assertLessEqual(abs(charge-round(charge)), 1e-6)
            self.assertTrue(any(key[0] == identity for key in hydrogens))
        report = path.with_name("eight-his-preparation-test.json")
        report.write_text(json.dumps({"pH": provenance["pH"], "histidines": [rows[key] for key in sorted(histidines)], "source_atoms": len(unmodified.receptor), "heavy_pose_unchanged": True, "derived_test_fixture": "Test-only geometric OXT (assumed biological terminus) and OpenMM standard PDB bond graph; original PSE unmodified", "unmodified_scene": "Rejected: missing terminal OXT; no score"}, indent=2)+"\n")

    def test_ph_estimate_and_explicit_variants_are_not_a_blanket_histidine_assignment(self):
        _, _, snapshot = self.fixture_eight()
        values = snapshot.model_dump()
        retained = [i for i, atom in enumerate(values["receptor"]) if not (atom["resname"] == "HIS" and atom["element"] == "H")]
        mapping = {old: new for new, old in enumerate(retained)}
        values["receptor"] = [values["receptor"][i] for i in retained]
        bonds = [(edge, order) for edge, order in zip(values["receptor_bonds"], values["receptor_bond_orders"]) if all(i in mapping for i in edge)]
        values["receptor_bonds"] = [[mapping[i] for i in edge] for edge, _ in bonds]
        values["receptor_bond_orders"] = [order for _, order in bonds]
        values["preparation_ph"] = 5.0
        histidines = sorted({a["residue"] for a in values["receptor"] if a["resname"] == "HIS"})
        derived, _, provenance = ReceptorProtonation(snapshot=MolecularSnapshot.model_validate(values)).prepare(threading.Event())
        self.assertTrue(all(derived.protein_templates[identity] == "HIP" for identity in histidines))
        self.assertEqual(provenance["pH"], 5)
        values["protein_overrides"] = {histidines[0]: "HID"}
        for atom in values["receptor"]:
            if atom["residue"] == histidines[1]: atom["resname"] = "HIE"
        derived, _, provenance = ReceptorProtonation(snapshot=MolecularSnapshot.model_validate(values)).prepare(threading.Event())
        self.assertEqual(derived.protein_templates[histidines[0]], "HID")
        self.assertEqual(derived.protein_templates[histidines[1]], "HIE")
        rows = {row["residue"]: row for row in provenance["assignments"]}
        self.assertEqual(rows[histidines[0]]["origin"], "override")
        self.assertEqual(rows[histidines[1]]["origin"], "explicit residue name")

    def test_override_cannot_remove_deposited_histidine_hydrogens(self):
        _, _, snapshot = self.fixture_eight()
        residue = next(a.residue for a in snapshot.receptor if a.resname == "HIS" and a.name == "HD1")
        wrong = snapshot.model_copy(update={"protein_overrides": {residue: "HIE"}})
        with self.assertRaisesRegex(ValueError, "conflicts with deposited hydrogens"):
            ReceptorProtonation(snapshot=wrong).prepare(threading.Event())

    def test_missing_heavy_atoms_and_incompatible_override_fail_clearly(self):
        snapshot = test_preparation.PreparationTests.fixture(keep_h=False).model_copy(update={"preparation_recipe": "openmm-ph-v1", "preparation_ph": 7.0})
        values = snapshot.model_dump()
        removed = len(values["receptor"])-1
        values["receptor"].pop()
        keep = [(edge, order) for edge, order in zip(values["receptor_bonds"], values["receptor_bond_orders"]) if removed not in edge]
        values["receptor_bonds"] = [edge for edge, _ in keep]
        values["receptor_bond_orders"] = [order for _, order in keep]
        with self.assertRaisesRegex(ValueError, "Heavy atoms do not match"):
            ReceptorProtonation(snapshot=MolecularSnapshot.model_validate(values)).prepare(threading.Event())
        wrong = snapshot.model_copy(update={"protein_overrides": {"protein/1": "HID"}})
        with self.assertRaisesRegex(ValueError, "Heavy atoms do not match"):
            ReceptorProtonation(snapshot=wrong).prepare(threading.Event())


if __name__ == "__main__": unittest.main()
