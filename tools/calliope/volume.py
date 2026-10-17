"""Furnace quarter-step volume slides, expanded at conversion time.
Source semantics: playback.cpp 0A and nextTick volume slide handling.
"""

from .furnace import FormatError, Reader


def options(module):
    if module.version in (156, 232):
        return {
            "skip_first": bool(module.flags.get("compat_extended", [0] * 28)[1]),
            "legacy": bool(module.flags.get("compat_legacy", [0] * 20)[6]),
            "keep_speed": False,
        }
    values = {"noSlidesOnFirstTick": 0, "legacyVolumeSlides": 0, "noVolSlideReset": 0}
    for encoded in module.flags.get("compat", []):
        for line in Reader(bytes.fromhex(encoded)).string().splitlines():
            key, separator, value = line.partition("=")
            key = key.strip()
            if separator and key in values:
                try:
                    values[key] = int(value.strip())
                except ValueError as e:
                    raise FormatError("invalid volume-slide compatibility value") from e
    return dict(zip(("skip_first", "legacy", "keep_speed"), map(bool, values.values())))


class Slide:
    def __init__(self, maximum, options=None):
        self.maximum = (maximum << 8) | 255
        self.value = maximum << 8
        self.speed = 0
        self.options = options or {}
        self.first = True

    def begin(self, cell):
        self.first = True
        if cell.volume is not None:
            self.value = cell.volume << 8
        for _, effect, value in cell.effects:
            if effect != 0x0A:
                continue
            if self.options.get("legacy"):
                raise FormatError(
                    "legacy volume-slide underflow behavior is unsupported"
                )
            value = value or 0
            self.speed = -(value & 15) * 64 if value & 15 else (value >> 4) * 64

    def tick(self):
        if not (self.first and self.options.get("skip_first")):
            self.value += self.speed
            if not 0 <= self.value <= self.maximum:
                self.value = max(0, min(self.maximum, self.value))
                if not self.options.get("keep_speed"):
                    self.speed = 0
        self.first = False
        return self.value >> 8


def validate_loop_entry(cell, effects):
    if 0x0A in effects and (
        cell.volume is None or not any(e == 0x0A for _, e, _ in cell.effects)
    ):
        raise FormatError(
            "volume-slide loop entry requires explicit volume and 0A restart/cancel"
        )
