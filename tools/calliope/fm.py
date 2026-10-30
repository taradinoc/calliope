"""YM2612 musical pitch compilation, independent of the runtime register writer.
Frequency/block rounding follows Furnace engine.cpp calcFreq/CONVERT_FNUM_BLOCK.
"""

import math
from .furnace import FormatError, Reader


def frequency(note, region="ntsc", tuning=440, rounded=True):
    clock = (53693175 if region == "ntsc" else 53203424) / 7
    scale = 9440540 / clock
    value = int(tuning * 2 ** ((note - 57) / 12) * scale + (0.5 if rounded else 0))
    bottom = int(min(500, max(400, tuning)) * 2**0.25 * scale)
    top = int(2 * min(500, max(400, tuning)) * 2**0.25 * scale)
    while top > 2047:
        bottom >>= 1
        top >>= 1
    block = min(7, max(0, math.trunc((math.floor(note) - 60) / 12)))
    value >>= block
    while 0 < value < bottom and block > 0:
        value <<= 1
        block -= 1
    while value > top and block < 7:
        value >>= 1
        block += 1
    return min(2047, max(0, value)) | (block << 11)


def pitch_options(module):
    fields = {
        "partialPitch": ("compat_legacy", 1, 2),
        "fbPortaPause": ("compat_extended", 10, 0),
        "oldOctaveBoundary": ("compat_extended", 14, 0),
        "pitchMacroIsLinear": ("compat_extended", 12, 1),
        "pitchSlideSpeed": ("compat_extended", 13, 4),
        "noSlidesOnFirstTick": ("compat_extended", 1, 0),
        "limitSlides": ("compat_legacy", 0, 0),
        "targetResetsSlides": ("compat_legacy", 9, 1),
        "noteOffResetsSlides": ("compat_legacy", 8, 1),
        "stopPortaOnNoteOff": ("compat_legacy", 14, 0),
        "ignoreDuplicateSlides": ("compat_legacy", 13, 0),
        "buggyPortaAfterSlide": ("compat_extended", 4, 0),
        "brokenPortaLegato": ("compat_more", 0, 0),
    }
    result = {key: default for key, (_, _, default) in fields.items()}
    if module.version in (156, 232):
        for key, (group, index, default) in fields.items():
            values = module.flags.get(group, [])
            if index < len(values):
                result[key] = values[index]
    else:
        for encoded in module.flags.get("compat", []):
            for line in Reader(bytes.fromhex(encoded)).string().splitlines():
                key, separator, value = line.partition("=")
                key = key.strip()
                if separator and key in result:
                    try:
                        result[key] = int(value.strip())
                    except ValueError as e:
                        raise FormatError("invalid pitch compatibility value") from e
    result["partialPitch"] = (
        module.version in (156, 232) and result["partialPitch"] == 1
    )
    return result


