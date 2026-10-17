"""Desktop bank authoring, compilation and actual-driver audition UI."""

import base64
import copy
import json
import os
from pathlib import Path
import sys
from PySide6.QtCore import Qt, QProcess, QSettings, QTimer, QStandardPaths
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QFont
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QTabWidget,
    QPlainTextEdit,
    QPushButton,
    QLabel,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QInputDialog,
    QGroupBox,
    QFormLayout,
    QDialog,
    QDialogButtonBox,
    QAbstractItemView,
    QProgressBar,
)
from .audio import AudioOutput
from .document import Document
from .properties import Properties


def assets_path():
    candidates = (
        [Path(os.environ["CALLIOPE_EDITOR_ASSETS"])]
        if os.environ.get("CALLIOPE_EDITOR_ASSETS")
        else []
    )
    candidates += [
        Path(sys.argv[0]).resolve().parent / "assets",
        Path(sys.argv[0]).resolve().parent.parent / "Resources/assets",
        Path(__file__).resolve().parents[3] / "editor/assets",
    ]
    return next((p for p in candidates if p.is_dir()), candidates[-1])


class EditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1280, 880)
        self.document = Document()
        self.settings = QSettings()
        self.jobs = []
        self.player = None
        self.player_revision = None
        self.player_profile = None
        self.pending_command = None
        self.token = 0
        self.last_ack = 0
        self.busy = False
        self.inspect_target = None
        self.loading = False
        self.music_id = None
        self.setStyleSheet(
            "QTreeWidget,QPlainTextEdit {font-size:12px;} QGroupBox {font-weight:600;margin-top:8px;padding-top:14px;} QPushButton {padding:5px 10px;} QTabWidget::pane {border:1px solid #aeb8c2;}"
        )
        self.audio = AudioOutput(self)
        self.audio.problem.connect(lambda s: self.statusBar().showMessage(s, 10000))
        self.menu()
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel("CALLIOPE  /  BANK EDITOR")
        title.setStyleSheet("font-size:20px;font-weight:700;padding:8px;color:#146b72;")
        layout.addWidget(title)
        split = QSplitter()
        layout.addWidget(split, 1)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 6, 0)
        split.addWidget(left)
        self.browser = QTreeWidget()
        self.browser.setHeaderLabels(["Bank resources"])
        self.browser.currentItemChanged.connect(self.selected)
        ll.addWidget(self.browser)
        for text, callback in [
            ("Import Furnace / WAV…", self.import_resource),
            ("Add empty entry", self.add_empty),
            ("Remove entry", self.remove_resource),
            ("Move entry up", lambda: self.move_resource(-1)),
            ("Move entry down", lambda: self.move_resource(1)),
        ]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            ll.addWidget(button)
        self.tabs = QTabWidget()
        split.addWidget(self.tabs)
        split.setSizes([260, 980])
        self.properties = Properties()
        self.properties.changed.connect(self.properties_changed)
        self.tabs.addTab(self.properties, "Properties")
        source = QWidget()
        sl = QVBoxLayout(source)
        bar = QHBoxLayout()
        sl.addLayout(bar)
        for text, callback in [
            ("Inspect resource", self.inspect_resource),
            ("Locate / relink…", self.relink),
            ("Suggest voice mappings…", self.suggest_voices),
        ]:
            b = QPushButton(text)
            b.clicked.connect(callback)
            bar.addWidget(b)
        self.inspection = QPlainTextEdit()
        self.inspection.setReadOnly(True)
        self.inspection.setPlaceholderText(
            "Inspect a Furnace or WAV resource to view parsed metadata, patterns and conversion details."
        )
        sl.addWidget(self.inspection)
        self.tabs.addTab(source, "Read-only source")
        json_tab = QWidget()
        jl = QVBoxLayout(json_tab)
        self.json = QPlainTextEdit()
        self.json.setFont(QFont("Consolas", 10))
        jl.addWidget(self.json)
        apply = QPushButton("Apply JSON to bank")
        apply.clicked.connect(self.apply_json)
        jl.addWidget(apply)
        self.tabs.addTab(json_tab, "Bank JSON")
        self.json.textChanged.connect(self.json_modified)
        self.tabs.currentChanged.connect(self.tab_changed)
        playback = QGroupBox("Genesis audition")
        pl = QVBoxLayout(playback)
        layout.addWidget(playback)
        transport = QHBoxLayout()
        pl.addLayout(transport)
        self.region = QComboBox()
        self.region.addItems(["NTSC", "PAL"])
        transport.addWidget(self.region)
        self.mode = QComboBox()
        self.mode.addItems(["Pure Z80", "Hybrid · ROM PCM"])
        transport.addWidget(self.mode)
        for text, callback in [
            ("Play song", self.play_song),
            ("Pause", lambda: self.command([2])),
            ("Resume", lambda: self.command([3])),
            ("Stop music", lambda: self.command([4])),
            ("Stop all", lambda: self.command([5])),
            ("Close preview", self.stop_preview),
        ]:
            b = QPushButton(text)
            b.clicked.connect(callback)
            transport.addWidget(b)
        self.region.currentIndexChanged.connect(self.profile_changed)
        self.mode.currentIndexChanged.connect(self.profile_changed)
        row = QHBoxLayout()
        pl.addLayout(row)
        row.addWidget(QLabel("SFX"))
        self.sfx = QComboBox()
        self.sfx.setMinimumWidth(180)
        row.addWidget(self.sfx, 1)
        self.priority = QSpinBox()
        self.priority.setRange(0, 255)
        self.priority.setValue(1)
        row.addWidget(QLabel("Priority"))
        row.addWidget(self.priority)
        self.sfx.currentIndexChanged.connect(self.sfx_selected)
        for text, callback in [
            ("Trigger [Space]", self.trigger_sfx),
            ("Cancel token", self.cancel_token),
        ]:
            b = QPushButton(text)
            b.clicked.connect(callback)
            row.addWidget(b)
        self.token_label = QLabel("Token: —")
        row.addWidget(self.token_label)
        controls = QHBoxLayout()
        pl.addLayout(controls)
        self.arrangement = QComboBox()
        self.arrangement.setMinimumWidth(120)
        controls.addWidget(self.arrangement)
        self.boundary = QComboBox()
        self.boundary.addItems(["Immediate", "Next bar", "Next marker"])
        controls.addWidget(self.boundary)
        self.marker = QSpinBox()
        self.marker.setRange(0, 65535)
        controls.addWidget(QLabel("Marker"))
        controls.addWidget(self.marker)
        b = QPushButton("Switch arrangement")
        b.clicked.connect(self.switch_arrangement)
        controls.addWidget(b)
        b = QPushButton("Mixer / part controls…")
        b.clicked.connect(self.mixer)
        controls.addWidget(b)
        self.preview_label = QLabel("Preview stopped")
        pl.addWidget(self.preview_label)
        self.progress_label = QLabel()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setMaximumHeight(8)
        self.cancel_job = QPushButton("Cancel")
        self.cancel_job.clicked.connect(self.cancel_operation)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress_label, 1)
        progress_row.addWidget(self.cancel_job)
        pl.addLayout(progress_row)
        pl.addWidget(self.progress_bar)
        self.set_progress(None)
        self.ownership = QLabel("FM1–5  ·  PSG1–3  ·  Noise  ·  PCM")
        self.ownership.setWordWrap(True)
        pl.addWidget(self.ownership)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(155)
        self.diagnostics.setPlaceholderText("Validation and build results")
        layout.addWidget(self.diagnostics)
        QShortcut(QKeySequence("Space"), self, activated=self.trigger_sfx)
        self.watcher = QTimer(self)
        self.watcher.setInterval(2000)
        self.watcher.timeout.connect(self.check_sources)
        self.watcher.start()
        self.source_stats = {}
        self.rebuild()
        self.statusBar().showMessage("Ready · source resources are read only")

    def menu(self):
        file = self.menuBar().addMenu("&File")
        edit = self.menuBar().addMenu("&Edit")
        build = self.menuBar().addMenu("&Build")

        def action(menu, name, shortcut, callback):
            a = QAction(name, self)
            a.triggered.connect(callback)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            menu.addAction(a)

        for name, key, callback in [
            ("New", "Ctrl+N", self.new),
            ("Open…", "Ctrl+O", self.open),
            ("Save", "Ctrl+S", self.save),
            ("Save as…", "Ctrl+Shift+S", lambda: self.save(True)),
            ("Exit", "Ctrl+Q", self.close),
        ]:
            action(file, name, key, callback)
        action(edit, "Undo", "Ctrl+Z", lambda: self.history(-1))
        action(edit, "Redo", "Ctrl+Shift+Z", lambda: self.history(1))
        action(build, "Validate", "F6", lambda: self.start_job("validate"))
        action(build, "Compile / export…", "F7", self.export)
        help_menu = self.menuBar().addMenu("&Help")
        action(help_menu, "About / dependency notices", None, self.about)

    def profile(self):
        return ("ntsc", "pal")[self.region.currentIndex()], ("z80", "hybrid")[
            self.mode.currentIndex()
        ]

    def selection(self):
        item = self.browser.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def entry(self):
        selection = self.selection()
        if selection and selection[0] in ("songs", "sfx") and selection[1] is not None:
            return self.document.data[selection[0]][selection[1]]
        return None

    def rebuild(self, selection=None):
        selection = selection or self.selection() or ("bank", None)
        self.loading = True
        self.browser.clear()
        bank = QTreeWidgetItem(
            self.browser,
            [self.document.path.name if self.document.path else "Untitled bank"],
        )
        bank.setData(0, Qt.ItemDataRole.UserRole, ("bank", None))
        chosen = bank
        for cat, title in [("songs", "Music"), ("sfx", "Sound effects")]:
            group = QTreeWidgetItem(bank, [title])
            group.setData(0, Qt.ItemDataRole.UserRole, (cat, None))
            for i, entry in enumerate(self.document.data.get(cat, [])):
                item = QTreeWidgetItem(
                    group, [f"{entry.get('id','?'):}  {entry.get('name','unnamed')}"]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, (cat, i))
                if selection == (cat, i):
                    chosen = item
        self.browser.expandAll()
        self.browser.setCurrentItem(chosen)
        self.loading = False
        self.selected()
        old = self.sfx.currentData()
        self.sfx.blockSignals(True)
        self.sfx.clear()
        for i, item in enumerate(self.document.data.get("sfx", [])):
            self.sfx.addItem(str(item.get("name", "?")), i)
        if old is not None:
            self.sfx.setCurrentIndex(max(0, self.sfx.findData(old)))
        self.sfx.blockSignals(False)
        self.sfx_selected()
        self.refresh_title()
        self.json.blockSignals(True)
        self.json.setPlainText(
            json.dumps(self.document.data, indent=2, ensure_ascii=False)
        )
        self.json.document().setModified(False)
        self.json.blockSignals(False)

    def refresh_title(self):
        self.setWindowTitle(
            ("● " if self.document.dirty or self.json.document().isModified() else "")
            + (str(self.document.path) if self.document.path else "Untitled bank")
            + " — Calliope"
        )

    def selected(self, *_):
        if self.loading:
            return
        selection = self.selection()
        entry = self.entry()
        self.inspection.clear()
        if entry is not None:
            self.properties.load(entry, selection[0], self.document.base_dir)
        else:
            self.properties.load(
                {
                    k: v
                    for k, v in self.document.data.items()
                    if k not in ("songs", "sfx")
                },
                "bank",
                self.document.base_dir,
            )
        self.arrangement.clear()
        if selection and selection[0] == "songs" and entry:
            arrangements = entry.get("arrangements", {})
            if isinstance(arrangements, dict):
                self.arrangement.addItems([str(n) for n in arrangements])

    def properties_changed(self, value):
        data = copy.deepcopy(self.document.data)
        selection = self.selection()
        if self.entry() is not None:
            data[selection[0]][selection[1]] = value
        else:
            for key in list(data):
                if key not in ("songs", "sfx"):
                    del data[key]
            data.update(value)
        self.replace(data)

    def replace(self, value, selection=None):
        self.document.replace(value)
        self.rebuild(selection)
        if self.player:
            self.preview_label.setText(
                f"Playing revision {self.player_revision}; bank has changed. Play/Trigger rebuilds preview."
            )

    def json_modified(self):
        self.refresh_title()

    def tab_changed(self, index):
        if index != 2 and self.json.document().isModified() and not self.apply_json():
            self.tabs.setCurrentIndex(2)

    def apply_json(self):
        try:
            value = json.loads(self.json.toPlainText())
            if not isinstance(value, dict):
                raise ValueError("Bank JSON must be an object")
            for cat in ("songs", "sfx"):
                if not isinstance(value.get(cat, []), list) or any(
                    not isinstance(i, dict) for i in value.get(cat, [])
                ):
                    raise ValueError(cat + " must be an array of objects")
            self.replace(value)
            return True
        except ValueError as e:
            QMessageBox.warning(self, "Invalid JSON", str(e))
            return False

    def flush_json(self):
        return not self.json.document().isModified() or self.apply_json()

    def history(self, delta):
        if not self.flush_json():
            return
        self.document.travel(delta)
        self.rebuild()

    def may_close(self):
        if not self.flush_json():
            return False
        if not self.document.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved bank",
            "Save changes to this bank?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        return (
            self.save()
            if answer == QMessageBox.StandardButton.Save
            else answer == QMessageBox.StandardButton.Discard
        )

    def new(self):
        if self.may_close():
            self.stop_preview()
            self.music_id = None
            self.document = Document()
            self.rebuild()

    def open(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open bank",
            self.settings.value("last_dir", str(Path.cwd())),
            "Calliope manifests (*.json)",
        )
        if path:
            self.open_path(Path(path))

    def open_path(self, path):
        if not self.may_close():
            return
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or any(
                not isinstance(value.get(c, []), list)
                or any(not isinstance(i, dict) for i in value.get(c, []))
                for c in ("songs", "sfx")
            ):
                raise ValueError("Expected a bank manifest object with resource arrays")
            self.stop_preview()
            self.music_id = None
            self.document = Document(value, path)
            self.settings.setValue("last_dir", str(path.parent))
            self.rebuild()
            self.check_sources()
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Open failed", str(e))

    def save(self, save_as=False):
        if not self.flush_json():
            return False
        path = self.document.path
        if save_as or not path:
            name, _ = QFileDialog.getSaveFileName(
                self,
                "Save bank",
                str(path or self.document.base_dir / "bank.json"),
                "JSON manifest (*.json)",
            )
            if not name:
                return False
            path = Path(name)
        try:
            self.document.save(path)
            self.rebuild()
            return True
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return False

    def add_empty(self):
        if not self.flush_json():
            return
        cat = "sfx" if self.selection() and self.selection()[0] == "sfx" else "songs"
        data = copy.deepcopy(self.document.data)
        entries = data.setdefault(cat, [])
        used = {i.get("id") for i in entries}
        n = next((n for n in range(65535) if n not in used), None)
        if n is None:
            return
        names = {i.get("name") for c in ("songs", "sfx") for i in data.get(c, [])}
        name = f'{"song" if cat=="songs" else "effect"}_{n}'
        while name in names:
            name += "_"
        entries.append(
            dict(
                id=n,
                name=name,
                source="",
                **({"voices": []} if cat == "songs" else {"priority": 1}),
            )
        )
        self.replace(data, (cat, len(entries) - 1))

    def remove_resource(self):
        if not self.flush_json():
            return
        if self.entry() is None:
            return
        cat, index = self.selection()
        data = copy.deepcopy(self.document.data)
        del data[cat][index]
        self.replace(data, ("bank", None))

    def move_resource(self, delta):
        if not self.flush_json():
            return
        if self.entry() is None:
            return
        cat, index = self.selection()
        data = copy.deepcopy(self.document.data)
        entries = data[cat]
        other = index + delta
        if 0 <= other < len(entries):
            entries[index], entries[other] = entries[other], entries[index]
            self.replace(data, (cat, other))

    def relative(self, path):
        try:
            return Path(os.path.relpath(path, self.document.base_dir)).as_posix()
        except ValueError:
            return Path(path).as_posix()

    def relink(self):
        if not self.flush_json():
            return
        if self.entry() is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Locate resource",
            str(self.document.base_dir),
            "Audio resources (*.fur *.wav)",
        )
        if path:
            cat, index = self.selection()
            data = copy.deepcopy(self.document.data)
            data[cat][index]["source"] = self.relative(path)
            self.replace(data)

    def import_resource(self):
        if not self.flush_json():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import resource",
            str(self.document.base_dir),
            "Audio resources (*.fur *.wav)",
        )
        if not path:
            return
        cat = (
            "sfx"
            if Path(path).suffix.lower() == ".wav"
            or self.selection()
            and self.selection()[0] == "sfx"
            else "songs"
        )
        self.inspect_target = ("import", path, cat)
        self.start_job("inspect", source=path, subsong=0)

    def inspect_resource(self):
        if not self.entry():
            return
        item = self.entry()
        self.inspect_target = ("inspect", self.selection(), None)
        self.start_job(
            "inspect",
            source=str(self.document.base_dir / item.get("source", "")),
            subsong=item.get("subsong", 0),
            gain=item.get("gain", 1),
            loop=(
                item.get("loop")
                if Path(item.get("source", "")).suffix.lower() == ".wav"
                else None
            ),
        )

    def suggest_voices(self):
        if self.selection() and self.selection()[0] == "songs" and self.entry():
            self.inspect_target = ("suggest", self.selection(), None)
            self.start_job(
                "inspect",
                source=str(self.document.base_dir / self.entry().get("source", "")),
                subsong=self.entry().get("subsong", 0),
            )

    def review_import(self, result, target):
        if target[0] == "inspect":
            self.inspection.setPlainText(
                json.dumps(result, indent=2, ensure_ascii=False)
            )
            return
        if target[0] == "suggest":
            suggestions = result.get("voice_suggestions", [])
            if self.review("Review voice mappings", suggestions):
                cat, index = target[1]
                data = copy.deepcopy(self.document.data)
                data[cat][index]["voices"] = suggestions
                self.replace(data, (cat, index))
            return
        path, cat = target[1:]
        self.add_empty_for_import(cat)
        entry = copy.deepcopy(self.entry())
        entry["source"] = self.relative(path)
        # Keep generated valid identifiers; the source title is visible in inspection.
        if cat == "songs":
            entry["voices"] = result.get("voice_suggestions", [])
        if self.review("Review imported bank entry", entry):
            data = copy.deepcopy(self.document.data)
            category, index = self.selection()
            data[category][index] = entry
            self.replace(data)
            self.inspection.setPlainText(
                json.dumps(result, indent=2, ensure_ascii=False)
            )
        else:
            self.history(-1)

    def add_empty_for_import(self, cat):
        # Select the target category before using the common ID/name allocator.
        root = self.browser.topLevelItem(0)
        self.browser.setCurrentItem(root.child(0 if cat == "songs" else 1))
        self.add_empty()

    def review(self, title, value):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(900, 620)
        layout = QVBoxLayout(dialog)
        label = QLabel(
            "Review the proposed settings. Source files will stay read only. You can change these settings in Properties."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        form = Properties()
        layout.addWidget(form)
        voices_only = isinstance(value, list)
        proposal = (
            copy.deepcopy(self.entry() or {}) if voices_only else copy.deepcopy(value)
        )
        if voices_only:
            proposal["voices"] = copy.deepcopy(value)
        category = (
            self.selection()[0]
            if self.selection() and self.selection()[0] in ("songs", "sfx")
            else "songs"
        )
        form.load(proposal, category, self.document.base_dir)
        if voices_only:
            form.tabs.setCurrentIndex(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted:
            if voices_only:
                value[:] = copy.deepcopy(form.value.get("voices", []))
            else:
                value.clear()
                value.update(copy.deepcopy(form.value))
        return accepted

    def export(self):
        if not self.flush_json():
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Compile bank",
            str(self.document.base_dir / "build/bank.bin"),
            "Calliope bank (*.bin)",
        )
        if path:
            self.start_job("export", output=path)

    def start_job(self, action, **extra):
        if not self.flush_json():
            return
        if self.busy:
            self.statusBar().showMessage("A resource operation is still running", 5000)
            return
        self.check_sources()
        if action == "preview":
            self.stop_preview()
        self.busy = True
        region, mode = self.profile()
        self.set_progress(
            "Preparing Genesis preview…"
            if action == "preview"
            else action.capitalize() + "…"
        )
        request = dict(
            action=action,
            document=self.document.data,
            base_dir=str(self.document.base_dir),
            document_path=str(self.document.path) if self.document.path else None,
            revision=self.document.revision,
            region=region,
            mode=mode,
            assets=str(assets_path()),
            cache_dir=str(
                Path(
                    QStandardPaths.writableLocation(
                        QStandardPaths.StandardLocation.CacheLocation
                    )
                )
                / "banks"
            ),
            **extra,
        )
        process = QProcess(self)
        process.buffer = b""
        process.request = request
        process.target = self.inspect_target
        entry = Path(sys.argv[0]).resolve()
        if os.environ.get("CALLIOPE_EDITOR_EXECUTABLE"):
            program = os.environ["CALLIOPE_EDITOR_EXECUTABLE"]
            args = ["--worker"]
        else:
            program = sys.executable
            args = [
                str(Path(__file__).resolve().parents[2] / "calliope_editor.py"),
                "--worker",
            ]
        self.jobs.append(process)
        process.readyReadStandardOutput.connect(lambda: self.read_worker(process))
        process.readyReadStandardError.connect(lambda: self.worker_stderr(process))
        process.finished.connect(
            lambda code, status: self.worker_finished(process, code)
        )
        process.errorOccurred.connect(lambda error: self.worker_error(process, error))
        process.started.connect(
            lambda: process.write((json.dumps(request) + "\n").encode("utf-8"))
        )
        process.start(program, args)
        self.statusBar().showMessage(action.capitalize() + "…")

    def read_worker(self, process):
        process.buffer += bytes(process.readAllStandardOutput())
        while b"\n" in process.buffer:
            line, process.buffer = process.buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                self.worker_message(process, json.loads(line))
            except (ValueError, KeyError, TypeError, RuntimeError) as e:
                self.diagnostics.appendPlainText("Worker protocol error: " + str(e))

    def worker_stderr(self, process):
        text = bytes(process.readAllStandardError()).decode("utf-8", "replace").strip()
        if text:
            self.diagnostics.appendPlainText(text)

    def worker_message(self, process, value):
        typ = value["type"]
        if typ == "progress":
            if process in self.jobs:
                self.set_progress(value["message"])
            return
        if typ == "audio":
            if process is self.player:
                self.audio.append(base64.b64decode(value["data"]))
                if "status" in value:
                    self.show_status(value["status"])
            return
        request = process.request
        self.check_sources()
        stale = (
            request["revision"] != self.document.revision
            or (request["region"], request["mode"]) != self.profile()
            or request["base_dir"] != str(self.document.base_dir)
            or json.dumps(request["document"]) != json.dumps(self.document.data)
        )
        if stale:
            if typ == "ready":
                process.kill()
            self.statusBar().showMessage(
                "Discarded a result for an earlier document revision", 6000
            )
            return
        if typ == "ready":
            self.set_progress(None)
            self.player = process
            self.busy = False
            self.player_revision = request["revision"]
            self.player_profile = self.profile()
            try:
                self.audio.start(value["rate"])
            except RuntimeError as e:
                self.stop_preview()
                self.diagnostics.setPlainText(str(e))
                return
            self.preview_label.setText(
                f"Playing bank revision {self.player_revision} · {self.region.currentText()} · {self.mode.currentText()}"
            )
            self.diagnostics.setPlainText(self.report(value["report"]))
            if self.pending_command:
                for command in self.pending_command:
                    self.send_command(command)
                self.pending_command = None
        elif typ == "inspection":
            self.review_import(value["result"], process.target)
        elif typ == "diagnostics":
            self.diagnostics.setPlainText(
                "\n\n".join(
                    f"{d['code']} · {d.get('field') or 'bank'} · {d.get('source') or ''}\n{d['message']}"
                    for d in value["diagnostics"]
                )
            )
            self.pending_command = None
        elif typ == "validated":
            self.diagnostics.setPlainText(
                "Compatible with Calliope for the selected region and driver mode."
            )
            self.statusBar().showMessage("Validation passed")
        elif typ == "exported":
            self.diagnostics.setPlainText(
                "Exported " + request["output"] + "\n" + self.report(value["report"])
            )
            self.statusBar().showMessage("Compilation complete")
        elif typ == "error":
            self.diagnostics.setPlainText(value["message"])
            self.pending_command = None
            self.statusBar().showMessage("Operation failed")
        elif typ == "allocation":
            self.diagnostics.appendPlainText(value["message"])

    def report(self, report):
        return (
            f"{report['format']} · {report['bytes']:,} bytes · {report['songs']} songs · {len(report['effects'])} effects · {report['patches']} patches\n"
            + json.dumps(
                {
                    k: report.get(k)
                    for k in (
                        "driver_mode",
                        "pcm_timer",
                        "music_samples",
                        "effects",
                        "dependencies",
                    )
                },
                indent=2,
            )
        )

    def worker_error(self, process, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.busy = False
            self.set_progress(None)
            self.diagnostics.appendPlainText(
                "Worker could not start: " + process.errorString()
            )

    def worker_finished(self, process, code):
        self.read_worker(process)
        if process in self.jobs:
            self.jobs.remove(process)
        if process is self.player:
            self.player = None
            self.audio.stop()
            self.preview_label.setText("Preview stopped")
        if process.request["action"] != "preview" or self.player is None:
            self.busy = False
        if not self.busy:
            self.set_progress(None)
        if code and not getattr(process, "cancelled", False):
            self.diagnostics.appendPlainText(f"Worker exited with code {code}")
        process.deleteLater()

    def set_progress(self, message):
        self.progress_label.setText(message or "")
        self.progress_bar.setVisible(bool(message))
        self.cancel_job.setVisible(bool(message))
        if message:
            self.statusBar().showMessage(message)

    def cancel_operation(self):
        for process in list(self.jobs):
            if process is not self.player:
                process.cancelled = True
                process.kill()
        self.pending_command = None
        self.busy = False
        self.set_progress(None)
        self.statusBar().showMessage("Operation cancelled")

    def profile_changed(self, *_):
        if self.busy:
            self.cancel_operation()
        self.stop_preview()
        self.statusBar().showMessage(
            "Playback profile changed; the next preview recompiles the bank"
        )

    def play_song(self):
        if not self.flush_json():
            return
        entry = self.entry()
        selection = self.selection()
        if entry is None or selection[0] != "songs":
            self.statusBar().showMessage("Select a song to play", 5000)
            return
        self.music_id = entry.get("id", 0)
        self.command([1, self.music_id], rebuild=True)

    def sfx_selected(self, *_):
        index = self.sfx.currentData()
        if index is not None:
            priority = self.document.data["sfx"][index].get("priority", 1)
            self.priority.setValue(priority if type(priority) == int else 1)

    def trigger_sfx(self):
        if not self.flush_json():
            return
        index = self.sfx.currentData()
        if index is None:
            return
        item = self.document.data["sfx"][index]
        self.token = self.token % 65535 + 1
        self.token_label.setText(f"Token: {self.token}")
        self.command(
            [
                7 if Path(str(item.get("source", ""))).suffix.lower() == ".wav" else 6,
                item.get("id", 0),
                self.priority.value(),
                self.token,
            ],
            rebuild=True,
        )

    def cancel_token(self):
        token, ok = QInputDialog.getInt(
            self, "Cancel SFX", "Token", self.token, 0, 65535
        )
        if ok:
            self.command([8, token])

    def command(self, command, rebuild=False):
        if not self.flush_json():
            return
        self.check_sources()
        stale = (
            self.player_revision != self.document.revision
            or self.player_profile != self.profile()
        )
        if rebuild and (not self.player or stale):
            if self.busy:
                self.statusBar().showMessage(
                    "Wait for the current resource operation", 5000
                )
                return
            self.pending_command = (
                [[1, self.music_id]]
                if command[0] in (6, 7) and self.music_id is not None
                else []
            ) + [command]
            self.start_job("preview")
            return
        if command[0] in (4, 5):
            self.music_id = None
        if self.player:
            self.send_command(command)
        else:
            self.statusBar().showMessage(
                "Start a song or trigger an effect to open preview", 5000
            )

    def send_command(self, command):
        if self.player:
            self.player.write((json.dumps({"command": command}) + "\n").encode())

    def switch_arrangement(self):
        if self.arrangement.currentIndex() >= 0:
            self.command(
                [
                    9,
                    self.arrangement.currentIndex(),
                    (1, 8, 16)[self.boundary.currentIndex()],
                    self.marker.value(),
                ]
            )

    def mixer(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Music mixer and part controls")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        layout.addLayout(form)

        def control(label, maximum, default, callback):
            spin = QSpinBox()
            spin.setRange(0, maximum)
            spin.setValue(default)
            form.addRow(label, spin)
            spin.valueChanged.connect(callback)
            return spin

        fm = control(
            "FM volume", 127, 127, lambda v: self.command([12, v, psg.value()])
        )
        psg = control("PSG volume", 15, 15, lambda v: self.command([12, fm.value(), v]))
        control("Music PCM priority", 255, 0, lambda v: self.command([13, v]))
        control("Muted group bitmask", 65535, 0, lambda v: self.command([10, v]))
        control(
            "Muted physical channel bitmask", 1023, 0, lambda v: self.command([11, v])
        )
        tempo = control("Tempo (%)", 200, 100, lambda v: None)
        tempo.setMinimum(50)
        tempo.setSingleStep(10)
        tempo_button = QPushButton("Apply tempo at selected boundary")
        tempo_button.clicked.connect(
            lambda: self.command(
                [16, (tempo.value() * 256 + 50) // 100,
                 (1, 8, 16)[self.boundary.currentIndex()], self.marker.value()]
            )
        )
        layout.addWidget(tempo_button)
        parts = control("Enabled logical part bitmask", 65535, 65535, lambda v: None)
        button = QPushButton("Apply part selection at selected boundary")
        button.clicked.connect(
            lambda: self.command(
                [
                    14,
                    parts.value(),
                    (1, 8, 16)[self.boundary.currentIndex()],
                    self.marker.value(),
                ]
            )
        )
        layout.addWidget(button)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()

    def show_status(self, status):
        self.last_status = status
        labels = ["FM1", "FM2", "FM3", "FM4", "FM5", "PSG1", "PSG2", "PSG3", "Noise"]
        owners = "  ·  ".join(
            f"{name}: " + (f"SFX {owner}" if owner else "music / free")
            for name, owner in zip(labels, status["owners"])
        )
        effects = "; ".join(
            f"SFX {i+1}: token {e['token']}, priority {e['priority']}"
            for i, e in enumerate(status["effects"])
            if e["active"]
        )
        pcm = ("idle", "music", "SFX")[min(2, status["pcm_owner"])]
        self.ownership.setText(
            f"{owners}\nPCM: {pcm} · tick {status['tick']} · fault {status['fault']} · underruns {status['underruns']}\n{effects}"
        )
        if status["ack"] != self.last_ack:
            self.last_ack = status["ack"]
            self.statusBar().showMessage(
                (
                    "Command queued; see channel ownership for allocation"
                    if status["queued"]
                    else "Adapter rejected command"
                ),
                4000,
            )

    def stop_preview(self):
        if self.player:
            player = self.player
            self.player = None
            player.write(b'{"quit":true}\n')
            player.closeWriteChannel()
            QTimer.singleShot(
                1000,
                lambda p=player: (
                    p.kill()
                    if p in self.jobs and p.state() != QProcess.ProcessState.NotRunning
                    else None
                ),
            )
        self.audio.stop()
        self.player_revision = None
        self.player_profile = None
        self.preview_label.setText("Preview stopped")

    def check_sources(self):
        stats = {}
        for cat in ("songs", "sfx"):
            for item in self.document.data.get(cat, []):
                if not isinstance(item.get("source"), str):
                    continue
                path = (self.document.base_dir / item["source"]).resolve()
                try:
                    s = path.stat()
                    stats[str(path)] = (s.st_size, s.st_mtime_ns)
                except OSError:
                    stats[str(path)] = None
        if self.source_stats and stats != self.source_stats:
            self.document.revision += 1
            self.statusBar().showMessage(
                "Resource files changed; validate or restart preview to rebuild", 8000
            )
            if self.player:
                self.preview_label.setText(
                    f"Playing revision {self.player_revision}; source resources changed"
                )
        self.source_stats = stats

    def about(self):
        QMessageBox.information(
            self,
            "Calliope Bank Editor",
            "Bank authoring and actual-driver Genesis audition.\n\nPreview uses Genesis Plus GX; its noncommercial redistribution terms and complete core source are bundled in assets. Qt/PySide6 uses its applicable LGPL/GPL/commercial terms. Player builds include SGDK code; see editor/THIRD_PARTY.md.\n\nEmulator preview includes current Calliope timing limitations. Physical hardware validation remains separate.",
        )

    def closeEvent(self, event):
        if not self.may_close():
            event.ignore()
            return
        self.stop_preview()
        for process in list(self.jobs):
            process.kill()
            process.waitForFinished(1000)
        event.accept()
