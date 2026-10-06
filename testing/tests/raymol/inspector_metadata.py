"""Native-core regression checks for metadata persistence and state selection."""

import unittest

from pymol import appkit_inspector, cmd


class TestStateProperties(unittest.TestCase):
    def setUp(self):
        cmd.delete('all')
        cmd.fragment('gly', 'ligand')
        cmd.create('ligand', 'ligand', 1, 2)

    def testBooleanValuesSurviveSessionSerialization(self):
        cmd.set_property('active', False, 'ligand', state=1, proptype=1)
        cmd.set_property('active', True, 'ligand', state=2, proptype=1)
        session = cmd.get_session()
        cmd.delete('all')
        cmd.set_session(session)
        self.assertIs(cmd.get_property('active', 'ligand', state=1), False)
        self.assertIs(cmd.get_property('active', 'ligand', state=2), True)

    def testCurrentStatePropertiesDoNotLeakAcrossStates(self):
        for state in (1, 2):
            cmd.set_property('rank', state, 'ligand', state=state, proptype=2)
            cmd.set_property('compound_id', str(state).zfill(5), 'ligand', state=state, proptype=6)
        for state in (2, 1):
            cmd.set('state', state, 'ligand')
            meta = appkit_inspector._build(['ligand'])['objmeta']['ligand']
            self.assertEqual(meta['property_state'], state)
            self.assertEqual(meta['properties'], {'rank': state, 'compound_id': str(state).zfill(5)})
