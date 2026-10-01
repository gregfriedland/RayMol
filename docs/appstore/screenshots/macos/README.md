# RayMol — App Store screenshots

## macOS (2560×1600, App Store-accepted size)

### 1.12.0 materials set
Real captures of the running Mac App Store-flavour Release build
(`RAYMOL_MAS_RESTRICTED`: no Sparkle, no MCP), window at 1280×800 pt on a 2×
Retina display, live Metal ray tracing + studio environment
(`material_env 1`). Scenes 1–4 were set up entirely by `PYMOL_AUTOCMD`
scripts (no mouse/keyboard input); scene 5's inspector row and Look menu were
opened through the Accessibility API. The only post-processing is flattening
the window's rounded-corner transparency to opaque RGB (App Store Connect
rejects alpha). Scene spec is shared with the iOS set.

- `01-gold.png` — myoglobin (1A6M) heme pocket on black: cartoon Look → Gold
  with the pocket side chains following it; heme as copper sticks, carbons
  orange.
- `03-marble.png` — HIV-1 protease + MK1 inhibitor (1HSG) on black: surface
  Look → Marble (statuary); inhibitor as orange sticks in the cleft.
- `04-mixed-materials.png` — p53 core domain on DNA (1TSR) on black: DNA
  cartoon Look → Gold; chains B/C plastic cartoon; chain A metallic cartoon
  under a frosted-glass surface. (Materials are per object, so the complex is
  split into dna / p53_core / p53_A objects.)
- `05-material-controls.png` — scene 1 with the Inspector open on myoglobin's
  Cartoon layer: Material reads Custom (metallic) with its sliders, and the
  Look menu is open with Gold highlighted.

There is no `02`: a standalone glass shot was tried (on black, then on light
grey) and dropped, because the glass read as haze at screenshot size. Frosted
glass still appears in `04`.

Upload order for 1.12.0: `01-gold`, `03-marble`, `04-mixed-materials`,
`05-material-controls`, then five of the live 1.11.2 shots.

### Earlier set (IL-2, ray tracing)
Captured from the signed Release build (single-window, Screen Recording) and
padded to 2560×1600. Bundled IL-2 (interleukin-2), real-time hardware ray
tracing.

- `01-cartoon-raytrace.png` — rainbow cartoon, ray-traced AO + shadows
- `02-surface.png` — molecular surface, ray-traced
- `03-spheres.png` — CPK spheres (analytic impostors), ray-traced
- `04-sticks.png` — sticks, by element, on white

## iPad 13" / iPhone 6.9"
See `../ios/README.md`.
