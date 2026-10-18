# Calliope

Calliope is a Genesis / Mega Drive sound driver, Furnace bank converter and
desktop bank editor. It plays FM, PSG and one exclusive PCM channel, with music,
prioritized effects, arrangements and external events.

## First build

Install Python 3.10+, SGDK and SGDK's Java runtime requirement. Linux and WSL
also need native SGDK tools; see the [Linux setup](docs/integration.md).
Set `GDK` to your SGDK directory, then run from this checkout:

```text
python tools/build.py
```

Use `--sgdk PATH` to override `GDK`, `--region pal` for PAL, or `--hybrid` for
68000 sequencing with higher-rate PCM. Open `audition/out/rom.bin` in an emulator.
A replays Green Hill Zone; B stops; C plays an FM effect. UP selects Elevator,
LEFT plays PSG ring, RIGHT plays PCM, and START holds DMA stress.

For a small starting point using original synthetic assets, build
[examples/minimal](examples/minimal/README.md). New and existing SGDK projects
can integrate with one Make include: see the [consumer guide](docs/integration.md),
including migration instructions for existing Calliope consumers.
No JDK is required by this default workflow; the optional rescomp extension
still requires one to build.

## Bank editor

Install the source editor's dependencies and launch it:

```text
python -m pip install -r editor/requirements.txt
python tools/calliope_editor.py examples/bank.json
```

The editor edits bank manifests, inspects Furnace/WAV resources, validates and
compiles banks, and previews the actual driver with overlapping effects.
Windows standalone packages include Python, Qt, a preview emulator and player
ROM templates. See [editor instructions](docs/editor.md) and
[standalone instructions](docs/editor-standalone.md).

## Documentation

- [Integration and migration](docs/integration.md): one Make include, build modes and DMA.
- [Authoring](docs/authoring.md): manifests, supported Furnace behavior and sound allocation.
- [Arrangements and API](docs/arrangements.md): parts, groups, boundaries and cues.
- [Karaoke example](examples/karaoke/README.md): timed events and display synchronization.
- [Current limitations](docs/status.md) and [hybrid architecture](docs/hybrid.md).
- [Maintainer validation](docs/maintenance.md) and [release preparation](docs/releasing.md).

Pure Z80 is the default, with approximately 6.66 kHz NTSC PCM; hybrid uses
approximately 13.32 kHz. Neither mode has completed physical hardware acceptance.
Known scheduling, DAC timing and PAL stress limits remain; see the current
limitations before choosing a mode. Windows and native Linux tools under WSL
have verified ROM builds. macOS builds remain unverified.

## License and release status

Original Calliope code is [MIT licensed](LICENSE), copyright 2026 Tara McGrew.
Third-party components and supplied example assets have separate terms:
see [notices](THIRD_PARTY.md) and [asset inventory](examples/ASSETS.md).
The source and Windows editor archives are release candidates. Publication
requires resolving the items in [release preparation](docs/releasing.md).
