"""Launch Calliope Bank Editor, or its isolated worker in a frozen release."""

from pathlib import Path
import os
import sys


def main():
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        os.environ["CALLIOPE_EDITOR_EXECUTABLE"] = str(Path(sys.argv[0]).resolve())
        # The converter runs under bundled CPython, without native translation.
        # Nuitka's frozen package path does not include sibling source modules.
        import calliope
        from importlib.machinery import (
            PathFinder,
            FileFinder,
            SourceFileLoader,
            SOURCE_SUFFIXES,
        )

        converter_path = str(Path(__file__).resolve().parent / "calliope")
        if converter_path not in calliope.__path__:
            calliope.__path__.append(converter_path)
        sys.path_importer_cache[converter_path] = FileFinder(
            converter_path, (SourceFileLoader, SOURCE_SUFFIXES)
        )
        if PathFinder not in sys.meta_path:
            sys.meta_path.append(PathFinder)
    if "--worker" in sys.argv:
        from calliope.editor.worker import run

        run()
        return
    # A checkout can use project-local dependencies without installing globally.
    local = Path(__file__).resolve().parents[1] / "out/editor-deps"
    if local.is_dir():
        sys.path.insert(0, str(local))
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        raise SystemExit(
            "Install editor dependencies: python -m pip install -r editor/requirements.txt"
        ) from e
    from calliope.editor.window import EditorWindow

    app = QApplication(sys.argv)
    app.setOrganizationName("Calliope")
    app.setApplicationName("Calliope Bank Editor")
    app.setStyle("Fusion")
    window = EditorWindow()
    window.show()
    if len(sys.argv) > 1:
        window.open_path(Path(sys.argv[1]))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
