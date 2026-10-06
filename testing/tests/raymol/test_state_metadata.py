"""Targeted inspector-query tests without launching or changing a live viewer."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


class StateMetadataQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.command = MagicMock()
        pymol = types.ModuleType("pymol")
        pymol.cmd = cls.command
        source = Path(__file__).resolve().parents[3] / "modules/pymol/appkit_inspector.py"
        spec = importlib.util.spec_from_file_location("metadata_inspector", source)
        cls.inspector = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"pymol": pymol}):
            spec.loader.exec_module(cls.inspector)

    def setUp(self):
        self.command.reset_mock()
        self.command.get_names.return_value = ["ligand"]
        self.command.get_type.return_value = "object:molecule"
        self.command.get.side_effect = lambda key, *args: 2 if key == "state" else 0
        self.command.get_state.return_value = 3
        self.command.count_states.return_value = 3
        self.command.get_title.return_value = "compound"
        self.command.get_scene_list.return_value = []
        self.command.get_view.return_value = [0] * 18
        self.command.get_extent.return_value = [[0, 0, 0], [1, 1, 1]]
        self.command.get_property_list.side_effect = None
        self.command.get_property_list.return_value = ["compound_id", "GPS", "active"]
        self.command.get_property.side_effect = lambda name, obj, state: {
            "compound_id": "00123", "GPS": 0.938765, "active": False
        }[name]
        for name, value in (("takes_atom_selection", False), ("_object_peel", 0),
                            ("_bg_rgb", [0, 0, 0]), ("_color_setting_rgb", [0, 0, 0])):
            patcher = patch.object(self.inspector, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def meta(self):
        return self.inspector._build(["ligand"])["objmeta"]["ligand"]

    def test_queries_only_pinned_state_and_keeps_scalar_values(self):
        meta = self.meta()
        self.assertEqual(meta["state"], 2)
        self.assertEqual(meta["property_state"], 2)
        self.assertEqual(meta["properties"], {
            "compound_id": "00123", "GPS": 0.938765, "active": False
        })
        self.command.get_property_list.assert_called_once_with("ligand", state=2)
        self.assertTrue(all(call.kwargs["state"] == 2
                            for call in self.command.get_property.call_args_list))

    def test_unpinned_state_resolves_to_global_frame(self):
        self.command.get.side_effect = lambda *args: 0
        self.assertEqual(self.meta()["property_state"], 3)

    def test_singleton_uses_state_one_on_later_frame(self):
        self.command.count_states.return_value = 1
        self.assertEqual(self.meta()["property_state"], 1)

    def test_nonmolecule_and_deleted_objects_are_not_queried(self):
        self.command.get_type.return_value = "object:group"
        self.assertNotIn("properties", self.meta())
        self.command.get_property_list.assert_not_called()
        self.command.get_names.return_value = []
        self.assertNotIn("ligand", self.inspector._build(["ligand"])["objmeta"])

    def test_missing_tags_are_empty_not_stale(self):
        self.command.get_property_list.return_value = []
        self.assertEqual(self.meta()["properties"], {})

    def test_query_failure_and_nonfinite_data_are_visible(self):
        self.command.get_property_list.side_effect = RuntimeError("query failed")
        self.assertEqual(self.meta()["property_error"], "query failed")
        self.command.get_property_list.side_effect = None
        self.command.get_property.side_effect = lambda *args, **kwargs: float("nan")
        meta = self.meta()
        self.assertIn("Invalid metadata", meta["property_error"])
        self.assertNotIn("properties", meta)