class Pitch:
    """Bounded FM pitch automation; chip envelopes remain entirely hardware driven."""

    def __init__(
        self, linear, region, tuning, continuous=False, options=None, arp_options=None
    ):
        from .arpeggio import Arpeggio

        from .note_timing import DelayedLegato
        self.delayed_legato = DelayedLegato()
        self.tracker_arp = Arpeggio(arp_options)
        self.options = options or {}
        self.base = 0
        self.target = 0
        self.slide_speed = 0
        self.slide_kind = 0
        self.legato = False
        self.gate = False
        self.fresh = False
        self.row_tick = 0
        self.reset_on_next = False
        self.linear = linear
        self.region = region
        self.tuning = tuning
        self.partial = bool(self.options.get("partialPitch"))
        self.pause_freq = None
        self.continuous = continuous
        self.vibrato_rate = 0
        self.vibrato_depth = 0
        self.vibrato_phase = 0
        self.note = None
        self.fine = 0
        self.pitch = 0
        self.arp = 0
        self.last = None
        self.cursors = {}
        self.active = False
        self.released = False

    def begin(self, cell, instrument):
        from .macros import sequences, Cursor

        self.fresh = False
        self.row_tick = 0
        self.delayed_legato.begin(cell)
        if cell.instrument is not None:
            sequences(instrument, (0, 1, 4), 1)
        last_slide = 0
        for _, effect, value in cell.effects:
            value = value or 0
            if effect in (1, 2, 3):
                if not (self.linear or self.partial):
                    raise FormatError("FM slides require full linear pitch")
                if effect == 3 and self.options.get("buggyPortaAfterSlide", False):
                    raise FormatError(
                        "legacy portamento-after-slide behavior is unsupported"
                    )
                if (
                    effect in (1, 2)
                    and self.options.get("ignoreDuplicateSlides", False)
                    and last_slide in (effect, 3)
                ):
                    continue
                last_slide = effect
                self.slide_speed = value
                self.slide_kind = effect if value else 0
                if effect == 1:
                    self.target = self.position(
                        156 if self.options.get("limitSlides", False) else 179
                    )
                elif effect == 2:
                    self.target = self.position(
                        60 if self.options.get("limitSlides", False) else 0
                    )
                elif self.note is not None:
                    self.target = self.position(self.note)
            if effect == 0xEA:
                if self.options.get("brokenPortaLegato", False):
                    raise FormatError(
                        "legacy legato/portamento behavior is unsupported"
                    )
                self.legato = bool(value)
            if effect == 0x04:
                if not (self.linear or self.partial):
                    raise FormatError("FM vibrato requires full linear pitch")
                self.vibrato_rate = value >> 4
                self.vibrato_depth = value & 15
            if effect == 0xE5:
                if not (self.linear or self.partial):
                    raise FormatError("FM E5 requires full linear pitch")
                self.fine = value - 128
        if cell.note is None:
            self.tracker_arp.begin(cell, False)
            return
        if cell.note < 180:
            self.note = cell.note
            if self.slide_kind == 3:
                self.target = self.position(self.note)
            self.fresh = not self.legato and not (self.slide_kind == 3 and self.gate)
            if self.fresh:
                macros = sequences(instrument, (0, 1, 4), 1)
                if 4 in macros and not self.linear:
                    raise FormatError("FM pitch macro requires full linear pitch")
                if self.partial and 1 in macros:
                    raise FormatError("FM arpeggio macro requires full linear pitch")
                self.cursors = {
                    code: Cursor(sequence)
                    for code, sequence in macros.items()
                    if code != 0
                }
                self.base = self.position(self.note)
                self.pitch = 0
                self.arp = 0
                self.last = None
                self.active = True
                self.released = False
                self.pause_freq = None
            elif self.legato and self.slide_kind != 3:
                self.base = self.position(self.note)
            if not self.continuous and not (self.slide_kind == 3 and self.gate):
                self.vibrato_phase = 0
            if not self.gate and self.reset_on_next:
                self.slide_speed = 0
                self.slide_kind = 0
                self.reset_on_next = False
            if self.fresh:
                self.gate = True
        elif cell.note in (180, 181):
            self.release(cell.note)
        elif cell.note == 182:
            self.released = True
        else:
            raise FormatError("unsupported FM note command")
        self.tracker_arp.begin(cell, self.fresh)

    def position(self, note):
        return (
            frequency(note, self.region, self.tuning, False)
            if self.partial
            else note * 128
        )

    def partial_slide(self):
        # Furnace v0.6.8.3 OPN portamento: F-number steps and a pending
        # octave crossing, rather than logarithmic semitone increments.
        step = self.slide_speed
        paused = self.pause_freq is not None
        if paused:
            if self.options.get("oldOctaveBoundary"):
                fnum = self.base & 2047
                fnum = (
                    fnum >> 1
                    if (self.pause_freq & 0xF800) > (self.base & 0xF800)
                    else fnum << 1
                )
                self.base = fnum | (self.pause_freq & 0xF800)
                step *= 2
            else:
                self.base = self.pause_freq
        new = (
            min(self.target, self.base + step)
            if self.target > self.base
            else max(self.target, self.base - step)
        )
        clock = (53693175 if self.region == "ntsc" else 53203424) / 7
        bottom = int(self.tuning * 2**0.25 * 9440540 / clock)
        top = int(self.tuning * 2**1.25 * 9440540 / clock)
        if not paused:
            fnum = new & 2047
            block = new & 0xF800
            if fnum > top and block < 0x3800:
                if self.options.get("fbPortaPause"):
                    self.pause_freq = bottom | ((new + 0x800) & 0xF800)
                    return
                new = (fnum >> 1) | ((new + 0x800) & 0xF800)
            if (new & 2047) < bottom and (new & 0xF800) > 0:
                if self.options.get("fbPortaPause"):
                    self.pause_freq = (top - 1) | ((new - 0x800) & 0xF800)
                    return
                new = ((new & 2047) << 1) | ((new - 0x800) & 0xF800)
        self.pause_freq = None
        self.base = new

    def release(self, note):
        self.gate = False
        self.tracker_arp.gate = False
        if note == 180:
            self.cursors = {}
        else:
            self.released = True
        if self.slide_kind == 3 and self.options.get("noteOffResetsSlides", True):
            self.reset_on_next = True
            if self.options.get("stopPortaOnNoteOff", False):
                self.slide_speed = 0
                self.slide_kind = 0

    def tick(self, cut=None):
        shift = self.delayed_legato.tick()
        if shift and self.note is not None:
            self.note += shift
            self.base = self.position(self.note)
        vibrato = 0
        if self.vibrato_depth:
            self.vibrato_phase = (self.vibrato_phase + self.vibrato_rate) & 63
            wave = int(127 * math.sin(self.vibrato_phase * math.tau / 64))
            vibrato = math.trunc(((self.vibrato_depth * wave * 15) >> 4) / 15)
        first = self.row_tick == 0
        self.row_tick += 1
        if not self.active:
            self.tracker_arp.tick(self.note, bool(self.slide_speed))
            return None
        if self.slide_speed and not (
            first and self.options.get("noSlidesOnFirstTick", False)
        ):
            if self.partial:
                self.partial_slide()
            else:
                step = self.slide_speed * self.options.get("pitchSlideSpeed", 4)
                self.base = (
                    min(self.target, self.base + step)
                    if self.target > self.base
                    else max(self.target, self.base - step)
                )
            if self.base == self.target and self.options.get(
                "targetResetsSlides", True
            ):
                self.note = self.note if self.partial else self.target // 128
                self.slide_speed = 0
                self.slide_kind = 0
        if cut is not None:
            self.release(cut)
        arp_note = self.tracker_arp.tick(self.note, bool(self.slide_speed))
        if arp_note is not None:
            self.base = self.position(arp_note)
        for code, cursor in self.cursors.items():
            value = cursor.step(self.released)
            if value is None:
                continue
            if code == 1:
                self.arp = value
            else:
                self.pitch = (
                    max(-1048576, min(1048575, self.pitch + value))
                    if cursor.sequence.mode
                    else value
                )
        if self.partial:
            pitch = max(-2048, min(2047, self.fine + vibrato))
            multiplier = int(1024 * 2 ** (pitch / 1536) + 0.5)
            fnum = ((self.base & 2047) * multiplier) >> 10
            block = self.base >> 11
            if fnum > 2047:
                while block < 7:
                    fnum >>= 1
                    block += 1
            current = (block << 11) | min(2047, max(0, fnum))
        else:
            current = frequency(
                self.base / 128 + self.arp + (self.pitch + self.fine + vibrato) / 128,
                self.region,
                self.tuning,
            )
        if current == self.last:
            return None
        self.last = current
        return current


