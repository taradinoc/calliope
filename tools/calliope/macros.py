"""Bounded Furnace sequence macros, interpreted only at conversion time.
Semantics are documented in Furnace papers/newIns.md and macroInt.cpp.
"""

from dataclasses import dataclass
from .furnace import Reader, FormatError


def full_linear_pitch(module):
    # Format 232 distinguishes partial (1) from full (2) pitch linearity.
    # Modern Furnace removed partial mode; do not silently reinterpret it.
    if module.version in (156, 232):
        return module.flags.get("compat_legacy", [0, 0])[1] == 2
    result = 1
    for encoded in module.flags.get("compat", []):
        data = Reader(bytes.fromhex(encoded)).string()
        for line in data.splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "linearPitch":
                try:
                    result = int(value.strip())
                except ValueError as e:
                    raise FormatError("invalid linearPitch compatibility value") from e
                if not 0 <= result <= 255:
                    raise FormatError("invalid linearPitch compatibility value")
    return bool(result)


@dataclass(frozen=True)
class Sequence:
    values: tuple
    loop: int
    release: int
    mode: int
    delay: int
    speed: int
    instant_release: bool


def sequences(instrument, allowed=(0, 1, 4), kind=0):
    if instrument is None:
        return {}
    if instrument["kind"] != kind or set(instrument["features"]) - (
        {"NA", "MA", "FM", "LD", "SM", "NE"} if kind == 1 else {"NA", "MA"}
    ):
        raise FormatError("instrument requires bounded sequence macros")
    data = instrument["features"].get("MA")
    if data is None:
        return {}
    r = Reader(data)
    if r.u16() != 8:
        raise FormatError("unsupported macro header")
    result = {}
    while r.pos < r.end:
        code = r.u8()
        if code == 255:
            if r.pos != r.end:
                raise FormatError("trailing macro data")
            return result
        length, loop, release, mode, flags, delay, speed = [r.u8() for _ in range(7)]
        if code not in allowed or code in result:
            raise FormatError("unsupported or duplicate sequence macro")
        if not 1 <= length <= 255 or not speed:
            raise FormatError("sequence needs 1..255 steps and nonzero speed")
        if flags & 0x36 or mode not in ((0, 1) if code == 4 else (0,)):
            raise FormatError(
                "only normal relative arpeggio and absolute/relative pitch sequences are supported"
            )
        size = (1, 1, 2, 4)[flags >> 6]
        values = tuple(
            int.from_bytes(r.take(size), "little", signed=bool(flags >> 6))
            for _ in range(length)
        )
        bounds = {
            0: (0, 127 if kind == 1 else 15),
            1: (-96, 96),
            4: (-32768, 32767),
            2: (0, 3),
            14: (0, 1),
        }[code]
        if any(not bounds[0] <= v <= bounds[1] for v in values):
            raise FormatError("sequence value outside supported range")
        result[code] = Sequence(
            values, loop, release, mode, delay, speed, bool(flags & 8)
        )
    raise FormatError("unterminated macro data")


class Cursor:
    def __init__(self, sequence):
        self.sequence = sequence
        self.position = 0
        self.wait = 0
        self.first = True

    def step(self, released):
        s = self.sequence
        if released and s.instant_release and self.position < s.release < len(s.values):
            self.position = s.release
            self.wait = 0
        if self.wait:
            self.wait -= 1
            return None
        if self.position >= len(s.values):
            return None
        value = s.values[self.position]
        self.position += 1
        self.wait = s.delay if self.first and s.delay else s.speed - 1
        self.first = False
        if self.position > s.release and not released:
            self.position = (
                s.loop
                if s.loop < len(s.values) and s.loop < s.release
                else self.position - 1
            )
        if (
            self.position >= len(s.values)
            and s.loop < len(s.values)
            and (s.loop >= s.release or s.release >= len(s.values))
        ):
            self.position = s.loop
        return value


def continuous_vibrato(module):
    if module.version in (156, 232):
        return bool(module.flags.get("compat_legacy", [0] * 20)[15])
    for encoded in module.flags.get("compat", []):
        for line in Reader(bytes.fromhex(encoded)).string().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "continuousVibrato":
                try:
                    return bool(int(value.strip()))
                except ValueError as e:
                    raise FormatError(
                        "invalid continuousVibrato compatibility value"
                    ) from e
    return False
