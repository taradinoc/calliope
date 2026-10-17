"""Compile supported Furnace row timing into waits and conductor rate changes."""

from .furnace import FormatError

TIMING_EFFECTS = {0x09, 0x0F, 0xC0, 0xC1, 0xC2, 0xC3, 0xF0}


class CommandStream:
    """Keep elapsed time until an operation or order end needs a wait."""

    def __init__(self, initial=b"", compact=False):
        self.data = bytearray(initial)
        self.delay = 0
        self.compact = compact

    def flush(self):
        while self.delay:
            ticks = min(self.delay, 255)
            self.data.extend(
                (0x7F + ticks,) if self.compact and ticks <= 125 else (0, ticks)
            )
            self.delay -= ticks

    def extend(self, operations):
        if operations:
            self.flush()
            self.data.extend(operations)

    def wait(self, ticks):
        self.delay += ticks

    def finish(self):
        self.flush()
        return bytes(self.data)


def rate5(hz):
    value = round(hz * 5)
    if not 125 <= value <= 600 or abs(value - hz * 5) > 0.0001:
        raise FormatError("tick rate must be 25..120 Hz in 0.2 Hz steps")
    return value


class Timing:
    def __init__(self, module, sub):
        self.module = module
        self.song = module.subsongs[sub]
        self.sub = sub
        self.speeds = list(self.song.speeds)
        self.phase = 0
        self.rate = rate5(self.song.hz)
        self.grooves = module.flags.get("grooves", [])
        for speeds in self.grooves:
            if not 1 <= len(speeds) <= 16 or any(not 1 <= v <= 255 for v in speeds):
                raise FormatError("invalid groove")
        if module.flags.get("compat_extended", [0])[0] and len(set(self.speeds)) > 1:
            raise FormatError("legacy speed selection is unsupported")
        self.pre = None

    def row(self, order, row, channels):
        output = bytearray()
        for ch in sorted(channels):
            for _, effect, value in self.module.cell(self.sub, order, row, ch).effects:
                value = 0 if value is None else value
                if effect == 0x09:
                    if self.grooves:
                        if value >= len(self.grooves):
                            raise FormatError("unknown groove index")
                        self.speeds = list(self.grooves[value])
                        self.phase = 0
                    elif value:
                        self.speeds[0] = value
                elif effect == 0x0F and value:
                    self.speeds[
                        1 if len(self.speeds) == 2 and not self.grooves else 0
                    ] = value
                elif effect in (0xC0, 0xC1, 0xC2, 0xC3, 0xF0):
                    self.rate = rate5(
                        value * 2 / 5 if effect == 0xF0 else ((effect & 3) << 8) | value
                    )
                    output.extend((0x34, self.rate & 255, self.rate >> 8))
        if (
            self.module.flags.get("compat_extended", [0])[0]
            and len(set(self.speeds)) > 1
        ):
            raise FormatError("legacy speed selection is unsupported")
        self.pre = (tuple(self.speeds), self.phase, self.rate)
        ticks = self.speeds[self.phase]
        self.phase = (self.phase + 1) % len(self.speeds)
        return ticks, output
