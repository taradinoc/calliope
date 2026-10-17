# Third-party notices

The MIT license at the project root covers original Calliope code and original
synthetic fixtures. It does not relicense the following components or supplied
music, samples, lyrics and artwork.

- `tools/libretro_host.py` was adapted from BonkGen's `tools/libretro_host.py`;
  its game-start helper was removed. Preserve this attribution. The originating
  harness license/permission must be confirmed before public distribution.
- SGDK supplies the SDK and library code linked into ROMs. Its MIT notice is
  retained in `editor/assets/SGDK-LICENSE.txt`. Calliope generates a local DMA
  wrapper around the user's configured SDK; it does not modify the installation.
- The editor bundles Genesis Plus GX at revision
  `393f70cd64c7334564c19a424c383f53986c13f4`, with a read-only Z80 RAM export.
  Preserve `editor/assets/GENESIS-PLUS-GX-LICENSE.txt` and
  `editor/assets/genesis-plus-gx-source.zip`. Its terms restrict commercial
  redistribution. See `editor/THIRD_PARTY.md` for component details.
- The desktop editor uses PySide6/Qt and Shiboken. Preserve the licenses of the
  exact packaged wheels, shared-library replacement capability, and applicable
  corresponding-source provisions. See the editor notices and release checklist.
- Furnace format and playback documentation informed the independent converter.
  Furnace is not linked into the driver or editor. Optional oracle tests invoke
  a separately installed Furnace executable.

[Example asset inventory](examples/ASSETS.md) records known origins and missing
permissions. Unknown authorship or permission is a publication blocker, not an
implicit grant under the project's MIT license.
