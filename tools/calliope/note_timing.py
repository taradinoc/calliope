"""Note timing effects following Furnace playback.cpp."""

from .furnace import FormatError, Reader


def delay_policy(module):
    if module.version in (156, 232):
        return module.flags.get("compat_extended", [0] * 28)[22]
    result = 2
    for encoded in module.flags.get("compat", []):
        for line in Reader(bytes.fromhex(encoded)).string().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "delayBehavior":
                try:
                    result = int(value.strip())
                except ValueError as e:
                    raise FormatError("invalid delay compatibility value") from e
    return result


def cuts(cell, ticks, policy):
    result = {}
    for _, effect, value in cell.effects:
        if effect not in (0xEC, 0xFC):
            continue
        if policy not in (0, 1, 2):
            raise FormatError("unsupported delay compatibility policy")
        value = value or 0
        if effect == 0xEC and not value:
            continue
        if value >= ticks:
            if policy == 2:
                raise FormatError(
                    "cross-row lax note cuts/releases are unsupported; use strict delay behavior or a row-bounded cut"
                )
            continue
        result = {value: 180 if effect == 0xEC else 181}
    return result


def fm_delay(cell, ticks, policy):
    """Delay channel row processing, retaining the strict/lax bounds policy."""
    delay = 0
    for _, effect, value in cell.effects:
        if effect != 0xED:
            continue
        if policy not in (0, 1, 2):
            raise FormatError("unsupported delay compatibility policy")
        value = value or 0
        if type(value) is not int or not 0 <= value <= 255:
            raise FormatError("ED note delay must be 0..255")
        if not value:
            continue
        if value >= ticks:
            if value == ticks and policy in (1, 2):
                delay = value
                continue
            if policy == 2:
                raise FormatError(
                    "cross-row FM note delay is unsupported; use strict delay behavior or a row-bounded delay"
                )
            continue
        delay = value
    return delay


class DelayedLegato:
    """E8xy/E9xy: shift the held note on tick x without key-on.

    Furnace starts the countdown at x+1 and ticks it on the effect's row.
    It survives row boundaries until it fires or another E8/E9 replaces it.
    """
    def __init__(self):
        self.remaining = 0
        self.semitones = 0
        self.used = False

    def begin(self, cell):
        for _, effect, value in cell.effects:
            if effect in (0xE8, 0xE9):
                value = value or 0
                if type(value) is not int or not 0 <= value <= 255:
                    raise FormatError("E8/E9 delayed legato must be 0..255")
                self.used = True
                self.semitones = (value & 15) * (-1 if effect == 0xE9 else 1)
                self.remaining = (value >> 4) + 1 if self.semitones else 0

    def tick(self):
        if self.remaining:
            self.remaining -= 1
            if not self.remaining:
                shift = self.semitones
                self.semitones = 0
                return shift
        return 0

    def state(self):
        return self.remaining, self.semitones

    def validate_loop(self, initial, entry):
        if not self.used:
            return
        if entry.note is None or entry.note >= 180:
            raise FormatError("delayed legato loop entry requires a fresh note")
        if self.state() != initial and not any(e in (0xE8, 0xE9) for _, e, _ in entry.effects):
            raise FormatError("loop entry must reset changed delayed legato countdown")
