"""Movies replay each scene's PER-OBJECT settings, and building one leaves the
live session alone (#508).

A scene captures globals and per-object overrides (materials,
metal_rt_reflect*). Two things were wrong with movies built from scenes:

  * playback replayed only the GLOBAL half. raymol_scene_anim.enter_scene,
    the frame command authored at each scene cut, applied the scene's globals
    and never its per-object overrides -- so in a marble -> clay movie every
    object changed except the ones the user had styled, which stayed frozen;
  * AUTHORING mutated the live session. Every authoring path recalled each
    scene -- and `mview store ... scene=` recalls it again by itself -- with
    the recall hook live, then scrubbed every frame (running every authored
    frame command) to find the cuts. The session was left carrying whichever
    scene's values were applied last.

Frame commands run only when THEIR frame is displayed, so playback is
simulated by displaying every frame in order, as the player and an export do.

Runs on a RayMol --testing build:
    pymol -ckqy testing/testing.py --run testing/tests/raymol/movie_object_settings.py
"""
import base64
import json

from pymol import cmd, testing
from pymol import appkit_movie as am


def obj_state():
    return (cmd.get('stick_material', 'o1'), cmd.get('metal_rt_reflect', 'o2'),
            cmd.get('material_default'))


class TestMovieObjectSettings(testing.PyMOLTestCase):

    def setUp(self):
        super().setUp()
        cmd.reinitialize()
        cmd.fragment('ala', 'o1')
        cmd.fragment('gly', 'o2')
        cmd.hide('everything')
        cmd.show('sticks')
        # scene s1: per-object marble / 0.1, global matte
        cmd.set('stick_material', 'marble', 'o1')
        cmd.set('metal_rt_reflect', 0.1, 'o2')
        cmd.set('material_default', 'matte')
        cmd.scene('s1', 'store')
        # scene s2: per-object clay / 0.9, global plastic
        cmd.set('stick_material', 'clay', 'o1')
        cmd.set('metal_rt_reflect', 0.9, 'o2')
        cmd.set('material_default', 'plastic')
        cmd.scene('s2', 'store')
        # the LIVE state authoring must not disturb: neither scene's
        cmd.set('stick_material', 'rubber', 'o1')
        cmd.set('metal_rt_reflect', 0.5, 'o2')
        cmd.set('material_default', 'clay')
        self.live = obj_state()

    def play(self):
        """{frame: obj_state()} displaying every frame in order."""
        seen = {}
        for f in range(1, cmd.count_frames() + 1):
            cmd.frame(f)
            seen[f] = obj_state()
        return seen

    def assertAuthoringLeftTheSessionAlone(self):
        """Either untouched, or exactly what the frame now on screen shows in
        playback. rebuild() ends with cmd.rewind(), which DISPLAYS frame 1 and
        so runs its scene cut -- that is the movie showing its first frame, not
        authoring leaking. What must never happen is the LAST authored scene's
        values being left applied (the bug)."""
        after = obj_state()
        here = int(cmd.get_frame() or 1)
        seen = self.play()
        self.assertIn(after, (self.live, seen[here]),
                      'authoring left %r live at frame %d' % (after, here))
        return seen

    def assertStepsBetweenTheScenes(self, seen):
        s1 = ('marble', '0.10000', 'matte')
        s2 = ('clay', '0.90000', 'plastic')
        # the first frames are inside s1's span, and somewhere later the movie
        # has cut to s2 -- per-object values included, not only the global
        self.assertEqual(seen[2], s1)
        self.assertIn(s2, list(seen.values()))
        firsts2 = min(f for f, v in seen.items() if v == s2)
        self.assertGreater(firsts2, 2)

    def testPlaceSceneLeavesTheSessionAloneAndPlaybackSteps(self):
        cmd.mset('1 x60')
        am.place_scene(1, 's1')
        am.place_scene(40, 's2')
        self.assertStepsBetweenTheScenes(self.assertAuthoringLeftTheSessionAlone())

    def testTheScenesTemplateLeavesTheSessionAloneAndPlaybackSteps(self):
        am.append_template('scenes', seconds_per_scene=1.0, scenes=['s1', 's2'])
        self.assertStepsBetweenTheScenes(self.assertAuthoringLeftTheSessionAlone())

    def testRebuildLeavesTheSessionAloneAndPlaybackSteps(self):
        spec = [{'frame': 1, 'scene': base64.b64encode(b's1').decode(),
                 'power': 0.0, 'linear': 0},
                {'frame': 40, 'end': 60,
                 'scene': base64.b64encode(b's2').decode(),
                 'power': 0.0, 'linear': 0}]
        am.rebuild(json.dumps(spec))
        self.assertStepsBetweenTheScenes(self.assertAuthoringLeftTheSessionAlone())
