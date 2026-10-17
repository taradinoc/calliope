"""Bank authoring forms. JSON keys and enum tokens stay behind the controls."""

import copy
import json
import os
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QTabWidget,
    QScrollArea,
    QGroupBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QPushButton,
    QCheckBox,
    QFileDialog,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
)
from ..furnace import parse, FormatError
from ..compiler import used_channels, infer_sfx
from .document import clean_optional_collections

SLOTS = [
    ("FM 1", "fm0"),
    ("FM 2", "fm1"),
    ("FM 3", "fm2"),
    ("FM 4", "fm3"),
    ("FM 5", "fm4"),
    ("PSG tone 1", "tone0"),
    ("PSG tone 2", "tone1"),
    ("PSG tone 3", "tone2"),
    ("PSG noise", "noise"),
    ("PCM", "pcm"),
]
SOURCE_CACHE = {}


def number(value, default=0):
    return value if type(value) in (int, float) else default


class Properties(QWidget):
    changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.value = None
        self.category = None
        self.base_dir = None
        self.controls = {}

    def load(self, value, category="songs", base_dir=None, force=False):
        base = Path(base_dir or Path.cwd()).resolve()
        if (
            not force
            and category == self.category
            and base == self.base_dir
            and json.dumps(value) == json.dumps(self.value)
        ):
            return
        old_tab = self.tabs.currentIndex()
        self.value = copy.deepcopy(value)
        self.category = category
        self.base_dir = base
        self.controls = {}
        while self.tabs.count():
            widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            widget.deleteLater()
        self.module = None
        source = self.value.get("source")
        self.source_problem = ""
        if isinstance(source, str) and Path(source).suffix.lower() == ".fur":
            try:
                path = (base / source).resolve()
                stat = path.stat()
                key = (str(path), stat.st_mtime_ns, stat.st_size)
                if key not in SOURCE_CACHE:
                    if len(SOURCE_CACHE) > 16:
                        SOURCE_CACHE.clear()
                    SOURCE_CACHE[key] = parse(path)
                self.module = SOURCE_CACHE[key]
            except (OSError, ValueError, FormatError) as e:
                self.source_problem = str(e)
        general = self.page("General")
        form = QFormLayout()
        general.addLayout(form)
        if category == "bank":
            form.addRow(
                "Bank format",
                QLabel("Calliope bank, version " + str(self.value.get("version", 1))),
            )
            self.numeric(
                form,
                "marker_advance_ticks",
                "Marker lookahead (ticks)",
                0,
                65535,
                0,
                optional=True,
                tip="Send marker events this many music ticks early. Songs can override this bank default.",
            )
            return
        self.text(
            form,
            "name",
            "Name",
            tip="A unique resource name, used in the generated C header. Letters, digits and underscores; start with a letter or underscore.",
        )
        self.numeric(
            form,
            "id",
            "Resource ID",
            0,
            65534,
            0,
            tip="Stable ID used by your game. Song IDs and effect IDs have separate namespaces.",
        )
        source_row = QWidget()
        row = QHBoxLayout(source_row)
        row.setContentsMargins(0, 0, 0, 0)
        path_edit = QLineEdit(str(source or ""))
        path_edit.setObjectName("source")
        self.controls["source"] = path_edit
        path_edit.setPlaceholderText("Choose a Furnace module or WAV sample")
        path_edit.editingFinished.connect(lambda: self.set("source", path_edit.text()))
        row.addWidget(path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        form.addRow("Audio file", source_row)
        if self.module:
            summary = QLabel(
                f'{self.module.name or "Untitled module"} · Furnace {self.module.version} · {len(self.module.channels)} source channels'
            )
            summary.setWordWrap(True)
            form.addRow("", summary)
        elif self.source_problem:
            label = QLabel(
                "Source could not be read. Choose a file with Browse to relink it."
            )
            label.setWordWrap(True)
            label.setToolTip(self.source_problem)
            form.addRow("", label)
        wav = isinstance(source, str) and Path(source).suffix.lower() == ".wav"
        self.effect_kind = "pcm" if wav else None
        if category == "sfx" and self.module:
            try:
                self.effect_kind = infer_sfx(
                    self.module, int(number(self.value.get("subsong"), 0))
                )[0]
            except (ValueError, IndexError, TypeError):
                pass
        if not wav:
            choices = (
                [
                    (f'{i+1} — {s.name or "Subsong "+str(i+1)}', i)
                    for i, s in enumerate(self.module.subsongs)
                ]
                if self.module
                else [
                    (f"Subsong {i+1}", i)
                    for i in range(
                        max(1, int(number(self.value.get("subsong"), 0)) + 1)
                    )
                ]
            )
            self.choice(form, "subsong", "Subsong", choices, 0, reload=True)
        if category == "sfx":
            descriptions = {
                "pcm": "PCM sample",
                "fm": "FM sequence",
                "multi_fm": "Multi-channel FM sequence",
                "psg": "PSG tone sequence",
                "noise": "PSG noise sequence",
                "coupled": "Coupled PSG tone / noise",
                "multi_psg": "Multi-channel PSG sequence",
            }
            form.addRow(
                "Effect type",
                QLabel(
                    descriptions.get(self.effect_kind, "Choose a supported source file")
                ),
            )
            self.numeric(
                form,
                "priority",
                "Default priority",
                0,
                255,
                1,
                tip="Higher-priority effects can replace lower-priority effects. Equal-priority effects do not preempt each other.",
            )
            if wav or self.effect_kind in (None, "fm", "multi_fm"):
                self.numeric(
                    form,
                    "gain" if wav else "fm_attenuation",
                    "Sample gain" if wav else "FM attenuation",
                    0,
                    64 if wav else 127,
                    1.0 if wav else 0,
                    decimals=3 if wav else None,
                    tip=(
                        "Gain before downsampling and conversion."
                        if wav
                        else "Additional FM attenuation: 0 keeps the authored volume; larger values are quieter."
                    ),
                )
        else:
            self.numeric(
                form,
                "pcm_gain",
                "PCM gain",
                0,
                64,
                1.0,
                decimals=3,
                tip="Gain applied to Furnace PCM samples before conversion. Lower it if samples clip.",
            )
        general.addStretch()
        if category == "songs":
            self.voice_page()
            self.membership_page()
            self.cues_page()
            self.song_playback()
        else:
            self.effect_playback(wav)
        self.tabs.setCurrentIndex(max(0, min(old_tab, self.tabs.count() - 1)))

    def page(self, title):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, title)
        return layout

    def hint(self, layout, text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color:#526572;")
        layout.addWidget(label)

    def set(self, key, value, reload=False):
        if key in self.value and self.value[key] == value:
            return
        self.value[key] = copy.deepcopy(value)
        self.publish(reload)

    def publish(self, reload=False):
        if self.category in ("songs", "sfx"):
            self.value = clean_optional_collections(self.value)
        value = copy.deepcopy(self.value)
        if reload:
            self.load(value, self.category, self.base_dir, force=True)
        self.changed.emit(value)

    def text(self, form, key, label, tip=""):
        edit = QLineEdit(str(self.value.get(key, "")))
        edit.setObjectName(key)
        edit.setToolTip(tip)
        form.addRow(label, edit)
        self.controls[key] = edit
        edit.editingFinished.connect(lambda: self.set(key, edit.text()))
        return edit

    def numeric(
        self,
        form,
        key,
        label,
        minimum,
        maximum,
        default,
        decimals=None,
        optional=False,
        tip="",
    ):
        spin = QDoubleSpinBox() if decimals is not None else QSpinBox()
        if decimals is not None:
            spin.setDecimals(decimals)
            spin.setSingleStep(0.1)
        spin.setRange(minimum, maximum)
        value = number(self.value.get(key), default)
        spin.setValue(value if decimals is not None else int(value))
        spin.setObjectName(key)
        spin.setToolTip(tip)
        self.controls[key] = spin
        if key == "tick_rate":
            spin.setSingleStep(0.2)
        spin.setKeyboardTracking(False)
        spin.editingFinished.connect(lambda: self.set(key, spin.value()))
        if optional:
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            enable = QCheckBox("Override")
            enable.setChecked(key in self.value)
            spin.setEnabled(enable.isChecked())
            row.addWidget(enable)
            row.addWidget(spin, 1)

            def toggle(checked):
                spin.setEnabled(checked)
                if checked:
                    self.set(key, spin.value())
                else:
                    self.value.pop(key, None)
                    self.publish()

            enable.toggled.connect(toggle)
            form.addRow(label, box)
        else:
            form.addRow(label, spin)
        return spin

    def choice(self, form, key, label, choices, default=None, reload=False):
        combo = QComboBox()
        combo.setObjectName(key)
        self.controls[key] = combo
        for name, value in choices:
            combo.addItem(name, value)
        value = self.value.get(key, default)
        index = combo.findData(value)
        if index < 0:
            combo.addItem("Unrecognized value: " + str(value), value)
            index = combo.count() - 1
        combo.setCurrentIndex(index)
        form.addRow(label, combo)
        combo.currentIndexChanged.connect(
            lambda: self.set(key, combo.currentData(), reload)
        )
        return combo

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose audio resource",
            str(self.base_dir),
            (
                "Audio resources (*.fur *.wav)"
                if self.category == "sfx"
                else "Furnace modules (*.fur)"
            ),
        )
        if path:
            try:
                value = Path(os.path.relpath(path, self.base_dir)).as_posix()
            except ValueError:
                value = Path(path).as_posix()
            self.set("source", value, True)

    def table(self, layout, headers, rows):
        table = QTableWidget(rows, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setMinimumHeight(170)
        layout.addWidget(table)
        return table

    def buttons(self, layout, actions):
        row = QHBoxLayout()
        layout.addLayout(row)
        for label, callback in actions:
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch()

    def cell_spin(self, table, row, column, value, maximum, callback):
        spin = QSpinBox()
        spin.setRange(0, maximum)
        spin.setValue(int(number(value)))
        spin.setKeyboardTracking(False)
        spin.editingFinished.connect(lambda: callback(spin.value()))
        table.setCellWidget(row, column, spin)
        return spin

    def sub(self):
        if not self.module:
            return None
        i = self.value.get("subsong", 0)
        return (
            self.module.subsongs[i]
            if type(i) == int and 0 <= i < len(self.module.subsongs)
            else self.module.subsongs[0]
        )

    def channels(self):
        if not self.module:
            return [(f"Source channel {i+1}", i) for i in range(16)]
        names = self.sub().names
        return [
            (
                f"{i+1} · {kind.upper()} {slot+1}"
                + (" — " + names[i] if names[i] else ""),
                i,
            )
            for i, (_, kind, slot) in enumerate(self.module.channels)
        ]

    def voice_page(self):
        layout = self.page("Channels")
        self.hint(
            layout,
            "Map each used Furnace channel to a Genesis channel. Voice names identify parts in groups and arrangements. FM channel 6 is reserved for PCM.",
        )
        voices = self.value.get("voices", [])
        voices = voices if isinstance(voices, list) else []
        table = self.table(
            layout,
            ["Voice name", "Furnace source channel", "Genesis destination"],
            len(voices),
        )
        self.voice_table = table
        for row, voice in enumerate(voices):
            if not isinstance(voice, dict):
                continue
            name = QLineEdit(str(voice.get("name", "")))
            table.setCellWidget(row, 0, name)
            name.editingFinished.connect(
                lambda r=row, e=name: self.rename_voice(r, e.text())
            )
            channel = QComboBox()
            for label, index in self.channels():
                channel.addItem(label, index)
            current = voice.get("channel", 0)
            if isinstance(current, str):
                matches = (
                    [i for i, n in enumerate(self.sub().names) if n == current]
                    if self.sub()
                    else []
                )
                if len(matches) == 1:
                    channel.setItemData(matches[0], current)
                    index = matches[0]
                else:
                    channel.addItem("Named channel: " + current, current)
                    index = channel.count() - 1
            else:
                index = channel.findData(current)
            channel.setCurrentIndex(index)
            table.setCellWidget(row, 1, channel)
            channel.currentIndexChanged.connect(
                lambda _, r=row, c=channel: self.voice_field(
                    r, "channel", c.currentData()
                )
            )
            slot = QComboBox()
            for label, value in SLOTS:
                slot.addItem(label, value)
            slot.setCurrentIndex(slot.findData(voice.get("slot")))
            table.setCellWidget(row, 2, slot)
            slot.currentIndexChanged.connect(
                lambda _, r=row, c=slot: self.voice_field(r, "slot", c.currentData())
            )
        self.buttons(
            layout,
            [
                ("Add voice", self.add_voice),
                ("Remove voice", lambda: self.remove_voice(table.currentRow())),
                ("Move up", lambda: self.move_list("voices", table.currentRow(), -1)),
                ("Move down", lambda: self.move_list("voices", table.currentRow(), 1)),
            ],
        )

    def voice_field(self, index, key, value):
        self.value["voices"][index][key] = value
        self.publish()

    def rename_voice(self, index, name):
        old = self.value["voices"][index].get("name")
        self.value["voices"][index]["name"] = name
        if old:
            for key in ("groups", "arrangements"):
                mapping = self.value.get(key, {})
                if isinstance(mapping, dict):
                    for members in mapping.values():
                        if isinstance(members, list):
                            members[:] = [name if v == old else v for v in members]
        self.publish(True)

    def add_voice(self):
        voices = self.value.setdefault("voices", [])
        if len(voices) >= 16:
            return
        used = {v.get("channel") for v in voices if isinstance(v, dict)}
        channel = next((i for _, i in self.channels() if i not in used), 0)
        slot = "fm0"
        if self.module:
            _, kind, n = self.module.channels[channel]
            slot = (
                "pcm"
                if kind == "fm" and n == 5
                else (
                    "noise"
                    if kind == "noise"
                    else ("fm" if kind == "fm" else "tone") + str(n)
                )
            )
        voices.append(dict(name=f"voice_{channel+1}", channel=channel, slot=slot))
        self.publish(True)

    def remove_voice(self, index):
        if index < 0:
            return
        name = self.value["voices"].pop(index).get("name")
        for key in ("groups", "arrangements"):
            if isinstance(self.value.get(key), dict):
                for members in self.value[key].values():
                    if isinstance(members, list):
                        members[:] = [v for v in members if v != name]
        self.publish(True)

    def move_list(self, key, index, delta):
        values = self.value.get(key, [])
        other = index + delta
        if 0 <= index < len(values) and 0 <= other < len(values):
            values[index], values[other] = values[other], values[index]
            self.publish(True)

    def membership_page(self):
        layout = self.page("Groups && arrangements")
        self.hint(
            layout,
            "Groups let the game mute related voices. Arrangements select which voices play together. Each arrangement must use distinct Genesis destinations.",
        )
        voices = [
            v.get("name")
            for v in self.value.get("voices", [])
            if isinstance(v, dict) and v.get("name")
        ]
        for key, title in [
            ("groups", "Voice groups"),
            ("arrangements", "Arrangements"),
        ]:
            group = QGroupBox(title)
            gl = QVBoxLayout(group)
            layout.addWidget(group)
            mapping = self.value.get(key, {})
            mapping = mapping if isinstance(mapping, dict) else {}
            table = self.table(gl, ["Name"] + [str(n) for n in voices], len(mapping))
            names = list(mapping)
            for row, name in enumerate(names):
                edit = QLineEdit(str(name))
                table.setCellWidget(row, 0, edit)
                edit.editingFinished.connect(
                    lambda k=key, n=name, e=edit: self.rename_member(k, n, e.text())
                )
                for column, voice in enumerate(voices, 1):
                    check = QCheckBox()
                    members = mapping[name] if isinstance(mapping[name], list) else []
                    check.setChecked(voice in members)
                    table.setCellWidget(row, column, check)
                    check.toggled.connect(
                        lambda checked, k=key, n=name, v=voice: self.member_toggle(
                            k, n, v, checked
                        )
                    )
            self.buttons(
                gl,
                [
                    (
                        "Add " + ("group" if key == "groups" else "arrangement"),
                        lambda _, k=key: self.add_member(k),
                    ),
                    (
                        "Remove",
                        lambda _, k=key, t=table: self.remove_member(k, t.currentRow()),
                    ),
                    (
                        "Move up",
                        lambda _, k=key, t=table: self.move_member(
                            k, t.currentRow(), -1
                        ),
                    ),
                    (
                        "Move down",
                        lambda _, k=key, t=table: self.move_member(
                            k, t.currentRow(), 1
                        ),
                    ),
                ],
            )
        form = QFormLayout()
        layout.addLayout(form)
        arrangements = self.value.get("arrangements", {})
        if isinstance(arrangements, dict) and arrangements:
            self.choice(
                form,
                "initial_arrangement",
                "Starting arrangement",
                [(str(n), n) for n in arrangements],
                next(iter(arrangements)),
            )
        layout.addStretch()

    def member_toggle(self, key, name, voice, enabled):
        values = self.value[key][name]
        if enabled and voice not in values:
            values.append(voice)
        elif not enabled and voice in values:
            values.remove(voice)
        self.publish()

    def add_member(self, key):
        for index, voice in enumerate(self.value.get("voices", [])):
            if isinstance(voice, dict) and not voice.get("name"):
                voice["name"] = f"voice_{index+1}"
        mapping = self.value.setdefault(key, {})
        if len(mapping) >= 16:
            return
        base = "group" if key == "groups" else "arrangement"
        i = 1
        while f"{base}_{i}" in mapping:
            i += 1
        mapping[f"{base}_{i}"] = []
        self.publish(True)

    def rename_member(self, key, old, new):
        mapping = self.value[key]
        if not new or new == old or new in mapping:
            return
        self.value[key] = {new if k == old else k: v for k, v in mapping.items()}
        if key == "arrangements" and self.value.get("initial_arrangement") == old:
            self.value["initial_arrangement"] = new
        self.publish(True)

    def remove_member(self, key, index):
        mapping = self.value.get(key, {})
        if 0 <= index < len(mapping):
            name = list(mapping)[index]
            del mapping[name]
            if key == "arrangements" and self.value.get("initial_arrangement") == name:
                self.value.pop("initial_arrangement", None)
            self.publish(True)

    def move_member(self, key, index, delta):
        mapping = self.value.get(key, {})
        names = list(mapping)
        other = index + delta
        if 0 <= index < len(names) and 0 <= other < len(names):
            names[index], names[other] = names[other], names[index]
            self.value[key] = {n: mapping[n] for n in names}
            self.publish(True)

    def cues_page(self):
        layout = self.page("Markers && events")
        self.hint(
            layout,
            "Markers define arrangement-switch boundaries. Events translate Furnace external commands into IDs and parameters for your game. Order and row positions start at zero.",
        )
        markers = self.value.get("markers", [])
        markers = markers if isinstance(markers, list) else []
        table = self.table(layout, ["Order", "Row", "Marker ID"], len(markers))
        for r, marker in enumerate(markers):
            for c, key in enumerate(("order", "row", "id")):
                self.cell_spin(
                    table,
                    r,
                    c,
                    marker.get(key, 0),
                    65535,
                    lambda v, r=r, k=key: self.marker_field(r, k, v),
                )
        self.buttons(
            layout,
            [
                ("Add marker", self.add_marker),
                (
                    "Remove marker",
                    lambda: self.remove_index("markers", table.currentRow()),
                ),
            ],
        )
        events = self.value.get("events", {})
        events = events if isinstance(events, dict) else {}
        event_table = self.table(
            layout, ["Furnace command value", "Game event ID", "Parameter"], len(events)
        )
        for r, (command, event) in enumerate(events.items()):
            try:
                value = int(command)
            except (ValueError, TypeError):
                value = 0
            self.cell_spin(
                event_table, r, 0, value, 255, lambda v, n=command: self.event_key(n, v)
            )
            for c, key in enumerate(("id", "parameter"), 1):
                self.cell_spin(
                    event_table,
                    r,
                    c,
                    event.get(key, 0),
                    65535,
                    lambda v, n=command, k=key: self.event_field(n, k, v),
                )
        self.buttons(
            layout,
            [
                ("Add event mapping", self.add_event),
                ("Remove mapping", lambda: self.remove_event(event_table.currentRow())),
            ],
        )
        form = QFormLayout()
        layout.addLayout(form)
        self.choice(
            form,
            "external_commands",
            "Unmapped external commands",
            [
                ("Require an explicit mapping", None),
                ("Pass through to the game", "passthrough"),
            ],
            None,
        )

    def marker_field(self, index, key, value):
        self.value["markers"][index][key] = value
        self.publish()

    def add_marker(self):
        self.value.setdefault("markers", []).append(dict(order=0, row=0, id=0))
        self.publish(True)

    def remove_index(self, key, index):
        if index >= 0:
            self.value[key].pop(index)
            self.publish(True)

    def event_field(self, name, key, value):
        self.value["events"][name][key] = value
        self.publish()

    def event_key(self, name, value):
        new = str(value)
        mapping = self.value["events"]
        if new != name and new not in mapping:
            self.value["events"] = {
                new if k == name else k: v for k, v in mapping.items()
            }
            self.publish(True)

    def add_event(self):
        events = self.value.setdefault("events", {})
        key = next((str(i) for i in range(256) if str(i) not in events), None)
        if key is not None:
            events[key] = dict(id=int(key), parameter=0)
            self.publish(True)

    def remove_event(self, index):
        if index >= 0:
            del self.value["events"][list(self.value["events"])[index]]
            self.publish(True)

    def song_playback(self):
        layout = self.page("Playback")
        form = QFormLayout()
        layout.addLayout(form)
        self.numeric(
            form,
            "loop_order",
            "Loop back to order",
            0,
            255,
            0,
            optional=True,
            tip="When disabled, follow the source song flow. Enable to choose an explicit loop order.",
        )
        self.numeric(
            form,
            "tick_rate",
            "Initial tempo (Hz)",
            25,
            120,
            60.0,
            decimals=1,
            optional=True,
            tip="Override the Furnace initial tick clock. Calliope accepts 0.2 Hz steps.",
        )
        self.numeric(
            form,
            "marker_advance_ticks",
            "Marker lookahead (ticks)",
            0,
            65535,
            0,
            optional=True,
        )
        self.choice(
            form,
            "ym_lfo",
            "YM2612 LFO",
            [("Off", 0)]
            + [(f"Enabled — hardware rate {i+1}", 8 + i) for i in range(8)],
            0,
        )
        self.hint(
            layout,
            "Channel stealing order: effects prefer unused eligible channels, then steal music in the order below. Move the least important music channels to the top.",
        )
        for key, title, defaults in [
            ("fm_steal_order", "FM channels", [f"fm{i}" for i in range(5)]),
            ("psg_steal_order", "PSG tone channels", [f"tone{i}" for i in range(3)]),
        ]:
            group = QGroupBox(title)
            gl = QVBoxLayout(group)
            layout.addWidget(group)
            lst = QListWidget()
            gl.addWidget(lst)
            lst.setMaximumHeight(155)
            values = self.value.get(key, defaults)
            labels = dict((v, n) for n, v in SLOTS)
            for value in values:
                item = QListWidgetItem(labels.get(value, str(value)))
                item.setData(Qt.ItemDataRole.UserRole, value)
                lst.addItem(item)
            self.buttons(
                gl,
                [
                    ("Move earlier", lambda _, k=key, l=lst: self.move_steal(k, l, -1)),
                    ("Move later", lambda _, k=key, l=lst: self.move_steal(k, l, 1)),
                    ("Use default order", lambda _, k=key: self.remove_option(k)),
                ],
            )

    def move_steal(self, key, lst, delta):
        row = lst.currentRow()
        other = row + delta
        if 0 <= row < lst.count() and 0 <= other < lst.count():
            item = lst.takeItem(row)
            lst.insertItem(other, item)
            lst.setCurrentRow(other)
            self.set(
                key,
                [
                    lst.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(lst.count())
                ],
            )

    def remove_option(self, key):
        self.value.pop(key, None)
        self.publish(True)

    def effect_playback(self, wav):
        layout = self.page("Playback && allocation")
        form = QFormLayout()
        layout.addLayout(form)
        enable = QCheckBox("Loop this effect")
        enable.setChecked("loop" in self.value)
        layout.addWidget(enable)
        loop_group = QGroupBox("Loop boundaries")
        loop_form = QFormLayout(loop_group)
        layout.addWidget(loop_group)
        loop_group.setEnabled(enable.isChecked())
        bounds = self.value.get(
            "loop", [0, 1] if wav else {"start": [0, 0], "end": [0, 1]}
        )

        def loop_field(position, component, value):
            target = self.value.setdefault(
                "loop", [0, 1] if wav else {"start": [0, 0], "end": [0, 1]}
            )
            if wav:
                target[position] = value
            else:
                target[position][component] = value
            self.publish()

        for index, label in enumerate(("Start (inclusive)", "End (exclusive)")):
            if wav:
                spin = QSpinBox()
                spin.setRange(0, 2147483647)
                spin.setValue(
                    int(
                        number(
                            bounds[index]
                            if isinstance(bounds, list) and len(bounds) > index
                            else index
                        )
                    )
                )
                spin.setKeyboardTracking(False)
                spin.editingFinished.connect(
                    lambda i=index, s=spin: loop_field(i, None, s.value())
                )
                loop_form.addRow(label + " sample frame", spin)
            else:
                key = ("start", "end")[index]
                values = (
                    bounds.get(key, [0, index])
                    if isinstance(bounds, dict)
                    else [0, index]
                )
                for component, text in enumerate(("order", "row")):
                    spin = QSpinBox()
                    spin.setRange(0, 65535)
                    spin.setValue(int(number(values[component])))
                    spin.setKeyboardTracking(False)
                    spin.editingFinished.connect(
                        lambda k=key, c=component, s=spin: loop_field(k, c, s.value())
                    )
                    loop_form.addRow(label + " " + text, spin)

        def toggle_loop(checked):
            loop_group.setEnabled(checked)
            if checked:
                self.value["loop"] = copy.deepcopy(bounds)
            else:
                self.value.pop("loop", None)
            self.publish()

        enable.toggled.connect(toggle_loop)
        self.hint(
            layout,
            "WAV loop boundaries use source sample frames. Furnace loop boundaries use source order/row positions. The end position is excluded.",
        )
        if not wav:
            reloc = QCheckBox(
                "Allow independent PSG tones to move between free tone channels"
            )
            reloc.setChecked(bool(self.value.get("relocatable_tones", False)))
            reloc.setEnabled(self.effect_kind in (None, "multi_psg"))
            reloc.toggled.connect(lambda v: self.set("relocatable_tones", v))
            layout.addWidget(reloc)
            group = QGroupBox("Eligible channels for a single-channel effect")
            gl = QVBoxLayout(group)
            layout.addWidget(group)
            override = QCheckBox("Restrict which channels this effect may steal")
            override.setChecked("resource_mask" in self.value)
            gl.addWidget(override)
            kind = "fm"
            if self.module:
                active = used_channels(
                    self.module, int(number(self.value.get("subsong"), 0))
                )
                if active:
                    kind = self.module.channels[active[0]][1]
            eligible = [
                (label, bit)
                for bit, (label, _) in enumerate(SLOTS[:9])
                if (bit < 5 if kind == "fm" else bit >= 5)
            ]
            default = 31 if kind == "fm" else 224
            mask = self.value.get("resource_mask", default)
            mask = int(number(mask, default))
            checks = []

            def update_mask():
                self.set(
                    "resource_mask", sum(1 << bit for bit, c in checks if c.isChecked())
                )

            for label, bit in eligible:
                check = QCheckBox(label)
                check.setChecked(bool(mask & (1 << bit)))
                check.setEnabled(override.isChecked())
                gl.addWidget(check)
                checks.append((bit, check))
                check.toggled.connect(update_mask)

            def toggle_mask(checked):
                for _, c in checks:
                    c.setEnabled(checked)
                if checked:
                    update_mask()
                else:
                    self.value.pop("resource_mask", None)
                    self.publish()

            override.toggled.connect(toggle_mask)
            self.hint(
                layout,
                "Multi-channel and coupled-noise effects acquire their required channels together. Validation checks any restrictions against the source effect type.",
            )
        layout.addStretch()
