# Calliope Bank Editor for Windows

Extract the entire archive to a writable directory and run `calliope_editor.exe`.
Keep its folders and DLLs together. Python, SGDK and Java are not required to
edit, validate, compile or preview banks in this package.

Open or create a bank JSON manifest, add Furnace modules or WAV effects, then
validate before exporting a bank. Asset paths are relative to the manifest.
Preview runs the actual Calliope driver with the selected NTSC/PAL and
Z80/hybrid profile. Save manifests and exports outside the application folder
if it is installed in a protected location. Furnace/WAV inspection is read-only.

The package contains no example songs. Obtain the source archive for examples
and full authoring/API instructions. Exported banks must match the driver mode
and region used by the game. Rebuild the game after changing audio resources.

The preview emulator has separate redistribution terms, including commercial
restrictions. Read `assets/THIRD_PARTY.md`, `THIRD_PARTY.md`, and the bundled
license texts before redistribution. Original Calliope code uses the MIT license
in `LICENSE`. The source archive contains the editor and player build tools;
`assets/genesis-plus-gx-source.zip` contains the preview-core source and build
modifications. Qt remains in separate shared libraries for replacement.

Known timing and hardware limitations are documented in the source archive's
`docs/status.md`. Preview results do not establish physical hardware acceptance.
