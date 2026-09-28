"""Material Looks: one-click starting points for a layer's material.

A Look is a named combination of a material, that material's Custom knobs
(#568) and a colour -- gold is `metallic` tinted gold with its own reflection
tint and roughness. It applies to ONE layer of ONE object and writes nothing
else: no lighting, no other layer, no atom colours. That is what separates it
from the Looks removed in #566, which rewrote all four layers of the object and
changed the scene's lighting, so a look "stuck" after the material changed.

What a Look writes, for layer `<rep>` of the object:
  * `<rep>_material` -- the look's material;
  * `<rep>_material_<knob>` -- that material's Custom overrides, after clearing
    any left from before (the layer then reads "Custom (metallic)");
  * `<rep>_color` -- the layer's colour, as a named colour `look_<name>`,
    which the look (re)defines in the colour table: the one thing outside the
    layer it touches. Every layer using that Look shares it.

So it is undone the ordinary ways: Reset or picking a material clears the
knobs, and the layer's Color row (Inherit) takes the colour off.

    apply_look gold, myprotein, surface
"""
import pymol
from pymol import cmd

#: The rep stems a Look can apply to, and the per-layer colour setting of each.
LAYERS = {
    'cartoon': 'cartoon_color',
    'surface': 'surface_color',
    'stick': 'stick_color',
    'sphere': 'sphere_color',
}

#: Every Custom knob suffix (#568), cleared before a look writes its own.
KNOBS = ('reflect', 'tint', 'rough', 'knob1', 'knob2', 'knob3',
         'knob4', 'knob5', 'knob6')

#: (name, label, material, colour 0xRRGGBB, {knob: value}). The metals keep the
#: colours, tints and roughnesses of the Looks they replace; bronze is new.
LOOKS = (
    ('gold', 'Gold', 'metallic', 0xd4af37, {'tint': 0.55, 'rough': 0.25}),
    ('copper', 'Copper', 'metallic', 0xb87333, {'tint': 0.55, 'rough': 0.30}),
    ('bronze', 'Bronze', 'metallic', 0xb08d57, {'tint': 0.60, 'rough': 0.45}),
    ('steel', 'Steel', 'metallic', 0x8c9299, {'tint': 0.15, 'rough': 0.35}),
    # the mirror end: barely tinted, almost perfectly smooth
    ('chrome', 'Chrome', 'metallic', 0xdbe2e9,
     {'reflect': 0.75, 'tint': 0.10, 'rough': 0.05}),
    # statuary: white marble with faint veins; terracotta: a coarser, drier clay
    ('statuary', 'Marble (statuary)', 'marble', 0xece8df, {'knob5': 0.45}),
    ('terracotta', 'Clay (terracotta)', 'clay', 0xc0643f,
     {'knob1': 0.16, 'knob3': 0.60}),
)

_BY_NAME = {row[0]: row for row in LOOKS}


def _stem(layer):
    """`surface`, `sticks`, `stick_material` ... -> the Custom stem."""
    layer = str(layer).strip().lower()
    if layer.endswith('_material'):
        layer = layer[:-len('_material')]
    return {'sticks': 'stick', 'spheres': 'sphere'}.get(layer, layer)


def apply_look(look, object, layer, quiet=1, _self=cmd):
    """
DESCRIPTION

    "apply_look" gives one layer of one object a named look: a material, its
    Custom knobs and a colour (the named colour look_<name>). No other layer,
    object or setting changes.

USAGE

    apply_look look, object, layer

ARGUMENTS

    look = gold, copper, bronze, steel, chrome, statuary or terracotta

    object = the object

    layer = cartoon, surface, stick or sphere
    """
    look = str(look).strip().lower()
    row = _BY_NAME.get(look)
    if row is None:
        raise pymol.CmdException('unknown look %r (one of: %s)'
                                     % (look, ', '.join(_BY_NAME)))
    stem = _stem(layer)
    if stem not in LAYERS:
        raise pymol.CmdException('unknown layer %r (one of: %s)'
                                     % (layer, ', '.join(LAYERS)))
    if object not in _self.get_names('objects'):
        raise pymol.CmdException('no object named %r' % (object,))
    name, _label, material, colour, knobs = row
    q = int(quiet)
    _self.set('%s_material' % stem, material, object, quiet=q)
    for k in KNOBS:
        _self.unset('%s_material_%s' % (stem, k), object, quiet=1)
    for k, v in knobs.items():
        _self.set('%s_material_%s' % (stem, k), v, object, quiet=q)
    colour_name = 'look_%s' % name
    _self.set_color(colour_name, [((colour >> 16) & 0xff) / 255.0,
                                  ((colour >> 8) & 0xff) / 255.0,
                                  (colour & 0xff) / 255.0], quiet=1)
    _self.set(LAYERS[stem], colour_name, object, quiet=q)


def looks_payload():
    """[[name, label, material], ...] for the Inspector's Look menu."""
    return [[name, label, material] for (name, label, material, _c, _k) in LOOKS]
