"""The Custom material, core half (#568).

A layer's material is its base material plus optional per-layer overrides of
that material's own knobs: `<rep>_material_reflect`, `_tint`, `_rough` and
`_knob1`..`_knob6` for cartoon, surface, stick and sphere. Unset means the
base's value. They are OBJECT-scoped settings, and only the knobs the base's
FAMILY has are used -- an override tunes a material, it never switches the
shading model:

  * reflective (plastic, metallic): reflect, tint, rough;
  * glass: rough (the frost) and knob1..knob3 (jelly's absorption, inner glow,
    wet highlight); knob6 would land on p[5], which the renderer owns;
  * procedural (matte, marble, clay, rubber): knob1..knob6 -> p[0..5];
  * default: none.

Asserted on the FINAL draw params (`get_material_draw_params`) -- what the
shader is handed -- not on the settings.

Runs on a RayMol --testing build:
    pymol -ckqy testing/testing.py --run testing/tests/raymol/material_custom.py
"""
from pymol import _cmd, cmd, setting, testing
from pymol.constants import repres

REPS = ('cartoon', 'surface', 'stick', 'sphere')
KNOBS = ('reflect', 'tint', 'rough', 'knob1', 'knob2', 'knob3',
         'knob4', 'knob5', 'knob6')
REP_INDEX = {'cartoon': repres['cartoon'], 'surface': repres['surface'],
             'stick': repres['sticks'], 'sphere': repres['spheres']}


def params(obj, rep='surface'):
    """(family, mode, reflect, tint, rough, (p0..p5))"""
    f, m, r, t, ro, p = _cmd.get_material_draw_params(cmd._COb, obj, REP_INDEX[rep])
    return f, m, r, t, ro, tuple(p)


class TestTheSettings(testing.PyMOLTestCase):

    def testThirtySixObjectScopedSettingsInOneBlock(self):
        names = ['%s_material_%s' % (r, k) for r in REPS for k in KNOBS]
        indices = [setting._get_index(n) for n in names]
        # .pse files store indices: these are theirs for good
        self.assertEqual(indices, list(range(846, 882)))
        for n in names:
            self.assertEqual(
                _cmd.get_setting_level(setting._get_index(n)), 'object', n)

    def testTheSceneCapturesThemPerObject(self):
        from pymol import raymol_scenes as rs
        for r in REPS:
            for k in KNOBS:
                self.assertIn('%s_material_%s' % (r, k), rs.OBJECT_CAPTURE)


class TestTheOverrides(testing.PyMOLTestCase):

    def setUp(self):
        super().setUp()
        cmd.reinitialize()
        cmd.fragment('ala', 'm1')
        cmd.fragment('gly', 'm2')

    def testUnsetMeansTheMaterialsOwnValues(self):
        cmd.set('surface_material', 'metallic', 'm1')
        self.assertEqual(params('m1')[2:5], (0.6000000238418579,
                                             0.3499999940395355,
                                             0.3499999940395355))

    def testAReflectiveMaterialTakesReflectTintAndRough(self):
        cmd.set('surface_material', 'metallic', 'm1')
        base = params('m1')
        cmd.set('surface_material_reflect', 0.9, 'm1')
        cmd.set('surface_material_rough', 0.05, 'm1')
        f, m, r, t, ro, _p = params('m1')
        self.assertAlmostEqual(r, 0.9, places=5)
        self.assertAlmostEqual(t, 0.35, places=5)   # not overridden: its own
        self.assertAlmostEqual(ro, 0.05, places=5)
        cmd.set('surface_material_tint', 1.0, 'm1')
        self.assertAlmostEqual(params('m1')[3], 1.0, places=5)
        # the shading model is still the base's
        self.assertEqual((f, m), base[:2])

    def testGlassTakesItsFrostAndJellysKnobsOnly(self):
        cmd.set('surface_material', 'jelly', 'm1')
        base = params('m1')
        cmd.set('surface_material_rough', 0.5, 'm1')
        for k in (1, 2, 3):
            cmd.set('surface_material_knob%d' % k, 0.1 * k, 'm1')
        cmd.set('surface_material_knob6', 9.0, 'm1')      # p[5]: not a glass knob
        cmd.set('surface_material_reflect', 0.7, 'm1')    # not a glass knob
        _f, _m, r, _t, ro, p = params('m1')
        self.assertAlmostEqual(ro, 0.5, places=5)
        for k in (1, 2, 3):
            self.assertAlmostEqual(p[k - 1], 0.1 * k, places=5)
        self.assertEqual(p[5], base[5][5])
        self.assertEqual(r, base[2])

    def testAProceduralMaterialTakesAllSixKnobs(self):
        cmd.set('surface_material', 'marble', 'm1')
        for k in range(1, 7):
            cmd.set('surface_material_knob%d' % k, 0.5 + k, 'm1')
        cmd.set('surface_material_reflect', 0.8, 'm1')    # not a procedural knob
        _f, _m, r, t, ro, p = params('m1')
        for k in range(1, 7):
            self.assertAlmostEqual(p[k - 1], 0.5 + k, places=5)
        self.assertEqual((r, t, ro), (0.0, 0.0, 0.0))

    def testDefaultHasNoKnobs(self):
        base = params('m1')
        for k in KNOBS:
            cmd.set('surface_material_%s' % k, 0.77, 'm1')
        self.assertEqual(params('m1'), base)

    def testAnOverrideIsOneLayerOfOneObject(self):
        for rep in ('surface', 'cartoon'):
            cmd.set('%s_material' % rep, 'metallic', 'm1')
        cmd.set('surface_material', 'metallic', 'm2')
        cmd.set('surface_material_reflect', 0.95, 'm1')
        self.assertAlmostEqual(params('m1', 'surface')[2], 0.95, places=5)
        self.assertAlmostEqual(params('m1', 'cartoon')[2], 0.6, places=5)
        self.assertAlmostEqual(params('m2', 'surface')[2], 0.6, places=5)

    def testAGlobalValueIsNotAnOverride(self):
        """Custom tunes one layer of one object. A global value (every object
        has one, as a fallback) must not become everyone's override."""
        cmd.set('surface_material', 'metallic', 'm1')
        cmd.set('surface_material_reflect', 0.95)
        self.assertAlmostEqual(params('m1')[2], 0.6, places=5)

    def testEveryLayerReadsItsOwnSettings(self):
        for rep in REPS:
            cmd.set('%s_material' % rep, 'metallic', 'm1')
            cmd.set('%s_material_reflect' % rep, 0.1 + REPS.index(rep) * 0.2, 'm1')
        for i, rep in enumerate(REPS):
            self.assertAlmostEqual(params('m1', rep)[2], 0.1 + i * 0.2, places=5,
                                   msg=rep)

    def testASceneRestoresTheOverrides(self):
        cmd.set('surface_material', 'metallic', 'm1')
        cmd.set('surface_material_reflect', 0.2, 'm1')
        cmd.scene('s1', 'store')
        cmd.set('surface_material_reflect', 0.9, 'm1')
        cmd.scene('s1', 'recall', animate=0)
        self.assertAlmostEqual(params('m1')[2], 0.2, places=5)
        # ...and an override added after the store is taken away again
        cmd.set('surface_material_tint', 0.9, 'm1')
        cmd.scene('s1', 'recall', animate=0)
        self.assertAlmostEqual(params('m1')[3], 0.35, places=5)
