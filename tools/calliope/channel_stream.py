"""Partition compiled ticks into one conductor and implicit channel streams."""

from .furnace import FormatError
from .music_sync import LENGTHS
from .timing import CommandStream

WIDTHS = {**LENGTHS, 0: 1, 0x38: 1, 0x39: 1}


def split(commands, count, terminal, synchronized=False, event_part=None):
    packets = [[] for _ in range(count + 1)]
    part = None
    tick = 0
    at = 0
    while at < len(commands):
        op = commands[at]
        size = 2 + commands[at + 1] if op in (0x30, 0x35) else 1 + WIDTHS.get(op, -1)
        if not size or at + size > len(commands):
            raise FormatError("invalid channel stream instruction")
        operation = commands[at : at + size]
        at += size
        if op == 0:
            tick += operation[1]
            continue
        if op == 0x14:
            part = operation[1]
            if part >= count:
                raise FormatError("invalid channel stream part")
            continue
        if op == 0x38:
            continue
        if op == 0x20 and synchronized:
            # External notifications keep their logical tick and source order,
            # but do not delay PCM preparation and the first chip channels.
            destination = 1 + (count - 1 if event_part is None else event_part)
        elif op in (0x15, 0x20, 0x34, 0x39):
            destination = 0
        elif part is None:
            raise FormatError("channel operation without a part")
        else:
            destination = part + 1
            if op in (0x10, 0x11, 0x13, 0x16, 0x36, 0x37):
                operation = operation[:1] + operation[2:]
        packets[destination].append((tick, operation))
    streams = []
    for index, packet in enumerate(packets):
        stream = CommandStream(compact=True)
        previous = 0
        for when, operation in packet:
            stream.wait(when - previous)
            stream.extend(operation)
            previous = when
        stream.wait(tick - previous)
        streams.append(stream.finish() + bytes((terminal if index == 0 else 0xFD,)))
    return streams
