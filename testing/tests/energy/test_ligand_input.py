"""SMILES chemistry mapping, stereochemistry and immutable scene pose regressions."""

import copy
import threading
import unittest

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem

from raymol_energy.ligand_input import LigandInput
from raymol_energy.protocol import Message
from raymol_energy.runtime import Runtime
from raymol_energy.protocol import MolecularSnapshot
from raymol_energy.prepare import Preparation
import test_preparation as fixtures


class LigandInputTests(unittest.TestCase):
    @staticmethod
    def fixture(smiles="CC(=O)Nc1ccccc1", hydrogens=True):
        mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
        assert AllChem.EmbedMolecule(mol, randomSeed=1234) == 0
        if not hydrogens: mol = Chem.RemoveHs(mol)
        mol = Chem.RenumberAtoms(mol, list(reversed(range(mol.GetNumAtoms()))))
        xyz = mol.GetConformer().GetPositions()/10
        return {"smiles": smiles, "atoms": [{"source_id": f"scene/{i}", "name": f"{atom.GetSymbol()}{i}", "element": atom.GetSymbol(), "residue": "ligand", "resname": "LIG", "chain": "L", "resi": "1", "xyz_nm": xyz[i].tolist()} for i, atom in enumerate(mol.GetAtoms())],
                "bonds": [[bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()] for bond in mol.GetBonds()]}

    def test_reordered_symmetric_ligand_with_or_without_h_preserves_pose_and_chemistry(self):
        for hydrogens in (True, False):
            with self.subTest(hydrogens=hydrogens):
                payload = self.fixture(hydrogens=hydrogens)
                original = copy.deepcopy(payload)
                result = Runtime.execute("ligand_input", payload, threading.Event())
                self.assertEqual(payload, original)
                mol = Chem.MolFromMolBlock(result["sdf"], removeHs=False)
                self.assertEqual(Chem.MolToSmiles(Chem.RemoveHs(mol)), "CC(=O)Nc1ccccc1")
                self.assertEqual(len(result["mapping"]), len(payload["atoms"]))
                self.assertEqual(len(set(result["mapping"])), len(payload["atoms"]))
                for atom, target in zip(payload["atoms"], result["mapping"]):
                    self.assertEqual(mol.GetAtomWithIdx(target).GetSymbol(), atom["element"])
                    np.testing.assert_allclose(mol.GetConformer().GetAtomPosition(target), np.array(atom["xyz_nm"])*10, atol=6e-7, rtol=0)
                Message(generation=1, request=1, kind="run", operation="ligand_input", payload=payload)

    def test_specified_tetrahedral_and_double_bond_stereo_and_reflected_pose(self):
        for smiles in ("C[C@H](O)F", "F/C=C/F"):
            payload = self.fixture(smiles, hydrogens=False)
            result = LigandInput.model_validate(payload).prepare(threading.Event())
            self.assertEqual(result["smiles"], Chem.MolToSmiles(Chem.MolFromSmiles(smiles)))
        payload = self.fixture("C[C@H](O)F", hydrogens=False)
        for atom in payload["atoms"]: atom["xyz_nm"][0] *= -1
        with self.assertRaisesRegex(ValueError, "pose stereochemistry"):
            LigandInput.model_validate(payload).prepare(threading.Event())

    def test_invalid_incomplete_ambiguous_and_wrong_connectivity_are_rejected(self):
        for smiles, error in [("invalid", "Invalid ligand SMILES"), ("CCO", "heavy-atom"),
                              ("CC(O)C(=O)O", "specify all ligand stereochemistry"), ("CC.[Cl-]", "one connected"),
                              ("[13CH4]", "Isotopic")]:
            payload = self.fixture()
            payload["smiles"] = smiles
            with self.subTest(smiles=smiles), self.assertRaisesRegex(ValueError, error):
                LigandInput.model_validate(payload).prepare(threading.Event())
        payload = self.fixture(hydrogens=False)
        payload["bonds"].pop()
        with self.assertRaisesRegex(ValueError, "connectivity"):
            LigandInput.model_validate(payload).prepare(threading.Event())
        payload = self.fixture("CC(=O)[O-]", hydrogens=False)
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            LigandInput.model_validate(payload).prepare(threading.Event())

    def test_partial_hydrogens_supported_but_wrong_protonation_and_cancel_fail(self):
        payload = self.fixture("C[NH3+]")
        payload["smiles"] = "CN"
        with self.assertRaisesRegex(ValueError, "hydrogen count"):
            LigandInput.model_validate(payload).prepare(threading.Event())
        payload = self.fixture()
        drop = next(i for i, atom in enumerate(payload["atoms"]) if atom["element"] == "H")
        payload["atoms"].pop(drop)
        payload["bonds"] = [[a-(a > drop), b-(b > drop)] for a, b in payload["bonds"] if drop not in (a, b)]
        result = LigandInput.model_validate(payload).prepare(threading.Event())
        self.assertEqual(len(result["mapping"]), len(payload["atoms"]))
        cancelled = threading.Event(); cancelled.set()
        with self.assertRaises(InterruptedError): LigandInput.model_validate(payload).prepare(cancelled)

    def test_pose_changed_after_review_cannot_bypass_stereo_check_or_parameter_cache(self):
        payload = self.fixture("C[C@H](O)F", hydrogens=False)
        result = LigandInput.model_validate(payload).prepare(threading.Event())
        values = fixtures.PreparationTests.fixture().model_dump(mode="json")
        values.update(ligand=payload["atoms"], ligand_sdf=result["sdf"], ligand_map=result["mapping"])
        for atom in values["ligand"]: atom["xyz_nm"][0] *= -1
        preparation = Preparation(directory=fixtures.PreparationTests.root / "run_261003_energy_feasibility/feature-261004")
        with self.assertRaisesRegex(ValueError, "Current ligand heavy pose disagrees"):
            preparation.ligand(MolecularSnapshot.model_validate(values), threading.Event())
