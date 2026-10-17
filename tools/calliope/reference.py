"""Independent host decoder for the experimental cartridge format."""

import struct
from .furnace import FormatError


class Player:
    def __init__(self, data):
        self.data = data
        if len(data) < 32 or data[:6] != b"BGM1\14\0":
            raise FormatError("unsupported bank")
        self.voices = [{"note": None, "patch": None} for _ in range(5)]
        self.parts = []
        self.selected = 0
        self.groups = 0
        self.pending_parts = None
        self.current_part = None
        self.trace = []
        self.tick = 0
        self.wait = 0
        self.playing = False
        self.events = []
        self.lost = 0

    def u24(self, p):
        if not 0 <= p <= len(self.data) - 3:
            raise FormatError("pointer outside bank")
        return int.from_bytes(self.data[p : p + 3], "little")

    def start(self, id, generation=1):
        base = self.u24(10)
        for i in range(self.data[6]):
            p = base + i * 24
            if p + 24 > len(self.data):
                raise FormatError("truncated song descriptor")
            if int.from_bytes(self.data[p : p + 2], "little") == id:
                self.count = self.data[p + 2] + 1
                self.loop = self.data[p + 3]
                self.orders = self.u24(p + 4)
                self.fm_steal_order = sorted(
                    range(5), key=lambda ch: self.data[p + 16 + ch]
                )
                self.psg_steal_order = sorted(
                    range(3), key=lambda ch: self.data[p + 21 + ch]
                )
                self.rate = int.from_bytes(self.data[p + 14 : p + 16], "little")
                self.credit = 0
                self.order = 0
                self.tick = 0
                self.wait = 0
                self.generation = generation
                self.playing = True
                meta = self.u24(p + 8)
                count = self.data[p + 11] & 31
                if not 1 <= count <= 16:
                    raise FormatError("invalid voice count")
                self.parts = [
                    {
                        "slot": self.data[meta + j * 4],
                        "groups": int.from_bytes(
                            self.data[meta + j * 4 + 1 : meta + j * 4 + 3], "little"
                        ),
                        "enabled": bool(self.data[meta + j * 4 + 3]),
                        "patch": None,
                        "note": None,
                    }
                    for j in range(count)
                ]
                self.selected = sum(
                    1 << j for j, v in enumerate(self.parts) if v["enabled"]
                )
                self.groups = 0
                self.arrangements = [
                    int.from_bytes(
                        self.data[
                            meta + count * 4 + j * 2 : meta + count * 4 + j * 2 + 2
                        ],
                        "little",
                    )
                    for j in range(self.data[p + 12])
                ]
                self.current_part = None
                self.pending_parts = None
                self.load_order()
                return
        raise FormatError("unknown song")

    def set_parts(self, mask, boundary=1, marker=0):
        if (
            not self.parts
            or not isinstance(mask, int)
            or mask < 0
            or mask >> len(self.parts)
        ):
            raise FormatError("invalid part mask")
        slots = [v["slot"] for i, v in enumerate(self.parts) if mask & (1 << i)]
        if len(set(slots)) != len(slots):
            raise FormatError("conflicting parts")
        if boundary not in (1, 8, 16):
            raise FormatError("invalid boundary")
        self.selected = mask
        effective = sum(
            1 << i
            for i, v in enumerate(self.parts)
            if mask & (1 << i) and not v["groups"] & self.groups
        )
        self.pending_parts = (effective, boundary, marker)
        self.boundary(1)

    def select_arrangement(self, index, boundary=1, marker=0):
        if not 0 <= index < len(self.arrangements):
            raise FormatError("invalid arrangement")
        self.set_parts(self.arrangements[index], boundary, marker)

    def mute_groups(self, mask):
        self.groups = mask
        self.set_parts(self.selected)

    def boundary(self, flags, marker=0):
        if self.pending_parts is None:
            return
        mask, requested, wanted = self.pending_parts
        if not flags & requested or requested == 16 and marker != wanted:
            return
        for i, v in enumerate(self.parts):
            enabled = bool(mask & (1 << i))
            if enabled != v["enabled"] and v["slot"] < 5:
                self.voices[v["slot"]]["note"] = None
            v["enabled"] = enabled
        self.pending_parts = None

    def audible(self):
        return self.current_part is None or self.parts[self.current_part]["enabled"]

    def read(self):
        if not 0 <= self.pc < len(self.data):
            raise FormatError("stream overrun")
        b = self.data[self.pc]
        self.pc += 1
        return b

    def load_order(self):
        record = self.u24(self.orders + self.order * 3)
        self.pc = self.u24(record)
        self.wait = 0
        self.channel_pcs = [
            self.u24(record + 3 + i * 3) for i in range(len(self.parts))
        ]
        self.channel_waits = [0] * len(self.parts)

    def clock(self):
        """Advance one nominal 60 Hz driver clock; SFX retain this clock."""
        self.credit += self.rate
        while self.credit >= 300:
            self.credit -= 300
            self.step()

    def step(self):
        if not self.playing:
            return
        self.tick += 1
        if self.wait:
            self.wait -= 1
        if not self.wait:
            self.current_part = None
            self.decode()
        if not self.playing:
            return
        self.channel_waits = [max(0, w - 1) for w in self.channel_waits]
        conductor_pc, conductor_wait = self.pc, self.wait
        for pcm in (True, False):
            for i, part in enumerate(self.parts):
                if self.channel_waits[i] or (part["slot"] == 9) != pcm:
                    continue
                self.pc = self.channel_pcs[i]
                self.wait = 0
                self.current_part = i
                self.decode()
                self.channel_pcs[i] = self.pc
                self.channel_waits[i] = self.wait
        self.pc, self.wait = conductor_pc, conductor_wait
        self.current_part = None

    def decode(self):
        for _ in range(1024):
            op = self.read()
            if 0x80 <= op < 0xFD:
                self.wait = op - 0x7F
                return
            if op == 0:
                self.wait = self.read()
                if not self.wait:
                    raise FormatError("zero delay")
                return
            if 0x40 <= op < 0x50:
                part = op & 15
                if part >= len(self.parts):
                    raise FormatError("invalid patch part")
                value = self.read()
                v = self.parts[part]
                v["patch"] = value
                if v["enabled"]:
                    ch = v["slot"]
                    self.voices[ch]["patch"] = value
                    self.trace.append((self.tick, 0x10, ch, value))
            elif op in (0x10, 0x11, 0x13, 0x36):
                if self.current_part is None:
                    raise FormatError("FM operation without a channel")
                ch = self.parts[self.current_part]["slot"]
                if ch >= 5:
                    raise FormatError("invalid FM slot")
                value = self.read() if op != 0x13 else None
                if op in (0x11, 0x36):
                    value |= self.read() << 8
                if self.current_part is not None:
                    part = self.parts[self.current_part]
                    if op != 0x36 or part["note"] is not None:
                        part["patch" if op == 0x10 else "note"] = value
                if self.audible() and (
                    op != 0x36 or self.voices[ch]["note"] is not None
                ):
                    if self.current_part is not None and op == 0x11:
                        self.voices[ch]["patch"] = self.parts[self.current_part][
                            "patch"
                        ]
                    self.voices[ch]["patch" if op == 0x10 else "note"] = value
                    self.trace.append((self.tick, op, ch, value))
            elif op == 0x37:
                ch = self.parts[self.current_part]["slot"]
                level = bytes(self.read() for _ in range(5))
                if self.current_part is not None:
                    self.parts[self.current_part]["level"] = level
                if self.audible():
                    self.voices[ch]["level"] = level
                    self.trace.append((self.tick, op, ch, level))
            elif op == 0x16:
                if self.current_part is None:
                    raise FormatError("panning operation without a channel")
                part = self.parts[self.current_part]
                ch = part["slot"]
                value = self.read()
                if ch >= 5:
                    raise FormatError("invalid FM panning slot")
                part["pan"] = value
                if self.audible():
                    self.voices[ch]["pan"] = value
                    self.trace.append((self.tick, op, ch, value))
            elif op == 0x15:
                flags = self.read()
                marker = self.read() | (self.read() << 8)
                self.boundary(flags, marker)
            elif op in (0x30, 0x35):
                values = tuple(self.read() for _ in range(self.read()))
                if self.audible():
                    self.trace.append((self.tick, op, values))
            elif op == 0x34:
                self.rate = self.read() | (self.read() << 8)
                self.credit = 0
            elif op == 0x39:
                self.trace.append((self.tick, "ym_lfo", self.read()))
            elif op == 0x33:
                control = self.read()
                volume = self.read()
                if self.audible():
                    self.trace.append((self.tick, op, control, volume))
            elif op == 0x31:
                descriptor = self.read() | (self.read() << 8) | (self.read() << 16)
                if self.audible():
                    self.trace.append((self.tick, op, descriptor))
            elif op == 0x32:
                if self.audible():
                    self.trace.append((self.tick, op))
            elif op == 0x20:
                eid = self.read() | (self.read() << 8)
                param = self.read() | (self.read() << 8)
                event = (self.tick, self.generation, eid, param)
                if len(self.events) == 16:
                    self.lost += 1
                else:
                    self.events.append(event)
            elif op == 0xFD:
                if self.current_part is not None:
                    raise FormatError("unexpected channel end")
                self.order += 1
                if self.order >= self.count:
                    if self.loop == 255:
                        self.playing = False
                        return
                    self.order = self.loop
                self.load_order()
            elif op == 255:
                self.playing = False
                return
            else:
                raise FormatError(f"unknown opcode {op:02x}")
        raise FormatError("zero-time command budget exhausted")
