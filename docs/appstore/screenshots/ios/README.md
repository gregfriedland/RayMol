# iOS App Store screenshots — RayMol 1.12.0 (Materials)

Real captures of the running app (App Review guideline 2.3.3). No compositing, no editing beyond converting the simulator PNG to RGB (alpha dropped) and tagging it sRGB.

| File | Size | Display type | Scene |
|---|---|---|---|
| `iphone/i01-gold.png` | 1320×2868 portrait | APP_IPHONE_67 (6.9") | 1 — Gold: myoglobin 1A6M, cartoon Look → Gold, pocket side chains follow it, heme in orange |
| `iphone/i03-marble.png` | 1320×2868 | APP_IPHONE_67 | 3 — Marble: HIV-1 protease 1HSG, surface Look → Marble (statuary), MK1 in orange in the cleft |
| `iphone/i04-mixed.png` | 1320×2868 | APP_IPHONE_67 | 4 — Mixed: p53–DNA 1TSR, DNA gold, protein chains in chain colours on the same metallic cartoon, frosted-glass surface on chain B |
| `iphone/i05-controls.png` | 1320×2868 | APP_IPHONE_67 | 5 — Controls: scene 1 with the Inspector open on `mol`, Cartoon layer, Material = Custom (metallic), Look menu open with Gold highlighted, sliders underneath |
| `ipad/p01-gold.png`, `p03-marble.png`, `p04-mixed.png`, `p05-controls.png` | 2752×2064 landscape | APP_IPAD_PRO_3GEN_129 (13") | Same four scenes, landscape |

There is no scene 2: a standalone glass shot was tried (on black, then on a light grey background with the light Paper theme) and dropped, because the glass read as haze at screenshot size and the light chrome didn't match the set. Frosted glass still appears in scene 4.

Upload order for 1.12.0 (both devices): gold, marble, mixed, controls, then the three live 1.11.1 shots.

## How they were captured

- Build: iOS Simulator Debug build of `release/1.12.0` (1.12.0 build 32), `swiftui/build_ios_dd`.
- Devices: iPhone 17 Pro Max simulator (1320×2868) and iPad Pro 13-inch (M5) simulator (2064×2752 native; rotated to landscape in Device Hub, Device ▸ Rotate Left, so `simctl` writes 2752×2064). iOS/iPadOS 26.2. The simulator has no hardware ray tracing; everything here is the raster Metal path with `material_env 1`.
- Each scene is a `.pml` script (below) run at launch with
  `SIMCTL_CHILD_PYMOL_AUTOCMD="@<scene>.pml"`, plus `PYMOL_SKIP_WHATS_NEW=1`, `PYMOL_SKIP_FIRSTBOOT_THEME=1`, `PYMOL_SKIP_GESTURE_HELP=1`, and `PYMOL_AUTOCOLLAPSE=1` (full-bleed viewport) for scenes 1–4. The iPad also gets `PYMOL_AUTOLANDSCAPE=left`. Structures were loaded from local mmCIF files (1A6M, 1STP, 1HSG, 1TSR from RCSB).
- Status bar: `xcrun simctl status_bar <udid> override --time 9:41 --batteryState charged --batteryLevel 100 --cellularBars 4 --wifiBars 3` before every capture. The iPad status bar also shows the date, which `simctl` cannot override.
- Capture: `xcrun simctl io <udid> screenshot <file>` about 10–12 s after launch.
- Scene 5: no collapse. The panel was opened and `mol` expanded by taps; the Look chip was then tapped, and the menu was captured while a finger was held on **Gold**, so the row is highlighted. Releasing it re-applies Gold, which the object already had.
- The camera is set in each script. It is deterministic for a given device and orientation, but the iPhone and iPad scripts differ in their final `turn` / `move` lines, because a portrait and a landscape viewport frame differently.

## Scene scripts

`<pdb-dir>` is wherever the four `.cif` files live.

### Scene 1

```
load <pdb-dir>/1A6M.cif, mol
bg_color black
set material_env, 1
remove solvent
hide everything
show cartoon
apply_look gold, mol, cartoon
show sticks, (byres (polymer within 5 of resn HEM)) and not name N+C+O
show sticks, resn HEM
util.cnc mol
color orange, resn HEM and elem C
set stick_radius, 0.3
orient resn HEM
turn x, 75
center resn HEM
zoom resn HEM, 12
```

iPad (landscape) differs:

```diff
+ move x, 7
```

### Scene 3

```
load <pdb-dir>/1HSG.cif, mol
bg_color black
set material_env, 1
remove solvent
hide everything
show surface, polymer
apply_look statuary, mol, surface
show sticks, resn MK1
color orange, resn MK1 and elem C
util.cnc resn MK1
set stick_radius, 0.3
orient resn MK1
turn y, 90
zoom mol, 2
move z, -20
```

iPad (landscape) differs:

```diff
- zoom mol, 2
- move z, -20
+ turn z, 90
+ zoom mol, 0
+ move z, 5
```

### Scene 4

```
load <pdb-dir>/1TSR.cif, mol
bg_color black
set material_env, 1
remove solvent
hide everything
show cartoon
apply_look gold, mol, cartoon
util.cbc mol and polymer.protein
show surface, chain B and polymer.protein
set surface_material, frosted_glass, mol
set surface_color, white, mol
orient mol
turn z, 90
zoom mol, 0
move z, -85
```

iPad (landscape) differs:

```diff
- turn z, 90
- move z, -85
+ move z, -18
```