def validate_loop_entry(cell, effects):
    """Expanded automation can repeat only when its pitch state restarts."""
    values = {effect: (value or 0) for _, effect, value in cell.effects}
    if effects & {1, 2, 3}:
        slides = [
            (effect, value or 0)
            for _, effect, value in cell.effects
            if effect in (1, 2, 3)
        ]
        if (
            cell.note is None
            or cell.note >= 180
            or not slides
            or slides[-1][0] == 3
            and slides[-1][1]
        ):
            raise FormatError(
                "FM slide loop entry requires a fresh note and explicit slide restart/cancel"
            )
    if 0xEA in effects and values.get(0xEA) != 0:
        raise FormatError("FM legato loop entry requires EA00")


class Level:
    """Authored FM volume and bounded volume sequence, compiled to four TLs."""

    def __init__(self, modern=True, ceil=False, slide_options=None, attenuation=0):
        from .volume import Slide

        self.slide = Slide(127, slide_options)
        self.modern = modern
        self.ceil = ceil
        self.volume = 127
        self.macro = 127
        self.attenuation = attenuation
        self.cursor = None
        self.released = False
        self.last = None
        self.instrument = None
        self.instrument_id = None
        self.operator_levels = [None] * 4

    def begin(self, cell, instrument, fresh):
        from .macros import sequences, Cursor

        if cell.instrument is not None and cell.instrument != self.instrument_id:
            self.operator_levels = [None] * 4
            self.instrument_id = cell.instrument
        self.instrument = instrument
        self.slide.begin(cell)
        if cell.volume is not None:
            self.volume = cell.volume
        if fresh:
            sequence = sequences(instrument, (0, 1, 4), 1).get(0)
            self.cursor = Cursor(sequence) if sequence else None
            self.macro = 127
            self.released = False
        if cell.note == 180:
            self.cursor = None
        elif cell.note in (181, 182):
            self.released = True
        for _, effect, value in cell.effects:
            if 0x12 <= effect <= 0x15:
                if type(value) is not int or not 0 <= value <= 127:
                    raise FormatError(f"{effect:02X}xx operator level must be 0..127")
                # Effects use musical order 1,2,3,4; patches use 1,3,2,4.
                self.operator_levels[(0, 2, 1, 3)[effect - 0x12]] = value

    def tick(self, cut=None):
        if cut == 180:
            self.cursor = None
        elif cut == 181:
            self.released = True
        self.volume = self.slide.tick()
        if self.instrument is None:
            return None
        if self.cursor:
            value = self.cursor.step(self.released)
            if value is not None:
                self.macro = value
        return self.render()

    def render(self):
        """Publish current levels without advancing a macro or volume slide."""
        if self.instrument is None:
            return None
        from .compiler import fm_patch

        volume = (
            max(0, self.volume + self.macro - 127)
            if self.modern
            else (self.volume * self.macro + (126 if self.ceil else 0)) // 127
        )
        patch = fm_patch(
            self.instrument,
            volume,
            self.modern,
            self.ceil,
            self.attenuation,
            self.operator_levels,
        )
        result = patch[4:8] + patch[31:32]
        if result == self.last:
            return None
        self.last = result
        return result


def volume_options(module):
    if module.version in (156, 232):
        return bool(module.flags.get("compat_extended", [1] * 28)[16]), bool(
            module.flags.get("compat_more", [0] * 8)[5]
        )
    values = {"newVolumeScaling": 1, "ceilVolumeScaling": 0}
    for encoded in module.flags.get("compat", []):
        for line in Reader(bytes.fromhex(encoded)).string().splitlines():
            key, separator, value = line.partition("=")
            key = key.strip()
            if separator and key in values:
                try:
                    values[key] = int(value.strip())
                except ValueError as e:
                    raise FormatError("invalid volume compatibility value") from e
    return bool(values["newVolumeScaling"]), bool(values["ceilVolumeScaling"])
