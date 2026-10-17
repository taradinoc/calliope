"""Furnace panning on the Genesis YM2612's binary stereo outputs."""

from .furnace import FormatError

EFFECTS = (0x08, 0x80)


def value_byte(value, effect):
    value = 0 if value is None else value
    if type(value) != int or not 0 <= value <= 255:
        raise FormatError(f"{effect:02X}xx panning value must be 00..FF")
    return value


def linear(value):
    value = value_byte(value, 0x80)
    return 0x80 if value == 0 else 0x40 if value == 255 else 0xC0


def split(value):
    value = value_byte(value, 0x08)
    # Furnace's Genesis backend treats two zero sides as centered.
    if value == 0:
        return 0xC0
    return (0x80 if value & 0xF0 else 0) | (0x40 if value & 0x0F else 0)


def row(cell):
    result = None
    for _, effect, value in cell.effects:
        if effect == 0x80:
            result = linear(value)
        elif effect == 0x08:
            result = split(value)
    return result
