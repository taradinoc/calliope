"""Bounded Furnace 156/232/248 reader. Based on Furnace papers/format.md and newIns.md.
No Furnace engine source is linked or embedded. Unknown semantic features are
retained for validation, never silently treated as supported.
"""

from dataclasses import dataclass, field
from pathlib import Path
import math
import struct
import zlib


class FormatError(ValueError):
    pass


class Reader:
    def __init__(self, data, label="", start=0, end=None):
        self.data, self.label, self.pos = data, label, start
        self.end = len(data) if end is None else end

    def take(self, n):
        if n < 0 or self.pos + n > self.end:
            raise FormatError(
                f"{self.label}: truncated at 0x{self.pos:x}, need {n} bytes"
            )
        v = self.data[self.pos : self.pos + n]
        self.pos += n
        return v

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return struct.unpack("<H", self.take(2))[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def f32(self):
        v = struct.unpack("<f", self.take(4))[0]
        if not math.isfinite(v):
            raise FormatError(f"{self.label}: nonfinite number")
        return v

    def string(self):
        end = self.data.find(b"\0", self.pos, self.end)
        if end < 0:
            raise FormatError(f"{self.label}: unterminated string")
        try:
            return self.take(end - self.pos + 1)[:-1].decode("utf-8")
        except UnicodeDecodeError as e:
            raise FormatError(f"{self.label}: invalid UTF-8") from e

    def block(self, offset, tag):
        if offset < 32 or offset + 8 > len(self.data):
            raise FormatError(f"{self.label}: invalid block offset {offset}")
        r = Reader(self.data, self.label, offset)
        if r.take(4) != tag:
            raise FormatError(f"{self.label}: expected {tag!r} at {offset:x}")
        size = r.u32()
        if size > len(self.data) - r.pos:
            raise FormatError(f"{self.label}: block overruns file")
        r.end = r.pos + size
        return r


@dataclass
class Cell:
    note: int | None = None
    instrument: int | None = None
    volume: int | None = None
    effects: list = field(default_factory=list)

    def active(self):
        return (
            self.note is not None
            or self.instrument is not None
            or self.volume is not None
            or bool(self.effects)
        )


@dataclass
class Subsong:
    hz: float
    rows: int
    orders: list
    names: list
    speeds: list
    virtual: tuple = (1, 1)
    highlights: tuple = (4, 16)
    name: str = ""
    arp_speed: int = 1


@dataclass
class Module:
    version: int
    name: str
    channels: list
    subsongs: list
    instruments: list
    patterns: dict
    flags: dict
    tuning: float
    path: str
    samples: list = field(default_factory=list)

    def cell(self, sub, order, row, channel):
        s = self.subsongs[sub]
        return self.patterns.get((sub, channel, s.orders[channel][order]), {}).get(
            row, Cell()
        )


CHIPS = {0x83: ("fm", 6), 3: ("psg", 4)}


def channel_list(chips):
    result = []
    for instance, chip in enumerate(chips):
        if chip not in CHIPS:
            raise FormatError(f"unsupported Furnace chip 0x{chip:02x}")
        kind, count = CHIPS[chip]
        result += [
            (instance, "noise" if kind == "psg" and ch == 3 else kind, ch)
            for ch in range(count)
        ]
    return result


def channel_metadata(r, channels, count):
    orders = [list(r.take(count)) for _ in range(channels)]
    columns = r.take(channels)
    if any(not 1 <= n <= 8 for n in columns):
        raise FormatError("invalid effect column count")
    r.take(channels * 2)
    names = [r.string() for _ in range(channels)]
    for _ in range(channels):
        r.string()
    return orders, names


def instrument(r):
    version, kind = r.u16(), r.u16()
    features = {}
    while r.pos < r.end:
        key = r.take(2).decode("ascii")
        if key == "EN":
            break
        size = r.u16()
        if key in features:
            raise FormatError("duplicate instrument feature " + key)
        features[key] = r.take(size)
    return {"version": version, "kind": kind, "features": features}


def parse(path):
    path = Path(path)
    raw = path.read_bytes()
    if not raw.startswith(b"-Furnace module-"):
        try:
            dec = zlib.decompressobj()
            raw = dec.decompress(raw, 16 * 1024 * 1024 + 1)
            if not dec.eof or len(raw) > 16 * 1024 * 1024 or dec.unused_data:
                raise FormatError("invalid or oversized compressed module")
        except zlib.error as e:
            raise FormatError(f"{path}: invalid zlib input") from e
    if len(raw) > 16 * 1024 * 1024:
        raise FormatError("module exceeds 16 MiB input limit")
    r = Reader(raw, str(path))
    if r.take(16) != b"-Furnace module-":
        raise FormatError("invalid Furnace magic")
    version = r.u16()
    r.take(2)
    info = r.u32()
    r.take(8)
    if version not in (156, 232, 248):
        raise FormatError(
            f"unsupported Furnace format {version}; supported: 156, 232, 248"
        )
    flags = {}
    subs = []
    pointers = {}
    columns = []
    name = ""
    tuning = 440.0
    if version in (156, 232):
        q = r.block(info, b"INFO")
        timebase, speed1, speed2, arp = q.take(4)
        hz = q.f32()
        rows = q.u16()
        count = q.u16()
        highlights = tuple(q.take(2))
        ni, nw, ns, np = q.u16(), q.u16(), q.u16(), q.u32()
        if max(ni, nw, ns) > 256 or np > 65536:
            raise FormatError("excessive asset count")
        chip_bytes = q.take(32)
        chips = (
            list(chip_bytes[: chip_bytes.index(0)])
            if 0 in chip_bytes
            else list(chip_bytes)
        )
        channels = channel_list(chips)
        q.take(64)
        flagptrs = [q.u32() for _ in range(32)]
        name, author, tuning = q.string(), q.string(), q.f32()
        flags["compat_legacy"] = list(q.take(20))
        flags["timebase"] = timebase
        pointers = {5: [], 6: [], 7: []}
        pointers[4] = [q.u32() for _ in range(ni)]
        pointers[5] = [q.u32() for _ in range(nw)]
        pointers[6] = [q.u32() for _ in range(ns)]
        pointers[7] = [q.u32() for _ in range(np)]
        metadata_at = q.pos
        orders, names = channel_metadata(q, len(channels), count)
        columns = list(
            raw[
                metadata_at
                + len(channels) * count : metadata_at
                + len(channels) * (count + 1)
            ]
        )
        q.string()
        flags["master_volume"] = q.f32()
        flags["compat_extended"] = list(q.take(28))
        virtual = (q.u16(), q.u16())
        subname = q.string()
        q.string()
        extra = q.u8()
        q.take(3)
        subptrs = [q.u32() for _ in range(extra)]
        for _ in range(6):
            q.string()
        flags["outputs"] = [(q.f32(), q.f32(), q.f32()) for _ in chips]
        connections = q.u32()
        q.take(connections * 4)
        flags["auto_patchbay"] = q.u8()
        flags["compat_more"] = list(q.take(8))
        n = q.u8()
        speeds = list(q.take(16))[:n]
        ng = q.u8()
        flags["grooves"] = []
        for _ in range(ng):
            n = q.u8()
            flags["grooves"].append(list(q.take(16))[:n])
        subs.append(
            Subsong(hz, rows, orders, names, speeds, virtual, highlights, subname, arp)
        )
        for ptr in subptrs:
            s = r.block(ptr, b"SONG")
            tb, s1, s2, ar = s.take(4)
            shz, sr, sc = s.f32(), s.u16(), s.u16()
            hi = tuple(s.take(2))
            vt = (s.u16(), s.u16())
            sn = s.string()
            s.string()
            so, names = channel_metadata(s, len(channels), sc)
            n = s.u8()
            sp = list(s.take(16))[:n]
            if tb:
                raise FormatError("nonzero subsong time base is not supported")
            subs.append(Subsong(shz, sr, so, names, sp, vt, hi, sn, ar))
        flags["chips"] = [
            r.block(p, b"FLAG").string() if p else "" for p in flagptrs[: len(chips)]
        ]
    else:
        q = r.block(info, b"INF2")
        strings = [q.string() for _ in range(8)]
        name = strings[0]
        tuning = q.f32()
        q.u8()
        flags["master_volume"] = q.f32()
        nch, nc = q.u16(), q.u16()
        if nc > 32 or nch > 256:
            raise FormatError("too many chips/channels")
        chips = []
        flags["outputs"] = []
        for _ in range(nc):
            chip, n = q.u16(), q.u16()
            chips.append(chip)
            if chip not in CHIPS or n != CHIPS[chip][1]:
                raise FormatError("unsupported chip/channel configuration")
            flags["outputs"].append((q.f32(), q.f32(), q.f32()))
        channels = channel_list(chips)
        if len(channels) != nch:
            raise FormatError("channel count mismatch")
        q.take(q.u32() * 4)
        flags["auto_patchbay"] = q.u8()
        while q.pos < q.end:
            kind = q.u8()
            if not kind:
                break
            n = q.u32()
            if n > 65536:
                raise FormatError("excessive element count")
            if kind in pointers:
                raise FormatError("duplicate element directory")
            pointers[kind] = [q.u32() for _ in range(n)]
        flags["chips"] = [r.block(p, b"FLAG").string() for p in pointers.get(2, [])]
        flags["compat"] = [
            r.block(p, b"CFLG").take(r.block(p, b"CFLG").end - p - 8).hex()
            for p in pointers.get(8, [])
        ]
        flags["grooves"] = []
        for ptr in pointers.get(10, []):
            groove = r.block(ptr, b"GROV")
            length = groove.u8()
            if not 1 <= length <= 16:
                raise FormatError("invalid groove length")
            flags["grooves"].append([groove.u16() for _ in range(16)][:length])
        for ptr in pointers.get(1, []):
            s = r.block(ptr, b"SNG2")
            hz = s.f32()
            ar, divider = s.u8(), s.u8()
            rows, count = s.u16(), s.u16()
            hi = tuple(s.take(2))
            vt = (s.u16(), s.u16())
            n = s.u8()
            sp = [s.u16() for _ in range(16)][:n]
            sn = s.string()
            s.string()
            orders, names = channel_metadata(s, nch, count)
            s.take(4 * nch)
            if divider != 1:
                raise FormatError("effect speed divider must be 1")
            subs.append(Subsong(hz, rows, orders, names, sp, vt, hi, sn, ar))
    if not subs:
        raise FormatError("no subsongs")
    for s in subs:
        if not 1 <= s.rows <= 256 or not s.orders or not 1 <= len(s.orders[0]) <= 256:
            raise FormatError("invalid pattern/order dimensions")
        if not 1 <= len(s.speeds) <= 16 or any(not 1 <= v <= 255 for v in s.speeds):
            raise FormatError("invalid speed pattern")
        if not 25 <= s.hz <= 120 or (s.virtual[0] == 0 or s.virtual[0] != s.virtual[1]):
            raise FormatError("unsupported tempo")
    instruments = [instrument(r.block(p, b"INS2")) for p in pointers.get(4, [])]
    patterns = {}
    for ptr in pointers.get(7, []):
        if version == 156:
            p = r.block(ptr, b"PATR")
            ch = p.u16()
            index = p.u16()
            sub = p.u16()
            p.u16()
            if sub >= len(subs) or ch >= len(channels):
                raise FormatError("invalid pattern location")
            key = (sub, ch, index)
            if key in patterns:
                raise FormatError("duplicate pattern")
            cells = {}
            for row in range(subs[sub].rows):
                note, octave, ins, vol = struct.unpack("<4h", p.take(8))
                cell = Cell()
                if note in (100, 101, 102):
                    cell.note = note + 80
                elif note:
                    if not 1 <= note <= 12:
                        raise FormatError("invalid legacy note")
                    octave = octave & 255
                    octave = octave - 256 if octave >= 128 else octave
                    cell.note = note + 12 * octave + 60
                    if not 0 <= cell.note < 180:
                        raise FormatError("legacy note outside supported range")
                if ins != -1:
                    cell.instrument = ins
                if vol != -1:
                    cell.volume = vol
                for col in range(columns[ch]):
                    effect, value = struct.unpack("<2h", p.take(4))
                    if effect != -1 or value != -1:
                        cell.effects.append(
                            (
                                col,
                                None if effect == -1 else effect,
                                None if value == -1 else value,
                            )
                        )
                if cell.active():
                    cells[row] = cell
            p.string()
            if p.pos != p.end:
                raise FormatError("trailing legacy pattern data")
            patterns[key] = cells
            continue
        p = r.block(ptr, b"PATN")
        sub = p.u8()
        ch = p.u8() if version < 240 else p.u16()
        index = p.u16()
        p.string()
        if sub >= len(subs) or ch >= len(channels):
            raise FormatError("invalid pattern location")
        key = (sub, ch, index)
        if key in patterns:
            raise FormatError("duplicate pattern")
        cells = {}
        row = 0
        while p.pos < p.end:
            mask = p.u8()
            if mask == 255:
                break
            if mask & 128:
                row += (mask & 127) + 2
                if row > subs[sub].rows:
                    raise FormatError("pattern skip exceeds row count")
                continue
            if row >= subs[sub].rows:
                raise FormatError("too many pattern rows")
            extra0 = p.u8() if mask & 32 else 0
            extra1 = p.u8() if mask & 64 else 0
            bits = extra0 | (extra1 << 8) | ((mask >> 3) & 3)
            cell = Cell()
            if mask & 1:
                cell.note = p.u8()
                if cell.note > 182:
                    raise FormatError("raw/invalid note unsupported")
            if mask & 2:
                cell.instrument = p.u8()
            if mask & 4:
                cell.volume = p.u8()
            for col in range(8):
                effect = p.u8() if bits & (1 << (col * 2)) else None
                value = p.u8() if bits & (2 << (col * 2)) else None
                if effect is not None or value is not None:
                    cell.effects.append((col, effect, value))
            if cell.active():
                cells[row] = cell
            row += 1
        patterns[key] = cells
    samples = []
    for pointer in pointers.get(6, []):
        q = r.block(pointer, b"SMP2")
        sample_name = q.string()
        frames = q.u32()
        rate = q.u32()
        center = q.u32()
        depth, direction, flags1, flags2 = q.take(4)
        start, end = q.u32(), q.u32()
        q.take(16)
        encoded = q.take(q.end - q.pos)
        if depth in (8, 16) and len(encoded) != frames * (depth // 8):
            raise FormatError("truncated PCM sample")
        samples.append(
            {
                "name": sample_name,
                "frames": frames,
                "rate": rate,
                "center_rate": center,
                "depth": depth,
                "direction": direction,
                "loop": None if start == 0xFFFFFFFF else [start, end],
                "data": encoded,
            }
        )
    return Module(
        version,
        name,
        channels,
        subs,
        instruments,
        patterns,
        flags,
        tuning,
        str(path),
        samples,
    )
