"""Every Inspector command that interpolates an object NAME goes through the
name guard (#531).

ObjectPanel.swift builds PyMOL commands by interpolating names into command
language. With `validate_object_names` off, a name can carry a line break or
`;` and split the command. engine.runCommand(_:naming:) refuses a command
whose names are outside PyMOL's own alphabet. This test fails if a builder
interpolates one of the panel's name variables without passing it through
`naming:` -- the regression a new button would otherwise introduce silently.

Source-reading, so skipped (not passed) outside a repo checkout.

Runs on a RayMol --testing build:
    pymol -ckqy testing/testing.py --run testing/tests/raymol/inspector_command_names.py
"""
import os
import re

from pymol import testing

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir,
                                     os.pardir, os.pardir))
PANEL = os.path.join(ROOT, 'swiftui', 'PyMOLViewer', 'Panels',
                     'ObjectPanel.swift')

# The variables that hold a name read back from PyMOL in ObjectPanel.swift.
NAME_VARS = ('name', 'objName', 'entry.name', 'sel.name', 'member',
             'target.name', 'g.name', 'escaped')


def calls(text):
    """(line number, argument text) of every engine.runCommand( call."""
    for m in re.finditer(r'engine\.runCommand\(', text):
        i = m.end()
        depth = 1
        while depth and i < len(text):
            depth += {'(': 1, ')': -1}.get(text[i], 0)
            i += 1
        yield text.count('\n', 0, m.start()) + 1, text[m.end():i - 1]


class TestInspectorCommandNames(testing.PyMOLTestCase):

    def setUp(self):
        super().setUp()
        if not os.path.isfile(PANEL):
            self.skipTest('ObjectPanel.swift not present; not a repo checkout')
        with open(PANEL, encoding='utf-8') as handle:
            self.src = handle.read()

    def testEveryNameInterpolatingCommandIsGuarded(self):
        unguarded = []
        n = 0
        for line, arg in calls(self.src):
            # `scene <name>, ...`: scene names are not object names -- PyMOL
            # allows spaces in them -- so they are deliberately not guarded.
            if arg.lstrip().startswith('"scene '):
                continue
            names = [v for v in NAME_VARS if '\\(%s)' % v in arg]
            if names:
                n += 1
                if 'naming:' not in arg:
                    unguarded.append('%d: %s' % (line, names))
        # the scan must find the builders, or it checks nothing
        self.assertGreater(n, 40)
        self.assertEqual(unguarded, [])

    def testTheGuardRefusesByTheNameAlphabet(self):
        body = self.src[self.src.index('func runCommand(_ command: String, naming'):]
        body = body[:body.index('\n    }\n')]
        self.assertIn('names.allSatisfy(isLegalObjectName)', body)
        self.assertIn('return', body)
