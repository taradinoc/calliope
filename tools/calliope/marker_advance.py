"""Move external cues on the resolved music timeline before channel splitting."""

from bisect import bisect_right
from .channel_stream import WIDTHS
from .furnace import FormatError
from .timing import CommandStream


def advance(streams, ticks, loop=None):
    if not ticks:
        return streams
    starts = []
    packets = []
    events = []
    elapsed = 0
    for commands in streams:
        starts.append(elapsed)
        packet = {}
        at = 0
        tick = 0
        while at < len(commands):
            op = commands[at]
            size = (
                2 + commands[at + 1] if op in (0x30, 0x35) else 1 + WIDTHS.get(op, -1)
            )
            if not size or at + size > len(commands):
                raise FormatError("invalid marker advance instruction")
            operation = commands[at : at + size]
            at += size
            if op == 0:
                tick += operation[1]
            elif op == 0x20:
                events.append((elapsed + tick, operation))
            else:
                packet.setdefault(tick, bytearray()).extend(operation)
        packets.append(packet)
        elapsed += tick
    ends = starts[1:] + [elapsed]
    for when, operation in events:
        # Keep looping cues in the repeated region, including its first pass.
        floor = starts[loop] if loop is not None and when >= starts[loop] else 0
        target = max(floor, when - ticks)
        index = bisect_right(starts, target) - 1
        packets[index].setdefault(target - starts[index], bytearray()).extend(operation)
    result = []
    for start, end, packet in zip(starts, ends, packets):
        stream = CommandStream()
        previous = 0
        for when, operations in sorted(packet.items()):
            if len(operations) > 192:
                raise FormatError("marker advance tick exceeds 192 bytes")
            stream.wait(when - previous)
            stream.extend(operations)
            previous = when
        stream.wait(end - start - previous)
        result.append(stream.finish())
    return result
