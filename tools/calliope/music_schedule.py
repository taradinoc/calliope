"""Prepare revision-13 Z80 channel streams without moving musical events.

Only a patch upload may move, into a preceding interval whose output carriers
are at maximum TL. The resident driver mutes every operator before changing
the algorithm, then commits the authored levels/pan at the original note.
Overlapping physical parts retain ordinary uploads, including all 16 voices.
"""

from bisect import bisect_right
from collections import Counter
from .channel_stream import WIDTHS
from .furnace import FormatError
from .timing import CommandStream


def prepare(streams, slots, terminals, synchronized=False, event_part=None, loop=None):
    packets = [[] for _ in range(len(slots) + 1)]
    starts, durations = [], []
    total = 0
    for order, commands in enumerate(streams):
        starts.append(total)
        part, tick, at = None, 0, 0
        while at < len(commands):
            op = commands[at]
            size = 2 + commands[at + 1] if op in (0x30, 0x35) else 1 + WIDTHS.get(op, -1)
            if not size or at + size > len(commands):
                raise FormatError("invalid music scheduling instruction")
            operation = commands[at:at + size]
            at += size
            if op == 0:
                tick += operation[1]
                continue
            if op == 0x14:
                part = operation[1]
                if part >= len(slots):
                    raise FormatError("invalid music scheduling part")
                continue
            if op == 0x38:
                continue
            if op == 0x20 and synchronized:
                destination = 1 + (len(slots) - 1 if event_part is None else event_part)
            elif op in (0x15, 0x20, 0x34, 0x39):
                destination = 0
            else:
                if part is None:
                    raise FormatError("music scheduling operation without a part")
                destination = part + 1
                if op in (0x10, 0x11, 0x13, 0x16, 0x36, 0x37):
                    operation = operation[:1] + operation[2:]
            packets[destination].append([total + tick, operation, order])
        durations.append(tick)
        total += tick

    occupied, prepared = set(), []
    counts = Counter(slots)
    for index, packet in enumerate(packets):
        unique_fm = index > 0 and slots[index - 1] < 5 and counts[slots[index - 1]] == 1
        silent, levels, previous_id = None, None, None
        previous_patch = last_level = -1
        updated, extra = [], []

        def full_note(at, when):
            following = []
            for t, v, _ in packet[at + 1:]:
                if t != when or v[0] == 0x10:
                    break
                following.append(v)
                if v[0] == 0x11:
                    break
            level = next((v for v in following if v[0] in (0x37, 0x11)), None)
            return (level is not None and level[0] == 0x37
                    and any(v[0] == 0x16 for v in following)
                    and following[-1][0] == 0x11)

        def reserve(when, last_patch, first_silent):
            if not unique_fm or first_silent is None:
                return None
            # No following old-instrument macro may unmute an operator which
            # becomes an output carrier under the preloaded algorithm.
            return next((t for t in range(max(first_silent + 1, last_patch + 1, last_level + 1), min(when, total))
                         if t not in occupied), None)

        def preload(when, instrument):
            owner = bisect_right(starts, when) - 1
            extra.append([when, bytes((0x3A, instrument)), owner])
            occupied.add(when)

        for at, (when, operation, owner) in enumerate(packet):
            if operation[0] == 0x10:
                full = full_note(at, when)
                same = previous_id == operation[1] and full
                early = reserve(when, previous_patch, silent) if full and not same else None
                previous_id = operation[1]
                if early is not None:
                    preload(early, operation[1])
                if early is not None or same:
                    operation = bytes((0x3B, operation[1]))
                previous_patch, silent = when, None
            elif operation[0] == 0x37:
                last_level = when
                levels = operation[1:5]
                muted = bool(operation[5]) and all(levels[i] == 127 for i in range(4) if operation[5] & (1 << i))
                if not muted:
                    silent = None
                elif silent is None:
                    silent = when
            updated.append([when, operation, owner])

        # A loop's first pass must still work without its preceding tail upload.
        # Short commits validate the physical patch ID and retain a full fallback.
        wrapped = set()
        if loop is not None:
            loop_base = starts[loop]
            for at, (when, operation, _) in enumerate(packet):
                if when < loop_base:
                    continue
                virtual = total + when - loop_base
                if operation[0] == 0x10:
                    full = full_note(at, when)
                    same = previous_id == operation[1] and full
                    early = reserve(virtual, previous_patch, silent) if full and not same else None
                    if early is not None:
                        preload(early, operation[1])
                    if early is not None or same:
                        wrapped.add(when)
                    break
                if operation[0] == 0x37:
                    last_level = virtual
                    levels = operation[1:5]
                    muted = bool(operation[5]) and all(levels[i] == 127 for i in range(4) if operation[5] & (1 << i))
                    if not muted:
                        silent = None
                    elif silent is None:
                        silent = virtual
        updated = [[t, bytes((0x3B, v[1])) if t in wrapped and v[0] in (0x10, 0x3B) else v, o]
                   for t, v, o in updated]

        previous, compact = None, []
        entered_loop = False
        for when, operation, owner in sorted(updated + extra, key=lambda item: item[0]):
            if loop is not None and when >= starts[loop] and not entered_loop:
                previous, entered_loop = None, True
            if operation[0] in (0x10, 0x3B):
                previous = None
            if operation[0] == 0x37:
                value, old = operation[1:], previous
                previous = value
                if old == value:
                    continue
                changes = [i for i in range(4) if old and old[i] != value[i]]
                if old and old[4] == value[4] and len(changes) == 1:
                    operator = changes[0]
                    operation = bytes((0x50 + operator, value[operator]))
            compact.append([when, operation, owner])
        prepared.append(compact)

    result = []
    for order, duration in enumerate(durations):
        group = []
        for index, packet in enumerate(prepared):
            stream, previous = CommandStream(compact=True), 0
            for when, operation, owner in packet:
                if owner != order:
                    continue
                local = when - starts[order]
                stream.wait(local - previous)
                stream.extend(operation)
                previous = local
            stream.wait(duration - previous)
            group.append(stream.finish() + bytes((terminals[order] if index == 0 else 0xFD,)))
        result.append(group)
    return result
