# App Store copy — RayMol 1.12.0

App Store Connect's "What's New in This Version" is plain text: no Markdown, 4000 characters max. Both store versions leave out the Sparkle "Check for Updates" footer, Homebrew, MCP / AI copilot (compiled out of both store builds) and Binder Design (experimental). The iOS copy makes no ray-tracing claims; confirm them on an iPad before adding any.

## Promotional text (both platforms, ≤170 characters, editable without review)

New in 1.12: materials. Make any cartoon, surface or stick gold, glass, marble or clay, and watch glass bend light around what's inside.

## Mac App Store — What's New

Materials: colour says what something is; a material says what it's made of.

• Every cartoon, surface, stick and sphere layer can now be matte, plastic, metallic, glass, frosted glass, jelly, marble, clay or rubber, per object. Your colours stay exactly as they are.
• Pick one from each layer's Material menu in the Inspector, or tune it with Custom… (reflection, tint, roughness, and more).
• Looks: Gold, Copper, Bronze, Steel, Chrome, Marble and Clay in one click.
• Side chains follow the cartoon's material automatically.
• Glass bends light: glass, frosted glass and jelly refract what's behind them through the shape of the surface.
• Reflective materials reflect the background or a studio, and with ray tracing on, the structure itself. Glass and jelly can also cast soft traced shadows.
• Materials are saved in sessions and scenes, and movies replay them.

Also in this release:
• Movies: Roll spins at a constant speed, and a camera item keeps the model that was showing when it was added.
• Fog fades to the background colour instead of black.
• Fixed: the depth-of-field aperture gave maximum blur at 0.
• Fixed: ray-traced shadows leaking between grid-mode cells.
• Fixed: GPU memory growing on every rebuild.

## iOS / iPadOS App Store — What's New

Materials: colour says what something is; a material says what it's made of.

• Every cartoon, surface, stick and sphere layer can now be matte, plastic, metallic, glass, frosted glass, jelly, marble, clay or rubber, per object. Your colours stay exactly as they are.
• Pick one from each layer's Material menu in the Inspector, or tune it with Custom….
• Looks: Gold, Copper, Bronze, Steel, Chrome, Marble and Clay in one tap.
• Side chains follow the cartoon's material automatically.
• Glass bends light: glass, frosted glass and jelly refract what's behind them through the shape of the surface.
• Materials are saved in sessions and scenes.

Fixes:
• Fixed: RayMol could be closed by iOS for running out of memory after many edits. GPU memory is now released every frame.
• A cold launch shows your last scene from the first frame, with no flash of garbage.
• Fixed: Copy Image left the busy overlay up for about a minute.
• The camera strip docks above the parked Inspector.
• Movies: Roll spins at a constant speed. Fog fades to the background colour instead of black.

## Screenshots

New shots lead; see `screenshots/macos/README.md` and `screenshots/ios/README.md` for how they were made.

- **Mac (APP_DESKTOP, 10 max):** `01-gold`, `03-marble`, `04-mixed-materials`, `05-material-controls`, then five of the ten live 1.11.2 shots. Suggested keepers: the rainbow GFP, the CPK surface, the nucleosome, the spheres with the menu open, and the DNA helix. Drop the light-theme shots and near-duplicates. Every live shot shows the console with internal Python lines, so replacing them with clean captures later is worth doing.
- **iPhone 6.9" (APP_IPHONE_67) and iPad 13" (APP_IPAD_PRO_3GEN_129):** gold, marble, mixed, controls, then the three live 1.11.1 shots (cartoon, inspector, surface).
- **Caveat:** the iOS shots come from the simulator, which has no hardware ray tracing, so the gold and mixed scenes look flatter than on a device.
