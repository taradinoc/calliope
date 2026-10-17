# Release preparation

The current version is recorded in `VERSION`.

## Build candidates

1. Follow `maintenance.md` and update `release-validation.md` with actual results.
2. Install `editor/requirements.txt`, plus CMake, Git, MSVC C++ build tools and
   the deployment tool versions selected by PySide's deploy specification.
3. Build the pinned core with `tools/build_editor_core.py --source PATH` and the
   four player templates with `tools/build_editor_player.py --sgdk PATH` when
   their source changes. The core input must be clean at the pinned revision.
4. Run `python tools/package_editor.py`. It uses a fresh staging directory and
   performs packaged converter parity and audio checks.
5. Add intended source inputs to version control, then run
   `python tools/prepare_release.py`. This exports the tracked working files,
   including reviewed uncommitted changes, rather than silently exporting an
   older revision. It does not publish or change history.

Candidates, SHA-256 checksums and an inventory of files/dependency versions are
written below `out/releases/VERSION/`. `--source-only` exports source alone.
Generated output, local editor settings, repository metadata and backups are
excluded. Distributed text is checked for developer paths and obvious private
keys. Review the inventory; automated scanning is not a substitute for review.
