"""Build-time expansion of fixed-clock PSG effects.
Only bounded sequence volume envelopes are accepted; no runtime macro VM.
"""

import math
from .furnace import Reader, FormatError
from .note_timing import cuts, delay_policy
from .panning import EFFECTS as PAN_EFFECTS, row as row_pan


def partial_pitch(module):
    return (
        module.version in (156, 232)
        and module.flags.get("compat_legacy", [0, 0])[1] == 1
    )


def tone_period(note, module, region, fine=0, noise=False):
    clock = (53693175 if region == "ntsc" else 53203424) / 15
    divider = 64 if noise else 32
    if partial_pitch(module):
        base = int(
            clock / (divider * module.tuning * 2 ** ((note - fine / 128 - 93) / 12))
            + 0.5
        )
        pitch = max(-2048, min(2047, fine))
        value = (base * int(1024 * 2 ** (-pitch / 1536) + 0.5)) // 1024
    else:
        value = int(clock / (divider * module.tuning * 2 ** ((note - 93) / 12)) + 0.5)
    value = min(1023, max(0, value))
    if module.flags.get("compat_extended", [0] * 28)[21] and (
        note > 153 if noise else value < 8
    ):
        value = 1
    return value


def compile_tone(
    module, sub, channel, region, name, noise=False, coupled=False, arp_channels=None
):
    if not noise:
        return compile_tone_sequences(module, sub, channel, region, name, arp_channels)
    song = module.subsongs[sub]
    if song.hz != 60:
        raise FormatError("PSG SFX requires a 60 Hz tick clock")
    if module.flags.get("compat_extended", [0])[0] and len(set(song.speeds)) > 1:
        raise FormatError("legacy speed selection is unsupported")
    if module.flags.get("timebase", 0) or song.virtual[0] != song.virtual[1]:
        raise FormatError("PSG SFX requires timebase zero and virtual tempo 1:1")
    noise_mode = 0x11
    last_noise = None
    instrument = None
    from .volume import options as slide_options

    envelope = VolumeEnvelope(False, True, slide_options(module))
    output = bytearray()
    pending = 0

    def emit(values):
        nonlocal pending
        while pending:
            n = min(pending, 255)
            output.extend((0, n))
            pending -= n
        output.extend((0x30, len(values), *values))

    for order in range(len(song.orders[channel])):
        for row in range(song.rows):
            cell = module.cell(sub, order, row, channel)
            context = f"{name}: order {order}, row {row}, channel {channel}"
            if cell.instrument is not None:
                if cell.instrument >= len(module.instruments):
                    raise FormatError(context + ": invalid instrument")
                instrument = module.instruments[cell.instrument]
            stop = False
            mode_written = False
            for _, effect, value in cell.effects:
                if effect in PAN_EFFECTS:
                    row_pan(cell)
                    continue
                if noise and effect == 0x20:
                    if value is None or value & ~0x11:
                        raise FormatError(
                            context + ": noise mode must be 00, 01, 10 or 11"
                        )
                    if cell.note is None:
                        raise FormatError(
                            context + ": noise mode changes currently require a note"
                        )
                    noise_mode = value
                    mode_written = True
                    continue
                if effect in (0x0A, 0xE0, 0xEC, 0xFC):
                    continue
                if effect != 255:
                    raise FormatError(
                        context + f": unsupported PSG SFX effect {effect!r}"
                    )
                stop = True
            try:
                envelope.begin(cell, instrument)
            except FormatError as e:
                raise FormatError(context + ": " + str(e)) from e
            if cell.note is not None:
                if cell.note < 180:
                    clock = (53693175 if region == "ntsc" else 53203424) / 15
                    period = min(
                        1023,
                        max(
                            0,
                            round(
                                clock
                                / (32 * module.tuning * 2 ** ((cell.note - 93) / 12))
                            ),
                        ),
                    )
                    if module.flags.get("compat_extended", [0] * 28)[21] and period < 8:
                        period = 1
                    if noise:
                        derived = bool(noise_mode & 0x10)
                        if derived and not coupled:
                            raise FormatError(
                                context
                                + ": tone-derived noise requires tone 3 and noise tracks"
                            )
                        if not derived and cell.note % 12 > 2:
                            raise FormatError(
                                context + ": fixed noise notes must be C, C# or D"
                            )
                        control = (
                            0xE0
                            | ((noise_mode & 1) << 2)
                            | (3 if derived else 2 - cell.note % 12)
                        )
                        if control != last_noise or mode_written:
                            emit((control,))
                            last_noise = control
                            if derived:
                                emit((0xDF,))
                        if derived:
                            period = min(
                                1023,
                                max(
                                    0,
                                    round(
                                        clock
                                        / (
                                            64
                                            * module.tuning
                                            * 2 ** ((cell.note - 93) / 12)
                                        )
                                    ),
                                ),
                            )
                            emit((0xC0 | (period & 15), (period >> 4) & 63))
                    else:
                        emit((0x80 | (period & 15), (period >> 4) & 63))
            ticks = song.speeds[(order * song.rows + row) % len(song.speeds)]
            try:
                cut = cuts(cell, ticks, delay_policy(module))
            except FormatError as e:
                raise FormatError(context + ": " + str(e)) from e
            for tick in range(ticks):
                attenuation = envelope.tick(cut.get(tick))
                if attenuation is not None:
                    emit((0xF0 | attenuation,))
                pending += 1
            if stop:
                break
        if stop:
            break
    while pending:
        n = min(pending, 255)
        output.extend((0, n))
        pending -= n
    output.append(255)
    return bytes(output)


