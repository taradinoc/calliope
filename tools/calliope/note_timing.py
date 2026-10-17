"""Row-bounded note cuts/releases following Furnace playback.cpp."""

from .furnace import FormatError, Reader


def delay_policy(module):
    if module.version in (156, 232):
        return module.flags.get("compat_extended", [0] * 28)[22]
    result = 2
    for encoded in module.flags.get("compat", []):
        for line in Reader(bytes.fromhex(encoded)).string().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "delayBehavior":
                try:
                    result = int(value.strip())
                except ValueError as e:
                    raise FormatError("invalid delay compatibility value") from e
    return result


def cuts(cell, ticks, policy):
    result = {}
    for _, effect, value in cell.effects:
        if effect not in (0xEC, 0xFC):
            continue
        if policy not in (0, 1, 2):
            raise FormatError("unsupported delay compatibility policy")
        value = value or 0
        if effect == 0xEC and not value:
            continue
        if value >= ticks:
            if policy == 2:
                raise FormatError(
                    "cross-row lax note cuts/releases are unsupported; use strict delay behavior or a row-bounded cut"
                )
            continue
        result = {value: 180 if effect == 0xEC else 181}
    return result


def fm_delay(cell, ticks, policy):
    """Delay channel row processing, retaining the strict/lax bounds policy."""
    delay = 0
    for _, effect, value in cell.effects:
        if effect != 0xED:
            continue
        if policy not in (0, 1, 2):
            raise FormatError("unsupported delay compatibility policy")
        value = value or 0
        if type(value) is not int or not 0 <= value <= 255:
            raise FormatError("ED note delay must be 0..255")
        if not value:
            continue
        if value >= ticks:
            if value == ticks and policy in (1, 2):
                delay = value
                continue
            if policy == 2:
                raise FormatError(
                    "cross-row FM note delay is unsupported; use strict delay behavior or a row-bounded delay"
                )
            continue
        delay = value
    return delay
