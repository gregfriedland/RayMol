'''
Movie export quality overrides (#581): snapshot/restore and cmd.movie_export
request building. Rendering itself happens in the native app's MovieExporter.
'''

import json

from pymol import cmd, testing
from pymol import movie_exporting as me


class TestMovieExportOverrides(testing.PyMOLTestCase):

    def tearDown(self):
        me.restore()
        super().tearDown()

    def test_standard_is_empty(self):
        self.assertEqual(me.preset_overrides('standard'), {})
        self.assertEqual(me.PRESET_SUPERSAMPLE['standard'], 1)

    def test_presets_only_use_overridable_settings(self):
        for name, table in me.QUALITY_PRESETS.items():
            for key in table:
                self.assertIn(key, me.OVERRIDABLE, '%s: %s' % (name, key))
                # Must be a real setting name.
                cmd.get(key)

    def test_restore_after_apply(self):
        before = {k: cmd.get(k) for k in me.OVERRIDABLE}
        me.apply_overrides(me.preset_overrides('maximum'))
        self.assertEqual(cmd.get('cartoon_sampling'), '20')
        self.assertEqual(cmd.get('metal_rt_samples'), '256')
        me.restore()
        after = {k: cmd.get(k) for k in me.OVERRIDABLE}
        self.assertEqual(before, after)
        self.assertEqual(me.saved_settings(), {})

    def test_apply_json_string(self):
        orig = cmd.get('stick_quality')
        me.apply_overrides(json.dumps({'stick_quality': 24}))
        self.assertEqual(cmd.get('stick_quality'), '24')
        me.restore()
        self.assertEqual(cmd.get('stick_quality'), orig)

    def test_second_apply_keeps_original_snapshot(self):
        orig = cmd.get('sphere_quality')
        me.apply_overrides({'sphere_quality': 3})
        me.apply_overrides({'sphere_quality': 4})
        me.restore()
        self.assertEqual(cmd.get('sphere_quality'), orig)

    def test_restore_is_idempotent(self):
        orig = cmd.get('metal_msaa')
        me.apply_overrides({'metal_msaa': 0})
        me.restore()
        me.restore()
        self.assertEqual(cmd.get('metal_msaa'), orig)

    def test_rejects_unknown_setting(self):
        orig = cmd.get('bg_rgb')
        with self.assertRaises(ValueError):
            me.apply_overrides({'bg_rgb': 'red'})
        self.assertEqual(cmd.get('bg_rgb'), orig)
        self.assertEqual(me.saved_settings(), {})

    def test_unknown_quality(self):
        with self.assertRaises(ValueError):
            me.preset_overrides('ultra')


class TestMovieExportCommand(testing.PyMOLTestCase):

    def test_request_defaults(self):
        req = cmd.movie_export('/tmp/x.mp4')
        self.assertEqual((req['width'], req['height']), (1920, 1080))
        self.assertEqual((req['format'], req['codec']), ('mp4', 'h264'))
        self.assertEqual(req['overrides'], {})
        self.assertEqual(req['supersample'], 1)

    def test_request_does_not_change_settings(self):
        before = {k: cmd.get(k) for k in me.OVERRIDABLE}
        cmd.movie_export('/tmp/x.mp4', quality='maximum')
        self.assertEqual(before, {k: cmd.get(k) for k in me.OVERRIDABLE})

    def test_format_from_extension(self):
        self.assertEqual(cmd.movie_export('/tmp/x.mov')['codec'], 'prores')
        self.assertEqual(cmd.movie_export('/tmp/x.mov', codec='hevc')['codec'], 'hevc')
        self.assertEqual(cmd.movie_export('/tmp/x.gif')['format'], 'gif')
        self.assertEqual(cmd.movie_export('/tmp/frames')['format'], 'png')

    def test_preset_and_rt_samples(self):
        req = cmd.movie_export('/tmp/x.mp4', 3840, 2160, quality='high', rt_samples=200)
        self.assertEqual(req['overrides']['metal_rt_samples'], 200)
        self.assertEqual(req['overrides']['cartoon_sampling'], 14)
        self.assertEqual(cmd.movie_export('/tmp/x.mp4', quality='maximum')['supersample'], 2)

    def test_empty_filename(self):
        with self.assertRaises(ValueError):
            cmd.movie_export('')

    def test_command_syntax(self):
        cmd.do('movie_export /tmp/y.mp4, 2560, 1440, quality=high')

    def test_invalid(self):
        for kw in ({'codec': 'prores'}, {'supersample': 3}, {'width': 99999},
                   {'format': 'avi'}, {'codec': 'png'}, {'bitrate': -1},
                   {'bitrate': 1000}, {'bitrate': float('nan')},
                   {'first': 0}, {'first': 5, 'last': 3}):
            with self.assertRaises(ValueError):
                cmd.movie_export('/tmp/x.mp4', **kw)


class TestPresetTablesInSync(testing.PyMOLTestCase):
    '''The Swift sheet keeps its own copy of the presets (MovieQuality.overrides);
    scripted and UI exports at the same preset must match.'''

    def test_swift_matches_python(self):
        import os, re
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, '..', '..', '..', 'swiftui', 'PyMOLViewer',
                            'Panels', 'MovieExportSheet.swift')
        if not os.path.exists(path):
            self.skipTest('Swift sources not present')
        src = open(path).read()
        body = src[src.index('var overrides: [String: Int] {'):]
        body = body[:body.index('// Mirrors PRESET_SUPERSAMPLE')]
        for name in ('draft', 'high', 'maximum'):
            m = re.search(r'case \.%s:\s*return \[(.*?)\]' % name, body, re.S)
            self.assertTrue(m, name)
            swift = {k: int(v) for k, v in re.findall(r'"(\w+)":\s*(-?\d+)', m.group(1))}
            self.assertEqual(swift, me.QUALITY_PRESETS[name], name)
        self.assertIn('case .standard, .custom:\n            return [:]', body)
