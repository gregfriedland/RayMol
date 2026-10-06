"""Renderer-independent tests of partial-result interruption semantics."""

import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
import uuid
from unittest.mock import patch


class ViewStateTests(unittest.TestCase):
    def setUp(self):
        cmd = types.ModuleType("pymol.cmd")
        cmd._cmd = types.SimpleNamespace()
        cmd._pymol = types.SimpleNamespace(session=types.SimpleNamespace())
        pymol = types.ModuleType("pymol")
        pymol.cmd = cmd
        pymol.cgo = types.ModuleType("pymol.cgo")
        source = Path(__file__).resolve().parents[3] / "modules/pymol/energy_view.py"
        spec = importlib.util.spec_from_file_location("energy_view_unit", source)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"pymol": pymol, "pymol.cmd": cmd}):
            spec.loader.exec_module(module)
        self.view = module.EnergyView
        self.analysis = {"removed": False, "accepts_events": True, "model": {"accepted": True},
                         "display": {"threshold": 0.1}, "stages": {
                             "prepared": {"status": "ready", "summary": {"particles": 41}},
                             "direct": {"status": "ready", "summary": {"coulomb": -2}},
                             "global_solvation": {"status": "failed", "message": "Numerical disagreement"},
                             "local_solvation": {"status": "pending", "artifacts": {"old": True}}}}
        self.view.analyses = {"analysis": self.analysis}

    def test_input_options_include_disabled_molecules_and_nonempty_named_selections_only(self):
        cmd = self.view.input.__globals__["cmd"]
        objects = ["protein", "ligand", "hidden_ligand", "measurement", "energy_group", "energy_dots", "empty"]
        kinds = {name: "object:molecule" for name in objects}
        kinds.update(measurement="object:measurement", energy_group="object:group", energy_dots="object:cgo")
        counts = {"protein": 20, "ligand": 10, "hidden_ligand": 10, "empty": 0,
                  "pocket": 5, "empty_selection": 0}
        with patch.object(cmd, "get_names", side_effect=lambda kind: objects if kind == "public_objects" else ["pocket", "empty_selection"], create=True), \
             patch.object(cmd, "get_type", side_effect=kinds.__getitem__, create=True), \
             patch.object(cmd, "count_atoms", side_effect=lambda selection: counts[selection[1:]], create=True):
            self.assertEqual(self.view.input_options(), {"molecules": ["protein", "ligand", "hidden_ligand"], "selections": ["pocket"], "session_epoch": [self.view.epoch]})
            objects.remove("ligand")
            self.assertNotIn("ligand", self.view.input_options()["molecules"])

    def test_interrupt_preserves_valid_and_failed_results_and_rejects_late_events(self):
        original = copy.deepcopy(self.analysis["stages"])
        with patch.object(self.view, "render") as render:
            states = self.view.interrupt("analysis", self.view.epoch, "Cancelled")
        render.assert_called_once_with("analysis", threshold=0.1)
        for stage in ("prepared", "direct", "global_solvation"):
            self.assertEqual(self.analysis["stages"][stage], original[stage])
        for stage in ("local_solvation", "strain"):
            self.assertEqual(states[stage], "unavailable")
            self.assertEqual(self.analysis["stages"][stage]["artifacts"], {})
            self.assertEqual(self.analysis["stages"][stage]["summary"], {})
        self.assertFalse(self.analysis["accepts_events"])
        with patch.object(self.view, "health"):
            with self.assertRaisesRegex(ValueError, "pre-recall"):
                self.view.accept({"analysis_id": "analysis"})

    def test_old_epoch_and_removed_analysis_cannot_interrupt_current_results(self):
        original = copy.deepcopy(self.analysis)
        self.assertFalse(self.view.interrupt("analysis", "old epoch", "Cancelled"))
        self.assertEqual(self.analysis, original)
        self.analysis["removed"] = True
        original = copy.deepcopy(self.analysis)
        self.assertFalse(self.view.interrupt("analysis", self.view.epoch, "Cancelled"))
        self.assertEqual(self.analysis, original)

    def test_cancel_before_preparation_never_renders_an_unaccepted_model(self):
        self.analysis.pop("model")
        self.analysis["stages"] = {}
        with patch.object(self.view, "render") as render:
            states = self.view.interrupt("analysis", self.view.epoch, "Cancelled")
        render.assert_not_called()
        self.assertEqual(len(states), 5)
        self.assertEqual(set(states.values()), {"unavailable"})

    def test_failed_preparation_is_recorded_without_rendering_an_unaccepted_model(self):
        self.analysis.pop("model")
        self.analysis["review"] = {"scene_revision": "scene", "snapshot": {"sha256": "input"}}
        event = {"analysis_id": "analysis", "scene_revision": "scene", "input_hash": "input",
                 "stage": "prepared", "status": "failed", "message": "Missing terminal OXT"}
        with patch.object(self.view, "health"), patch.object(self.view, "render") as render, \
             patch.object(self.view, "write") as write:
            self.view.dispatch("stage", {"event": event}, "report")
        render.assert_not_called()
        self.assertEqual(self.analysis["stages"]["prepared"], event)
        write.assert_called_once_with("report", {"status": "ready", "value": {}})

    def test_failed_later_stage_still_renders_accepted_results(self):
        self.analysis["review"] = {"scene_revision": "scene", "snapshot": {"sha256": "input"}}
        event = {"analysis_id": "analysis", "scene_revision": "scene", "input_hash": "input",
                 "stage": "strain", "status": "failed", "message": "Invalid reference"}
        with patch.object(self.view, "health"), patch.object(self.view, "render", return_value={"retained": True}) as render, \
             patch.object(self.view, "write") as write:
            self.view.dispatch("stage", {"event": event}, "report")
        render.assert_called_once_with("analysis")
        self.assertEqual(self.analysis["stages"]["strain"], event)
        write.assert_called_once_with("report", {"status": "ready", "value": {"retained": True}})

    def fixture_input(self):
        output = Path(__file__).resolve().parents[3] / "run_261003_energy_feasibility/feature-261004/view-input-tests" / uuid.uuid4().hex
        output.mkdir(parents=True)
        sdf = output / "ligand.sdf"
        sdf.write_text("reviewed SDF bytes\n")
        self.protein_atom = types.SimpleNamespace(name="C", symbol="C", resn="ACE", chain="A", resi="1", segi="", index=1, formal_charge=0, q=1, alt="")
        self.ligand_atom = types.SimpleNamespace(name="C1", symbol="C", resn="LIG", chain="L", resi="2", segi="", index=1, formal_charge=0, q=1, alt="")
        self.excluded_atom = types.SimpleNamespace(name="FE", symbol="Fe", resn="HEM", chain="A", resi="3", formal_charge=2, q=1, alt="")
        self.protein = [{"source_id": "source/1/1", "name": "C", "element": "C", "residue": "protein-residue", "resname": "ACE", "chain": "A", "resi": "1", "xyz_nm": [0, 0, 0]}]
        self.ligand = [{"source_id": "source/2/1", "name": "C1", "element": "C", "residue": "ligand-residue", "resname": "LIG", "chain": "L", "resi": "2", "xyz_nm": [1, 0, 0]}]
        self.protein_indices, self.ligand_indices = [("protein", 1)], [("ligand", 1)]
        return {"receptor": "protein", "ligand": "ligand", "sdf_path": str(sdf), "state": 1,
                "output": str(output), "mapping": [0]}

    def input_atoms(self, selection, state):
        if selection == "protein": return self.protein, [], self.protein_indices
        self.assertEqual(selection, "ligand")
        return self.ligand, [], self.ligand_indices

    def input_indices(self, selection):
        selection = selection.split(" and state ")[0]
        if selection.startswith("(") and selection.endswith(")"): selection = selection[1:-1]
        if selection == "protein": return self.protein_indices
        if selection == "ligand": return self.ligand_indices
        if selection.startswith("%"):
            return self.protein_indices + self.ligand_indices + [("protein", 2)]
        return self.protein_indices + self.ligand_indices

    def input_model(self, selection, state):
        if selection == "protein": atoms = [self.protein_atom]
        elif selection == "ligand": atoms = [self.ligand_atom]
        else: atoms = [self.protein_atom, self.ligand_atom] + ([self.excluded_atom] if selection.startswith("%") else [])
        return types.SimpleNamespace(atom=atoms, bond=[])

    def test_confirmation_binds_sdf_graph_mapping_source_and_exclusions_not_coordinates(self):
        arguments = self.fixture_input()
        cmd = self.view.input.__globals__["cmd"]
        with patch.object(self.view, "atoms", side_effect=self.input_atoms), \
             patch.object(cmd, "index", side_effect=self.input_indices, create=True), \
             patch.object(cmd, "get_model", side_effect=self.input_model, create=True), \
             patch.object(self.view, "source_current"):
            preview = self.view.input(**arguments)
            self.assertEqual(preview["exclusions"], ["protein/A/HEM3: 1 atoms"])
            committed = {**arguments, "confirmed": True, "reviewed_hash": preview["reviewed_hash"]}
            Path(arguments["sdf_path"]).write_text("externally changed SDF graph\n")
            with self.assertRaisesRegex(ValueError, "since Review"): self.view.input(**committed)
            Path(arguments["sdf_path"]).write_text("reviewed SDF bytes\n")
            self.excluded_atom.formal_charge = 3
            with self.assertRaisesRegex(ValueError, "since Review"): self.view.input(**committed)
            self.excluded_atom.formal_charge = 2
            self.ligand_atom.formal_charge = 1
            with self.assertRaisesRegex(ValueError, "since Review"): self.view.input(**committed)
            self.ligand_atom.formal_charge = 0
            self.ligand[0]["source_id"] = "source/99/1"
            with self.assertRaisesRegex(ValueError, "since Review"): self.view.input(**committed)
            self.ligand[0]["source_id"] = "source/2/1"
            with self.assertRaisesRegex(ValueError, "since Review"): self.view.input(**{**committed, "mapping": [1]})
            with self.assertRaisesRegex(ValueError, "since Review"): self.view.input(**{**committed, "reviewed_hash": None})
            self.ligand[0]["xyz_nm"] = [1.1, 0, 0]
            result = self.view.input(**committed)
            self.assertEqual(result["reviewed_hash"], preview["reviewed_hash"])
            self.assertNotEqual(result["scene_revision"], preview["scene_revision"])

    def test_smiles_generated_sdf_does_not_require_a_file_and_is_bound_to_confirmation(self):
        arguments = self.fixture_input()
        Path(arguments["sdf_path"]).unlink()
        arguments["sdf_text"] = "SMILES-generated chemistry\n"
        cmd = self.view.input.__globals__["cmd"]
        with patch.object(self.view, "atoms", side_effect=self.input_atoms), \
             patch.object(cmd, "index", side_effect=self.input_indices, create=True), \
             patch.object(cmd, "get_model", side_effect=self.input_model, create=True), \
             patch.object(self.view, "source_current"):
            self.assertEqual(self.view.ligand_input("ligand", 1), {"atoms": self.ligand, "bonds": []})
            preview = self.view.input(**arguments)
            result = self.view.input(**{**arguments, "confirmed": True, "reviewed_hash": preview["reviewed_hash"]})
            snapshot = self.view.analyses[result["analysis_id"]]["snapshot"]
            self.assertEqual(snapshot["ligand_sdf"], arguments["sdf_text"])
            with self.assertRaisesRegex(ValueError, "since Review"):
                self.view.input(**{**arguments, "sdf_text": "changed SMILES chemistry", "confirmed": True, "reviewed_hash": preview["reviewed_hash"]})

    def embedded_fixture(self):
        cmd = self.view.input.__globals__["cmd"]
        atoms = [{"index": index, "name": name, "element": element, "chain": "L", "segi": "", "resi": "2", "resn": "LIG", "sdf_index": mapped}
                 for index, name, element, mapped in [(7, "C1", "C", 0), (9, "N2", "N", 1)]]
        payload = {"version": 1, "ligands": [{"object": "ligand", "state": 2, "source": "source SDF", "smiles": "CN", "sdf": "explicit SDF text", "atoms": atoms}]}
        cmd._pymol.session.ligand_chemistry = payload
        models = [types.SimpleNamespace(index=a["index"], name=a["name"], symbol=a["element"], chain=a["chain"], segi=a["segi"], resi=a["resi"], resn=a["resn"]) for a in reversed(atoms)]
        return cmd, payload, models

    def test_embedded_chemistry_matches_selection_state_and_maps_current_atom_order(self):
        cmd, payload, models = self.embedded_fixture()
        original = copy.deepcopy(payload)
        with patch.object(cmd, "index", return_value=[("ligand", 9), ("ligand", 7)], create=True), \
             patch.object(cmd, "get_model", return_value=types.SimpleNamespace(atom=models), create=True):
            value = self.view.ligand_chemistry("named_ligand_selection", 2)
            self.assertEqual(value["mapping"], [1, 0])
            self.assertEqual(value["sdf"], "explicit SDF text")
            self.assertEqual(value["smiles"], "CN")
            self.assertIsNone(self.view.ligand_chemistry("named_ligand_selection", 1))
            payload["ligands"][0]["sdf"] = None
            self.assertIsNone(self.view.ligand_chemistry("named_ligand_selection", 2)["mapping"])
        original["ligands"][0]["sdf"] = None
        self.assertEqual(payload, original)

    def test_embedded_chemistry_rejects_partial_duplicate_changed_and_invalid_mapping(self):
        cmd, payload, models = self.embedded_fixture()
        with patch.object(cmd, "index", return_value=[("ligand", 9), ("ligand", 7)], create=True), \
             patch.object(cmd, "get_model", return_value=types.SimpleNamespace(atom=models), create=True):
            models[0].symbol = "O"
            with self.assertRaisesRegex(ValueError, "binding changed"): self.view.ligand_chemistry("ligand", 2)
            models[0].symbol = "N"
            payload["ligands"][0]["atoms"][1]["sdf_index"] = 0
            with self.assertRaisesRegex(ValueError, "mapping is invalid"): self.view.ligand_chemistry("ligand", 2)
            payload["ligands"][0]["atoms"][1]["sdf_index"] = 1
            payload["ligands"].append(copy.deepcopy(payload["ligands"][0]))
            with self.assertRaisesRegex(ValueError, "Multiple embedded"): self.view.ligand_chemistry("ligand", 2)
            payload["ligands"].pop()
            payload["ligands"][0]["state"] = True
            with self.assertRaisesRegex(ValueError, "Invalid embedded ligand state"): self.view.ligand_chemistry("ligand", 2)
            payload["ligands"][0]["state"] = 2
            payload["ligands"].append(copy.deepcopy(payload["ligands"][0]))
        payload["ligands"].pop()
        with patch.object(cmd, "index", return_value=[("ligand", 9)], create=True), \
             patch.object(cmd, "get_model", return_value=types.SimpleNamespace(atom=models[:1]), create=True):
            with self.assertRaisesRegex(ValueError, "exactly the ligand"): self.view.ligand_chemistry("ligand_subset", 2)
        payload["version"] = True
        with self.assertRaisesRegex(ValueError, "Unsupported embedded"): self.view.ligand_chemistry("ligand", 2)

    def test_automatic_input_persists_chemistry_and_receptor_settings_before_scoring(self):
        arguments = self.fixture_input()
        arguments.update(preparation_ph=7.0, smiles="C", sdf_text="explicit text", protein_overrides={"protein-residue": "ACE"})
        cmd = self.view.input.__globals__["cmd"]
        with patch.object(self.view, "atoms", side_effect=self.input_atoms), \
             patch.object(cmd, "index", side_effect=self.input_indices, create=True), \
             patch.object(cmd, "get_model", side_effect=self.input_model, create=True), \
             patch.object(self.view, "source_current"):
            preview = self.view.input(**arguments)
            self.assertEqual(cmd._pymol.session.energy_inputs["pH"], 7)
            original = copy.deepcopy(vars(cmd._pymol.session))
            with self.assertRaisesRegex(ValueError, "uniquely map every"):
                self.view.input(**{**arguments, "mapping": []})
            self.assertEqual(vars(cmd._pymol.session), original)
            self.assertEqual(self.view.ligand_chemistry("ligand", 1)["mapping"], [0])
            restored = self.view.session_inputs()
            self.assertEqual(restored["overrides"], {"protein-residue": "ACE"})
            self.assertEqual(restored["ligand"], "ligand")
            result = self.view.input(**{**arguments, "confirmed": True, "reviewed_hash": preview["reviewed_hash"]})
            self.assertEqual(self.view.analyses[result["analysis_id"]]["snapshot"]["preparation_recipe"], "openmm-ph-v1")
            with self.assertRaisesRegex(ValueError, "since Review"):
                self.view.input(**{**arguments, "preparation_ph": 5.0, "confirmed": True, "reviewed_hash": preview["reviewed_hash"]})
            self.protein_atom.name = "changed"
            with self.assertRaisesRegex(ValueError, "Saved receptor atom bindings changed"): self.view.session_inputs()

    def test_pse_results_embed_bytes_and_restore_without_original_directories(self):
        import numpy as np
        root = Path(self.fixture_input()["output"])
        self.analysis.update(review={"analysis_id": "c" * 32, "directory": str(root)}, locators={}, signature={"atoms": {}, "bonds": []}, owned={}, stages={}, parent="energy_test")
        snapshot = self.view.write(root / "snapshot.json", {"state": 1})
        model = self.view.write(root / "model.json", {"particles": []})
        with (root / "prepared.npy").open("wb") as handle: np.save(handle, np.zeros((0, 3)))
        data = (root / "prepared.npy").read_bytes()
        import hashlib
        coordinates = {"path": "prepared.npy", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "shape": [0, 3], "dtype": np.dtype(float).str}
        self.analysis["review"]["snapshot"] = snapshot
        self.analysis["stages"]["prepared"] = {"status": "ready", "artifacts": {"model": model, "coordinates": coordinates}}
        saved = {}
        with patch.object(self.view, "health"), patch.object(self.view, "ledger", return_value={}), \
             patch.object(self.view, "render"), patch.object(self.view, "session_root", root / "new-machine-cache"):
            self.view.session_save(saved)
            self.assertEqual(saved["raymol_energy"]["version"], 2)
            self.assertEqual(saved["raymol_energy"]["analyses"][0]["embedded_artifacts"]["prepared.npy"], data)
            for path in (root / "snapshot.json", root / "model.json", root / "prepared.npy"): path.unlink()
            self.view.session_restore(saved)
            restored = self.view.analyses["c" * 32]
            self.assertFalse(restored.get("stale", False), restored.get("restore_error"))
            self.assertEqual(restored["coordinates"].shape, (0, 3))
            for defect in ("missing", "hash", "path"):
                corrupt = copy.deepcopy(saved)
                row = corrupt["raymol_energy"]["analyses"][0]
                if defect == "missing": row["embedded_artifacts"].pop("prepared.npy")
                elif defect == "hash": row["embedded_artifacts"]["prepared.npy"] += b"bad"
                else: row["review"]["directory"] = "../../outside"
                self.view.session_restore(corrupt)
                self.assertTrue(self.view.analyses["c" * 32]["stale"], defect)

    def contact_fixture(self):
        import numpy as np
        specifications = [("K", "NZ", "N", None, 0), ("K", "HZ1", "H", 0, 0),
                          ("H", "NE2", "N", None, 0), ("A", "CB", "C", None, 0),
                          ("A", "HB1", "H", 3, 0), ("D", "OD1", "O", None, 0),
                          ("D", "OD2", "O", None, 0), ("L", "O1", "O", None, -1),
                          ("L", "C1", "C", None, 0), ("L", "O2", "O", None, 0),
                          ("L", "N1", "N", None, 1)]
        model = {"nprotein": 7, "particles": [{"residue": residue, "name": name, "element": element,
                 "parent": parent, "formal_charge": charge, "sigma_nm": 0.3} for residue, name, element, parent, charge in specifications],
                 "provenance": {"templates": {"K": "LYS", "H": "HIE", "A": "ALA", "D": "ASP"}},
                 "bonds": [{"atoms": [7, 8], "order": 1}, {"atoms": [8, 9], "order": 2}]}
        xyz = np.array([[3, 0, 0], [2, 0, 0], [6, 0, 0], [4, 0, 0], [4.1, 0, 0],
                        [1, 3, 0], [1, 3.2, 0], [0, 0, 0], [0.2, 0, 0], [0.5, 0, 0], [1, 0, 0]], dtype=float)
        pairs = np.zeros((7, 4, 3))
        pairs[1, 0] = [-4, 0, 2]
        pairs[2, 0, 0] = -30
        pairs[3, 0] = [-0.1, -2, 0.2]
        hbond = {"donor": 0, "hydrogen": 1, "acceptor": 7}
        return model, xyz, pairs, [hbond]

    def test_salt_groups_use_confirmed_charge_states_and_resonance_not_partial_charges(self):
        model, xyz, pairs, hbonds = self.contact_fixture()
        result = self.view.selective_contacts(model, xyz, pairs, hbonds, 0.1, 8, 200)
        salts = result["salt_bridges"]
        self.assertEqual(len(salts), 2)
        self.assertTrue(any(record["group_atoms"] == [[0], [7, 9]] for record in salts))
        self.assertTrue(all(record["formal_charges"][0]*record["formal_charges"][1] < 0 for record in salts))
        model["provenance"]["templates"].update(K="LYN", D="ASH")
        self.assertEqual(self.view.selective_contacts(model, xyz, pairs, hbonds, 0.1, 8, 200)["salt_bridges"], [])
        model["provenance"]["templates"].update(K="LYS", D="ASP")
        xyz[7:] += 20
        self.assertEqual(self.view.selective_contacts(model, xyz, pairs, hbonds, 0.1, 8, 200)["salt_bridges"], [])

    def test_normal_hbond_not_clash_but_independent_and_severe_overlaps_still_clash(self):
        model, xyz, pairs, hbonds = self.contact_fixture()
        result = self.view.selective_contacts(model, xyz, pairs, hbonds, 0.1, 8, 200)
        self.assertEqual(result["clashes"], [])
        pairs[3, 1, 2] = 8
        result = self.view.selective_contacts(model, xyz, pairs, hbonds, 0.1, 8, 200)
        self.assertEqual(result["clashes"], [3*4+1])
        xyz[0] = [1.5, 0, 0]
        result = self.view.selective_contacts(model, xyz, pairs, hbonds, 0.1, 8, 200)
        self.assertIn(1*4, result["clashes"])

    def test_packing_rank_ignores_large_coulomb_only_attraction_and_keeps_arrays_unchanged(self):
        import numpy as np
        model, xyz, pairs, hbonds = self.contact_fixture()
        original_model = copy.deepcopy(model)
        before = pairs.tobytes(), xyz.tobytes()
        result = self.view.selective_contacts(model, xyz, pairs, hbonds, 0.1, 8, 200)
        self.assertEqual([record["residue"] for record in result["strong_contacts"]], ["A"])
        self.assertAlmostEqual(result["strong_contacts"][0]["residue_net_lj_kcal"], -1.8)
        self.assertEqual(result["residue_totals"]["H"]["residue_coulomb_kcal"], -30)
        np.testing.assert_array_equal(pairs.sum(axis=(0, 1)), np.array([-34.1, -2, 2.2]))
        self.assertEqual(before, (pairs.tobytes(), xyz.tobytes()))
        self.assertEqual(original_model, model)
        self.assertEqual(self.view.selective_contacts(model, xyz, pairs, hbonds, 5, 8, 200)["strong_contacts"], [])

    def test_contact_caps_counts_and_order_are_deterministic(self):
        import numpy as np
        count = 40
        model = {"nprotein": count, "particles": [{"name": "CA", "residue": str(i), "element": "C", "parent": None, "formal_charge": 0, "sigma_nm": 0.3} for i in range(count+1)],
                 "bonds": [], "provenance": {"templates": {str(i): "ALA" for i in range(count)}}}
        xyz = np.array([[3+i*0.02, 0, 0] for i in range(count)]+[[0, 0, 0]])
        pairs = np.zeros((count, 1, 3))
        pairs[:, :, 1] = -2
        a = self.view.selective_contacts(model, xyz, pairs, [], 0.1, 8, 200)
        b = self.view.selective_contacts(model, xyz, pairs, [], 0.1, 8, 200)
        self.assertEqual(a, b)
        self.assertEqual(len(a["strong_contacts"]), 8)
        self.assertEqual(a["eligible"]["strong_contacts"], count)
        self.assertEqual([record["pair_id"] for record in a["strong_contacts"]], list(range(8)))
        pairs[:, :, 2] = 10
        result = self.view.selective_contacts(model, xyz, pairs, [], 0.1, 8, 200)
        self.assertEqual(result["clashes"], list(range(24)))
        self.assertEqual(result["eligible"]["clashes"], count)

    def test_capped_termini_are_neutral_and_confirmed_uncapped_termini_are_charged(self):
        model, xyz, pairs, hbonds = self.contact_fixture()
        model["provenance"]["templates"].update(K="NLYN", D="CASH")
        model["particles"][0]["name"] = "N"
        model["particles"][5]["name"] = "O"
        model["particles"][6]["name"] = "OXT"
        groups = self.view.ionic_groups(model)
        self.assertIn({"atoms": [0], "charge": 1}, groups)
        self.assertIn({"atoms": [5, 6], "charge": -1}, groups)
        model["provenance"]["templates"].update(K="ACE", D="NME")
        self.assertTrue(all(group["atoms"][0] >= 7 for group in self.view.ionic_groups(model)))

    def test_default_groups_disable_all_pair_views_and_keep_four_selective_toggles(self):
        cmd = self.view.groups.__globals__["cmd"]
        names = []
        analysis = {"owned": {}, "review": {"analysis_id": "a"*32}}
        with patch.object(cmd, "get_names", return_value=[], create=True), \
             patch.object(cmd, "group", side_effect=lambda name, *args: names.append(name) if name not in names else None, create=True), \
             patch.object(cmd, "load_cgo", side_effect=lambda data, name, *args, **kwargs: names.append(name), create=True), \
             patch.object(cmd, "disable", create=True) as disabled, \
             patch.object(self.view, "ledger", side_effect=lambda: {str(i): {"name": name} for i, name in enumerate(names)}), \
             patch.object(self.view.groups.__globals__["cgo"], "STOP", 0, create=True):
            self.view.groups(analysis)
            self.view.groups(analysis)
        off = {call.args[0] for call in disabled.call_args_list}
        enabled = {leaf for leaf, name in analysis["owned"].items() if name not in off}
        self.assertEqual(enabled, {"hbonds", "salt_bridges", "clashes", "strong_contacts"})
        self.assertIn(analysis["owned"]["electrostatics"], off)
        self.assertIn(analysis["owned"]["packing"], off)

    def test_neutral_nitro_resonance_does_not_create_false_salt_centers(self):
        model, xyz, pairs, hbonds = self.contact_fixture()
        model["particles"][8].update(element="N", formal_charge=1)
        model["particles"][10]["formal_charge"] = 0
        self.assertTrue(all(group["atoms"][0] < 7 for group in self.view.ionic_groups(model)))
        self.assertEqual(self.view.selective_contacts(model, xyz, pairs, hbonds, 0.1, 8, 200)["salt_bridges"], [])
