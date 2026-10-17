"""Expand non-linear SN76489 period/noise macros without a runtime macro VM.

Legacy Furnace SN pitch macros subtract raw divider units. Legato retains
macro position. A held loop is accepted only when a second pass has identical
register state and noise-reset events at every tick.
"""

from .furnace import FormatError
from .macros import sequences, Cursor
from .psg import tone_period
from .panning import EFFECTS as PAN_EFFECTS, row as row_pan


def compile_register_psg(module, sub, channels, region, item):
    song = module.subsongs[sub]
    if song.hz != 60 or module.flags.get("timebase", 0) or song.virtual != (1, 1):
        raise FormatError("raw-period PSG effects require nominal 60 Hz timing")
    loop = item.get("loop")
    orders = len(song.orders[0])
    total = orders * song.rows

    def position(value):
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(type(v) != int for v in value)
        ):
            raise FormatError("loop boundary must be [order,row]")
        order, row = value
        if (
            not 0 <= order <= orders
            or not 0 <= row < song.rows
            or (order == orders and row)
        ):
            raise FormatError("loop boundary outside song")
        return order * song.rows + row

    start = end = None
    if loop is not None:
        if not isinstance(loop, dict) or set(loop) != {"start", "end"}:
            raise FormatError("Furnace SFX loop requires start/end order,row pairs")
        start, end = position(loop["start"]), position(loop["end"])
        if not start < end:
            raise FormatError("SFX loop must have positive duration")
        total = end
    if (
        sum(song.speeds) * (total // len(song.speeds))
        + sum(song.speeds[: total % len(song.speeds)])
        > 65536
    ):
        raise FormatError("raw-period PSG effect exceeds 65536 expanded ticks")
    states = {
        ch: {
            "note": None,
            "ins": None,
            "volume": 15,
            "pitch": 0,
            "duty": 3,
            "active": False,
            "legato": False,
            "released": False,
            "cursors": {},
        }
        for ch in channels
    }
    timeline = []
    boundaries = {}
    stopped = False

    def render(first, last):
        nonlocal stopped
        result = []
        for index in range(first, last):
            boundaries.setdefault(index, len(timeline) + len(result))
            order, row = divmod(index, song.rows)
            resets = {}
            for ch in channels:
                cell = module.cell(sub, order, row, ch)
                state = states[ch]
                for _, effect, value in cell.effects:
                    if effect in PAN_EFFECTS:
                        row_pan(cell)
                        continue
                    if effect == 0xEA:
                        state["legato"] = bool(value)
                    elif effect == 0xFF:
                        stopped = True
                    elif effect in (0x0B, 0x0D):
                        if loop is None or index != end - 1:
                            raise FormatError(
                                "raw PSG backward flow requires an explicit matching SFX loop"
                            )
                        expected = (
                            start // song.rows if effect == 0x0B else start % song.rows
                        )
                        if value != expected:
                            raise FormatError("authored SFX flow disagrees with loop")
                    elif effect == 0x20 and module.channels[ch][1] == "noise":
                        if value not in (0, 1, 16, 17):
                            raise FormatError("unsupported noise mode")
                        state["duty"] = (value & 1) + 2 * bool(value & 16)
                        resets[ch] = True
                    else:
                        raise FormatError(
                            f"unsupported raw-period PSG effect {effect!r}"
                        )
                if cell.instrument is not None:
                    if not 0 <= cell.instrument < len(module.instruments):
                        raise FormatError("invalid instrument")
                    state["ins"] = module.instruments[cell.instrument]
                if cell.volume is not None:
                    if not 0 <= cell.volume <= 15:
                        raise FormatError("PSG volume outside 0..15")
                    state["volume"] = cell.volume
                if cell.note is not None:
                    if cell.note < 180:
                        if not state["legato"] or not state["active"]:
                            allowed = (
                                (0, 1, 4, 2, 14)
                                if module.channels[ch][1] == "noise"
                                else (0, 1, 4)
                            )
                            state["cursors"] = {
                                code: Cursor(seq)
                                for code, seq in sequences(
                                    state["ins"], allowed
                                ).items()
                            }
                            state.update(pitch=0, released=False)
                            resets[ch] = True
                        state["note"] = cell.note
                        state["active"] = True
                    elif cell.note == 180:
                        state["active"] = False
                        state["cursors"] = {}
                    elif cell.note in (181, 182):
                        state["released"] = True
            ticks = song.speeds[index % len(song.speeds)]
            for tick in range(ticks):
                values = []
                for ch in channels:
                    state = states[ch]
                    arp = 0
                    volume = state["volume"]
                    reset = bool(tick == 0 and resets.get(ch))
                    for code, cursor in state["cursors"].items():
                        value = cursor.step(state["released"])
                        if value is not None:
                            state["macro" + str(code)] = value
                        current = state.get("macro" + str(code), 0 if code != 0 else 15)
                        if code == 0:
                            volume = max(0, volume + current - 15)
                        elif code == 1:
                            arp = current
                        elif code == 4:
                            if value is not None:
                                state["pitch"] = (
                                    max(-32768, min(32767, state["pitch"] + value))
                                    if cursor.sequence.mode
                                    else value
                                )
                        elif code == 2:
                            state["duty"] = current
                        elif code == 14:
                            reset = reset or bool(value)
                    physical = module.channels[ch][2]
                    noise = physical == 3
                    period = (
                        None
                        if state["note"] is None
                        else max(
                            1,
                            min(
                                1023,
                                tone_period(state["note"] + arp, module, region)
                                - state["pitch"],
                            ),
                        )
                    )
                    if (
                        noise
                        and state["duty"] >= 2
                        and 2 not in [module.channels[c][2] for c in channels]
                    ):
                        raise FormatError(
                            "tone-derived noise requires tone 3 and noise tracks"
                        )
                    if noise and state["duty"] < 2 and (state["note"] or 0) % 12 > 2:
                        raise FormatError("fixed noise notes must be C, C# or D")
                    control = (
                        0xE0
                        | ((state["duty"] & 1) << 2)
                        | (3 if state["duty"] >= 2 else 2 - (state["note"] or 0) % 12)
                    )
                    values.append(
                        (
                            physical,
                            period,
                            15 - volume if state["active"] else 15,
                            control if noise else None,
                            reset if noise else False,
                        )
                    )
                result.append(tuple(values))
            if stopped:
                break
        return result

    timeline = render(0, total)
    boundaries[total] = len(timeline)
    if loop is not None:
        if stopped:
            raise FormatError("SFX loop ends at a stop command")
        second = render(start, end)
        if timeline[boundaries[start] : boundaries[end]] != second:
            raise FormatError(
                "held PSG loop must preserve macro state and register resets"
            )
    output = bytearray()
    previous = {}
    loop_offset = None
    pending = 0

    def emit(values):
        nonlocal pending
        while pending:
            n = min(255, pending)
            output.extend((0, n))
            pending -= n
        output.extend((0x30, len(values), *values))

    for tick, values in enumerate(timeline):
        entry = loop is not None and tick == boundaries[start]
        if entry:
            while pending:
                n = min(255, pending)
                output.extend((0, n))
                pending -= n
            loop_offset = len(output)
        for physical, period, volume, control, reset in values:
            old = previous.get(physical)
            if physical == 3:
                if reset or old is None or old[2] != control:
                    emit((control,))
                if (
                    control & 3 == 3
                    and period is not None
                    and (entry or old is None or old[0] != period)
                ):
                    emit((0xC0 | (period & 15), (period >> 4) & 63))
            elif period is not None and (entry or old is None or old[0] != period):
                # Single-tone streams are relocated by the runtime allocator.
                shift = 0 if len(channels) == 1 else physical << 5
                emit((0x80 | shift | (period & 15), (period >> 4) & 63))
            if entry or old is None or old[1] != volume:
                shift = 0 if len(channels) == 1 and physical < 3 else physical << 5
                emit((0x90 | shift | volume,))
            previous[physical] = (period, volume, control)
        pending += 1
    while pending:
        n = min(255, pending)
        output.extend((0, n))
        pending -= n
    output.append(255)
    return bytes(output), loop_offset
