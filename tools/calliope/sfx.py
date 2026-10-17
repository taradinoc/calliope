"""Validate explicit row-bounded SFX loops and retain semantic commands."""

from .furnace import FormatError


def apply_loop(data, item, module, sub, channels):
    loop = item.get("loop")
    if loop is None:
        return data, None
    if not isinstance(loop, dict) or set(loop) != {"start", "end"}:
        raise FormatError("Furnace SFX loop requires start/end order,row pairs")
    song = module.subsongs[sub]
    orders = len(song.orders[channels[0]])

    def position(value, end=False):
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(type(v) != int for v in value)
        ):
            raise FormatError("loop boundary must be [order,row]")
        order, row = value
        if (
            not 0 <= order < orders + (1 if end else 0)
            or not 0 <= row <= song.rows
            or (order == orders and row)
            or (not end and row == song.rows)
        ):
            raise FormatError("loop boundary outside song")
        count = order * song.rows + row
        cycles, tail = divmod(count, len(song.speeds))
        return cycles * sum(song.speeds) + sum(song.speeds[:tail])

    start = position(loop["start"])
    end = position(loop["end"], True)
    from .macros import continuous_vibrato

    if continuous_vibrato(module) and any(
        e == 4
        for ch in channels
        for order in range(orders)
        for row in range(song.rows)
        for _, e, _ in module.cell(sub, order, row, ch).effects
    ):
        raise FormatError("SFX vibrato loops require per-note phase reset")
    if end <= start:
        raise FormatError("SFX loop must have positive duration")
    from .arpeggio import validate_sfx_loop

    validate_sfx_loop(module, sub, channels, loop["start"], loop["end"])
    for ch in channels:
        cell = module.cell(sub, *loop["start"], ch)
        if cell.note is None or cell.note >= 180 or cell.instrument is None:
            raise FormatError(
                "SFX loop entry requires a fresh note and explicit instrument on each participating channel"
            )
        if module.channels[ch][1] == "fm":
            from .fm import validate_loop_entry

            validate_loop_entry(
                cell,
                {
                    e
                    for order in range(orders)
                    for row in range(song.rows)
                    for _, e, _ in module.cell(sub, order, row, ch).effects
                },
            )
        from .volume import validate_loop_entry as validate_volume_loop

        validate_volume_loop(
            cell,
            {
                e
                for order in range(orders)
                for row in range(song.rows)
                for _, e, _ in module.cell(sub, order, row, ch).effects
            },
        )
        # Fine pitch is persistent and is baked into sparse tone periods.
        # A repeated segment must establish it before its first note.
        if any(
            e == 0xE5
            for order in range(orders)
            for row in range(song.rows)
            for _, e, _ in module.cell(sub, order, row, ch).effects
        ) and not any(e == 0xE5 for _, e, _ in cell.effects):
            raise FormatError("SFX loop entry must reset fine pitch")
        if any(
            e == 4
            for order in range(orders)
            for row in range(song.rows)
            for _, e, _ in module.cell(sub, order, row, ch).effects
        ) and not any(e == 4 for _, e, _ in cell.effects):
            raise FormatError("SFX loop entry must reset vibrato")
        if module.channels[ch][1] == "noise" and not any(
            e == 0x20 for _, e, _ in cell.effects
        ):
            raise FormatError("noise loop entry requires an explicit 20xx mode")
    out = bytearray()
    tick = 0
    p = 0
    offset = 0 if start == 0 else None
    while p < len(data) and data[p] != 255 and tick < end:
        op = data[p]
        p += 1
        if op == 0:
            remaining = data[p]
            p += 1
            while remaining and tick < end:
                boundary = min(end, start if tick < start else end)
                n = min(remaining, boundary - tick, 255)
                out.extend((0, n))
                tick += n
                remaining -= n
                if tick == start:
                    offset = len(out)
        else:
            size = (
                6
                if op == 0x37
                else (
                    3
                    if op in (0x11, 0x36)
                    else (
                        2
                        if op in (0x10, 0x16)
                        else 1 if op == 0x13 else data[p] + 1 if op == 0x30 else None
                    )
                )
            )
            if size is None:
                raise FormatError("unsupported loop stream operation")
            out.append(op)
            out.extend(data[p : p + size])
            p += size
    if tick != end or offset is None:
        raise FormatError("loop boundary extends past SFX end")
    out.append(255)
    return bytes(out), offset


def relocate_tones(data, module, channels):
    """Keep packet widths/loop offsets; number independent tones by source order."""
    mapping = {module.channels[ch][2]: i for i, ch in enumerate(channels)}
    output = bytearray(data)
    p = 0
    while p < len(output):
        op = output[p]
        p += 1
        if op == 255:
            break
        if op == 0:
            p += 1
            continue
        if op != 0x30:
            raise FormatError("relocatable tones require PSG register packets")
        count = output[p]
        p += 1
        for i in range(p, p + count):
            value = output[i]
            if value & 128:
                ch = (value >> 5) & 3
                if ch not in mapping:
                    raise FormatError(
                        "relocatable tone stream references an undeclared resource"
                    )
                output[i] = (value & 0x9F) | (mapping[ch] << 5)
        p += count
    return bytes(output)
