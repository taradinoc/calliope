"""Separate PCM preparation from chip writes sharing its playback frontier."""

from .furnace import FormatError

PIPE = 0x38
BEGIN = 0
QUEUE = 1
END = 2
# Operand lengths in the compiled music instruction set.
LENGTHS = {
    0x10: 2,
    0x11: 3,
    0x13: 1,
    0x14: 1,
    0x15: 3,
    0x16: 2,
    0x20: 4,
    0x31: 3,
    0x32: 0,
    0x33: 2,
    0x34: 2,
    0x36: 3,
    0x37: 6,
}


def align_tick(commands):
    if not commands:
        return bytes(commands)
    setup = bytearray()
    pcm = bytearray()
    chips = bytearray()
    part = None
    at = 0
    selected = {id(setup): None, id(pcm): None, id(chips): None}
    while at < len(commands):
        op = commands[at]
        size = (2 + commands[at + 1]) if op in (0x30, 0x35) else 1 + LENGTHS.get(op, -1)
        if not size or at + size > len(commands):
            raise FormatError("invalid music alignment instruction")
        operation = commands[at : at + size]
        at += size
        if op == 0x14:
            part = operation[1]
            continue
        dest = (
            pcm
            if op in (0x31, 0x32)
            else setup if op in (0x10, 0x15, 0x20, 0x34) else chips
        )
        if (
            op not in (0x15, 0x20, 0x34)
            and part is not None
            and selected[id(dest)] != part
        ):
            dest.extend((0x14, part))
            selected[id(dest)] = part
        dest.extend(operation)
    if not chips:
        return bytes((PIPE, BEGIN)) + setup + pcm
    return (
        bytes((PIPE, BEGIN))
        + setup
        + pcm
        + bytes((PIPE, QUEUE))
        + chips
        + bytes((PIPE, END))
    )