def compile_noise(module, sub, channels, region, name):
    return compile_psg(module, sub, channels, region, name)


def compile_psg(module, sub, channels, region, name):
    """Merge authored channels at tick boundaries, preserving source write order."""
    streams = []
    has_tone3 = any(module.channels[ch][1:] == ("psg", 2) for ch in channels)
    for ch in channels:
        _, kind, physical = module.channels[ch]
        if kind == "psg":
            streams.append(
                (
                    compile_tone(module, sub, ch, region, name, arp_channels=channels),
                    physical << 5,
                )
            )
        else:
            streams.append(
                (compile_tone(module, sub, ch, region, name, True, has_tone3), 0)
            )
    timeline = {}
    ends = []
    for data, remap in streams:
        tick = 0
        p = 0
        while data[p] != 255:
            op = data[p]
            p += 1
            if op == 0:
                tick += data[p]
                p += 1
                continue
            count = data[p]
            p += 1
            values = data[p : p + count]
            p += count
            if remap:
                values = bytes((v | remap) if v & 0x80 else v for v in values)
            timeline.setdefault(tick, []).append(values)
        ends.append(tick)
    end = min(ends)
    out = bytearray()
    tick = 0

    def wait(count):
        while count:
            n = min(count, 255)
            out.extend((0, n))
            count -= n

    for when, commands in sorted(timeline.items()):
        if when >= end:
            break
        wait(when - tick)
        tick = when
        for values in commands:
            out.extend((0x30, len(values), *values))
    wait(end - tick)
    out.append(255)
    return bytes(out)


