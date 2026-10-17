"""Tracker 00xy arpeggio and global E0 speed, separate from instrument macros."""

from .furnace import Reader, FormatError


def options(module):
    fields = {
        "compatibleArpeggio": ("compat_legacy", 7, False),
        "arp0Reset": ("compat_legacy", 19, True),
        "rowResetsArpPos": ("compat_extended", 2, False),
        "resetArpPhaseOnNewNote": ("compat_more", 4, False),
    }
    result = {key: default for key, (_, _, default) in fields.items()}
    if module.version in (156, 232):
        for key, (group, index, default) in fields.items():
            values = module.flags.get(group, [])
            if index < len(values):
                result[key] = bool(values[index])
    else:
        for encoded in module.flags.get("compat", []):
            for line in Reader(bytes.fromhex(encoded)).string().splitlines():
                key, separator, value = line.partition("=")
                key = key.strip()
                if separator and key in result:
                    try:
                        result[key] = bool(int(value.strip()))
                    except ValueError as e:
                        raise FormatError("invalid arpeggio compatibility value") from e
    return result


def speed(current, cells):
    for cell in cells:
        for _, effect, value in cell.effects:
            if effect == 0xE0 and value:
                current = value
    return max(1, current)


class Arpeggio:
    def __init__(self, options=None, keyoff_resets=False):
        self.options = options or {}
        self.keyoff_resets = keyoff_resets
        self.value = 0
        self.stage = -1
        self.remaining = 1
        self.period = 1
        self.yield_tick = False
        self.reset = False
        self.first = True
        self.gate = False

    def begin(self, cell, fresh=True):
        self.first = True
        if cell.note is not None and cell.note < 180:
            if not self.gate and self.keyoff_resets:
                self.value = 0
            if self.value and self.options.get("compatibleArpeggio", False):
                self.yield_tick = True
            if fresh and self.options.get("resetArpPhaseOnNewNote", False):
                self.stage = -1
            self.gate = True
        elif cell.note in (180, 181):
            self.gate = False
        for _, effect, value in cell.effects:
            if effect == 0:
                self.value = value or 0
                if not self.value and self.options.get("arp0Reset", True):
                    self.reset = True

    def tick(self, note, blocked=False):
        result = note if self.reset else None
        self.reset = False
        if self.first and self.options.get("rowResetsArpPos", False):
            self.stage = -1
        self.first = False
        if self.value and not self.yield_tick and not blocked:
            self.remaining -= 1
            if self.remaining < 1:
                self.remaining = self.period
                self.stage = (self.stage + 1) % 3
                if note is not None:
                    result = note + (0, self.value >> 4, self.value & 15)[self.stage]
        else:
            self.yield_tick = False
        return result

    def state(self):
        return self.value, self.stage % 3, self.remaining, self.gate

    def validate_loop(self, initial, entry, used):
        if not used:
            return
        values = {e: (v or 0) for _, e, v in entry.effects}
        if self.value != initial[0] and 0 not in values:
            raise FormatError("loop entry must reset changed tracker arpeggio")
        if (
            entry.note is not None
            and entry.note < 180
            and self.options.get("compatibleArpeggio", False)
        ):
            before = bool(initial[0]) and (not self.keyoff_resets or initial[3])
            after = bool(self.value) and (not self.keyoff_resets or self.gate)
            if before != after:
                raise FormatError("loop cannot change first-note arpeggio yield")
        reset_stage = (
            self.options.get("rowResetsArpPos", False)
            or self.options.get("resetArpPhaseOnNewNote", False)
            and entry.note is not None
            and entry.note < 180
        )
        if (
            self.remaining != initial[2]
            or self.stage % 3 != initial[1]
            and not reset_stage
        ):
            raise FormatError("loop must preserve tracker arpeggio phase/countdown")


def validate_sfx_loop(module, sub, channels, start, end):
    from .note_timing import cuts, delay_policy

    song = module.subsongs[sub]
    used = {
        ch: any(
            e == 0 and v
            for o in range(len(song.orders[ch]))
            for r in range(song.rows)
            for _, e, v in module.cell(sub, o, r, ch).effects
        )
        for ch in channels
    }
    if not any(used.values()):
        return
    trackers = {
        ch: Arpeggio(options(module), module.channels[ch][1] == "psg")
        for ch in channels
        if used[ch]
    }
    initial = {}
    period = max(1, song.arp_speed)
    initial_period = period
    for row in range(end[0] * song.rows + end[1]):
        order, index = divmod(row, song.rows)
        cells = {ch: module.cell(sub, order, index, ch) for ch in channels}
        if (order, index) == tuple(start):
            initial = {ch: arp.state() for ch, arp in trackers.items()}
            initial_period = period
        period = speed(period, cells.values())
        for ch, arp in trackers.items():
            cell = cells[ch]
            if any(e in (1, 2, 3, 0xEA) for _, e, _ in cell.effects):
                raise FormatError(
                    "SFX loops combining tracker arpeggio with slides/legato are unsupported"
                )
            arp.period = period
            arp.begin(cell)
            ticks = song.speeds[row % len(song.speeds)]
            cut = cuts(cell, ticks, delay_policy(module))
            for tick in range(ticks):
                if tick in cut:
                    arp.gate = False
                arp.tick(cell.note)
    for ch, arp in trackers.items():
        arp.validate_loop(initial[ch], module.cell(sub, *start, ch), True)
    resets = any(
        e == 0xE0 and v
        for ch in channels
        for _, e, v in module.cell(sub, *start, ch).effects
    )
    if period != initial_period and not resets:
        raise FormatError("SFX loop entry must reset arpeggio speed")
