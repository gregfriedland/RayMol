"""metal_rt_transparent (#532): transparent geometry in the Metal ray tracer.

Metal RT used to leave every transparent draw out of its acceleration
structure, so glass, frosted glass and jelly cast no traced shadow or AO and
were absent from reflections. With `metal_rt_transparent 1` the transparent
draws go into a SEPARATE structure: a shadow ray that reaches no opaque caster
is attenuated by the transparent reps it crosses, an AO ray that misses opaque
geometry occludes by the alpha of a transparent hit, and a reflection ray
blends the nearest transparent surface over what it hit.

It is off by default, and the default must stay byte-identical. That rests on
two things this file pins in the source, since the rendering itself needs a
Metal GPU:
  * the RT shaders are SPECIALISED on a function constant, and the default
    pipelines are built with it false, so the new code is compiled out of them;
  * every argument only the transparent variant reads is itself gated on that
    constant. A plain argument would have to be bound on every default frame
    too, and Metal API validation aborts on an unbound one.

    pymol -ckqy testing/testing.py --run testing/tests/raymol/metal_rt_transparent.py
"""
import os
import re

from pymol import _cmd, cmd, setting, testing

SOURCE = os.path.join('layerGraphics', 'metal', 'RendererMetal.mm')


def rt_source():
    root = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, os.pardir)
    with open(os.path.normpath(os.path.join(root, SOURCE))) as f:
        src = f.read()
    m = re.search(r'static NSString\* const kRTSrc = @R"\((.*?)\)"', src, re.S)
    return src, m.group(1)


class TestTheSetting(testing.PyMOLTestCase):

    def testItIsAGlobalBooleanAtItsOwnIndex(self):
        # .pse files store indices: 845 is this setting's for good
        self.assertEqual(setting._get_index('metal_rt_transparent'), 845)
        self.assertEqual(
            _cmd.get_setting_level(setting._get_index('metal_rt_transparent')),
            'global')

    def testItIsOffByDefault(self):
        cmd.reinitialize()
        self.assertEqual(cmd.get_setting_boolean('metal_rt_transparent'), 0)

    def testAScenePutsItBack(self):
        from pymol import raymol_scenes as rs
        from pymol import appkit_inspector as ai
        self.assertIn('metal_rt_transparent', rs.CAPTURE)
        self.assertIn('metal_rt_transparent', ai.SCENE_SETTINGS)
        cmd.reinitialize()
        cmd.fragment('ala', 'm1')
        cmd.set('metal_rt_transparent', 1)
        cmd.scene('s1', 'store')
        cmd.set('metal_rt_transparent', 0)
        cmd.scene('s1', 'recall')
        self.assertEqual(cmd.get_setting_boolean('metal_rt_transparent'), 1)


class TestTheDefaultIsCompiledOut(testing.PyMOLTestCase):

    def testTheConstantIsFunctionConstantZero(self):
        _src, rt = rt_source()
        self.assertRegex(rt, r'constant bool kRTTrans \[\[function_constant\(0\)\]\];')

    def testTheDefaultPipelinesAreSpecialisedFalse(self):
        src, _rt = rt_source()
        # the specialisation writes index 0, the constant's, with the value
        # the caller asked for
        self.assertRegex(src, r'bool t = transparent;\s*'
                              r'\[fc setConstantValue:&t type:MTLDataTypeBool atIndex:0\]')
        # the default pair is built false; the transparent pair only when the
        # setting is on
        self.assertIn('buildRTPipelines(false, &_rtAOPipeline, &_rtResolvePipeline);', src)
        self.assertRegex(
            src, r'if \(_rtLib && _rtTransparent && !_rtTCompileTried\) \{\s*'
                 r'_rtTCompileTried = true;\s*'
                 r'buildRTPipelines\(true, &_rtAOPipelineT, &_rtResolvePipelineT\);')

    def testEveryTransparentOnlyArgumentIsGated(self):
        _src, rt = rt_source()
        # buffer slots 9..12 exist only for the transparent structure
        args = re.findall(r'\[\[buffer\((9|1[0-2])\)([^\]]*)\]\]', rt)
        self.assertGreaterEqual(len(args), 6)   # 3 in rt_ao, 3 in rt_composite
        for slot, rest in args:
            self.assertIn('function_constant(kRTTrans)', rest, 'buffer(%s)' % slot)

    def testTheTransparentPipelinesAreUsedOnlyWithTheStructure(self):
        src, _rt = rt_source()
        # chosen only when the setting is on AND the structure and its
        # pipelines exist -- otherwise the default pair, which binds nothing new
        self.assertRegex(src, r'const bool doRTTrans = doRT && _rtTransparent && _rtTReady && '
                              r'_rtTransAS &&\s*_rtAOPipelineT && _rtResolvePipelineT;')
        self.assertIn('setRenderPipelineState:doRTTrans ? _rtAOPipelineT : _rtAOPipeline]', src)
        self.assertIn('setRenderPipelineState:doRTTrans ? _rtResolvePipelineT : _rtResolvePipeline]', src)