class VolumeEnvelope:
    """Volume and optional pitch automation, expanded to sparse semantic writes."""

    def __init__(
        self,
        allow_pitch=False,
        linear=True,
        slide_options=None,
        arp_options=None,
        partial=False,
        continuous=False,
    ):
        from .arpeggio import Arpeggio

        from .note_timing import DelayedLegato
        self.delayed_legato = DelayedLegato()
        self.tracker_arp = Arpeggio(arp_options, True)
        self.tracker_base = None
        from .volume import Slide

        self.slide = Slide(15, slide_options)
        # The SMS backend ignores duty/noise-mode macros on tone channels.
        self.allowed = (0, 1, 2, 4) if allow_pitch else (0,)
        self.linear = linear
        self.partial = partial
        self.continuous = continuous
        self.cursors = {}
        self.vibrato_rate = 0
        self.vibrato_depth = 0
        self.vibrato_phase = 0
        self.effective_fine = 0
        self.macro = None
        self.released = False
        self.active = False
        self.porta_options = {}
        self.porta_speed = 0
        self.porta_target = 0
        self.porta_position = 0
        self.porta_first = True
        self.volume = 15
        self.macro_volume = 15
        self.last = 15
        self.note = None
        self.arp = 0
        self.pitch = 0
        self.fine = 0
        self.pitch_note = None
        self.pitch_changed = False

    def begin(self, cell, instrument):
        from .macros import sequences, Cursor

        self.porta_first = True
        self.delayed_legato.begin(cell)
        for _, effect, value in cell.effects:
            if effect == 3:
                if not self.linear:
                    raise FormatError("PSG portamento requires full linear pitch")
                if self.porta_options.get("buggyPortaAfterSlide"):
                    raise FormatError("legacy PSG portamento is unsupported")
                self.porta_speed = value or 0
        sliding = bool(self.porta_speed and self.active)
        if sliding and cell.note is not None and cell.note < 180:
            self.porta_target = cell.note * 128
        self.slide.begin(cell)
        self.tracker_arp.begin(cell, not sliding)
        if cell.instrument is not None:
            sequences(instrument, self.allowed)
        if cell.volume is not None:
            if not 0 <= cell.volume <= 15:
                raise FormatError("PSG volume outside 0..15")
            self.volume = cell.volume
        for _, effect, value in cell.effects:
            if effect == 4:
                if not (self.linear or self.partial):
                    raise FormatError("PSG vibrato requires linear pitch")
                self.vibrato_rate = (value or 0) >> 4
                self.vibrato_depth = (value or 0) & 15
            if effect == 0xE5 and 4 in self.allowed:
                if not (self.linear or self.partial):
                    raise FormatError("PSG E5 requires linear pitch")
                self.fine = (value or 0) - 128
        if cell.note is not None:
            if cell.note < 180 and not sliding:
                self.porta_position = cell.note * 128
                self.porta_target = self.porta_position
                macros = sequences(instrument, self.allowed)
                if 4 in macros and not self.linear:
                    raise FormatError("pitch macros require linear pitch")
                self.cursors = {
                    code: Cursor(sequence) for code, sequence in macros.items()
                }
                self.macro = macros or None
                self.released = False
                self.active = True
                self.macro_volume = 15
                self.last = None
                if not self.continuous:
                    self.vibrato_phase = 0
                self.note = cell.note
                self.tracker_base = cell.note
                self.arp = 0
                self.pitch = 0
                self.pitch_note = None
            elif cell.note == 180:
                self.active = False
                self.cursors = {}
                self.macro = None
                self.porta_speed = 0
            elif cell.note in (181, 182):
                self.released = True
            elif cell.note >= 180:
                raise FormatError("unsupported PSG note command")

    def tick(self, cut=None):
        shift = self.delayed_legato.tick()
        if shift and self.note is not None:
            self.note += shift
            self.porta_position = self.note * 128
            self.tracker_base = self.note
        if cut == 180:
            self.active = False
            self.cursors = {}
            self.macro = None
        elif cut == 181:
            self.released = True
        if cut is not None:
            self.tracker_arp.gate = False
        if cut is not None and self.porta_options.get("noteOffResetsSlides", True):
            self.porta_speed = 0
        if (
            self.active
            and self.porta_speed
            and not (self.porta_first and self.porta_options.get("noSlidesOnFirstTick"))
        ):
            step = self.porta_speed * self.porta_options.get("pitchSlideSpeed", 4)
            self.porta_position = (
                min(self.porta_target, self.porta_position + step)
                if self.porta_target > self.porta_position
                else max(self.porta_target, self.porta_position - step)
            )
            self.tracker_base = self.porta_position / 128
            if self.porta_position == self.porta_target and self.porta_options.get(
                "targetResetsSlides", True
            ):
                self.note = self.porta_target / 128
                self.porta_speed = 0
        self.porta_first = False
        arp_note = self.tracker_arp.tick(self.note, bool(self.porta_speed))
        if arp_note is not None:
            self.tracker_base = arp_note
        self.volume = self.slide.tick()
        self.pitch_changed = False
        vibrato = 0
        if self.vibrato_depth:
            self.vibrato_phase = (self.vibrato_phase + self.vibrato_rate) & 63
            wave = int(127 * math.sin(self.vibrato_phase * math.tau / 64))
            vibrato = math.trunc(((self.vibrato_depth * wave * 15) >> 4) / 15)
        self.effective_fine = self.fine + vibrato
        if self.active:
            for code, cursor in self.cursors.items():
                value = cursor.step(self.released)
                if value is None:
                    continue
                if code == 0:
                    self.macro_volume = value
                elif code == 1:
                    self.arp = value
                elif code == 4:
                    self.pitch = (
                        max(-32768, min(32767, self.pitch + value))
                        if cursor.sequence.mode
                        else value
                    )
            pitch_note = (
                self.tracker_base + self.arp + (self.pitch + self.effective_fine) / 128
            )
            self.pitch_changed = pitch_note != self.pitch_note
            self.pitch_note = pitch_note
        attenuation = (
            15 - max(0, self.volume + self.macro_volume - 15) if self.active else 15
        )
        if attenuation == self.last:
            return None
        self.last = attenuation
        return attenuation


def compile_tone_sequences(module, sub, channel, region, name, arp_channels=None):
    """Use the same bounded macro semantics for music and tone SFX."""
    from .macros import full_linear_pitch, continuous_vibrato

    song = module.subsongs[sub]
    if song.hz != 60:
        raise FormatError("PSG SFX requires a 60 Hz tick clock")
    if module.flags.get("timebase", 0) or song.virtual[0] != song.virtual[1]:
        raise FormatError("PSG SFX requires timebase zero and virtual tempo 1:1")
    if module.flags.get("compat_extended", [0])[0] and len(set(song.speeds)) > 1:
        raise FormatError("legacy speed selection is unsupported")
    from .volume import options as slide_options
    from .arpeggio import options as arp_options, speed as arp_speed

    envelope = VolumeEnvelope(
        True,
        full_linear_pitch(module),
        slide_options(module),
        arp_options(module),
        partial_pitch(module),
        continuous_vibrato(module),
    )
    envelope.tracker_arp.period = max(1, song.arp_speed)
    instrument = None
    output = bytearray()
    pending = 0
    phase = 0
    clock = (53693175 if region == "ntsc" else 53203424) / 15

    def emit(values):
        nonlocal pending
        while pending:
            count = min(pending, 255)
            output.extend((0, count))
            pending -= count
        output.extend((0x30, len(values), *values))

    stop = False
    for order in range(len(song.orders[channel])):
        for row in range(song.rows):
            cell = module.cell(sub, order, row, channel)
            context = f"{name}: order {order}, row {row}, channel {channel}"
            for _, effect, _ in cell.effects:
                if effect in PAN_EFFECTS:
                    row_pan(cell)
                    continue
                if effect not in (0xFF, 0xE5, 4, 0x0A, 0, 0xE0, 0xEC, 0xFC):
                    raise FormatError(
                        context + f": unsupported PSG SFX effect {effect!r}"
                    )
                if effect == 0xFF:
                    stop = True
            if cell.instrument is not None:
                if cell.instrument >= len(module.instruments):
                    raise FormatError(context + ": invalid instrument")
                instrument = module.instruments[cell.instrument]
            envelope.tracker_arp.period = arp_speed(
                envelope.tracker_arp.period,
                (
                    module.cell(sub, order, row, ch)
                    for ch in (arp_channels or [channel])
                ),
            )
            try:
                envelope.begin(cell, instrument)
            except FormatError as e:
                raise FormatError(context + ": " + str(e)) from e
            try:
                cut = cuts(cell, song.speeds[phase], delay_policy(module))
            except FormatError as e:
                raise FormatError(context + ": " + str(e)) from e
            for tick in range(song.speeds[phase]):
                volume = envelope.tick(cut.get(tick))
                if envelope.pitch_changed:
                    period = tone_period(
                        envelope.pitch_note, module, region, envelope.effective_fine
                    )
                    emit((0x80 | (period & 15), (period >> 4) & 63))
                if volume is not None:
                    emit((0x90 | volume,))
                pending += 1
            phase = (phase + 1) % len(song.speeds)
            if stop:
                break
        if stop:
            break
    while pending:
        count = min(pending, 255)
        output.extend((0, count))
        pending -= count
    output.append(255)
    return bytes(output)
