"""Resolve Furnace order flow to bounded sequential bundles and one loop.
Semantics: Furnace src/engine/playback.cpp, 0B/0D (jumpTreatment=0/1/2).
The runtime needs no jump interpreter; source coordinates remain in the map.
"""

from dataclasses import dataclass
from .furnace import FormatError, Reader


@dataclass(frozen=True)
class Flow:
    bundles: tuple
    loop: int | None


def compatibility(module):
    if module.version in (156, 232):
        flags = module.flags.get("compat_extended", [0] * 28)
        return flags[23], bool(flags[3])
    values = {}
    for encoded in module.flags.get("compat", []):
        for line in Reader(bytes.fromhex(encoded)).string().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in ("jumpTreatment", "ignoreJumpAtEnd"):
                try:
                    values[key.strip()] = int(value.strip())
                except ValueError as e:
                    raise FormatError("invalid flow compatibility value") from e
    return values.get("jumpTreatment", 0), bool(values.get("ignoreJumpAtEnd", 0))


def resolve(module, sub, channels, loop_order=None):
    song = module.subsongs[sub]
    count = len(song.orders[0])
    if loop_order is not None and (
        type(loop_order) is not int or not 0 <= loop_order < count
    ):
        raise FormatError("loop_order: expected a source order index")
    treatment, ignore_end = compatibility(module)
    position = (0, 0)
    visited = {}
    route = []
    loop_position = None
    while position is not None:
        if position in visited:
            loop_position = visited[position]
            break
        visited[position] = len(route)
        route.append(position)
        order, row = position
        target_order = None
        target_row = 0
        changed = False
        stop = False
        explicit_order = False
        for channel in sorted(channels):
            for _, effect, value in module.cell(sub, order, row, channel).effects:
                value = 0 if value is None else value
                if effect == 0xFF:
                    stop = True
                if effect not in (0x0B, 0x0D):
                    continue
                context = f"order {order}, row {row}, channel {channel}"
                if treatment not in (0, 1, 2):
                    raise FormatError(context + ": unknown 0B/0D treatment")
                if effect == 0x0B:
                    if target_order is None or treatment == 0:
                        target_order = value
                        changed = True
                        explicit_order = True
                        if treatment:
                            target_row = 0
                elif not (ignore_end and order == count - 1):
                    if treatment == 1 and explicit_order:
                        continue
                    if target_order is None or treatment == 2:
                        target_order = order + 1
                        explicit_order = False
                    target_row = value
                    changed = True
        if stop:
            position = None
        elif changed:
            if target_row >= song.rows:
                raise FormatError(f"order {order}, row {row}: jump row outside pattern")
            # A break past the final order ends playback; no implicit Furnace loop.
            if target_order == count and not explicit_order:
                position = None
            elif not 0 <= target_order < count:
                raise FormatError(f"order {order}, row {row}: jump order outside song")
            else:
                position = (target_order, target_row)
        elif row + 1 < song.rows:
            position = (order, row + 1)
        elif order + 1 < count:
            position = (order + 1, 0)
        else:
            position = None if loop_order is None else (loop_order, 0)
    if loop_order is not None:
        wanted = visited.get((loop_order, 0))
        if wanted is None:
            raise FormatError("loop_order is unreachable")
        if loop_position is not None and wanted != loop_position:
            raise FormatError("loop_order conflicts with authored order flow")
    bundles = []
    current = []
    loop_bundle = None
    for index, coordinate in enumerate(route):
        contiguous = bool(current) and coordinate == (
            current[-1][0],
            current[-1][1] + 1,
        )
        if current and (not contiguous or index == loop_position):
            bundles.append(tuple(current))
            current = []
        if index == loop_position:
            loop_bundle = len(bundles)
        current.append(coordinate)
    if current:
        bundles.append(tuple(current))
    if len(bundles) > 256 or loop_bundle == 255:
        raise FormatError("compiled flow exceeds the 256-bundle/254-loop-index limit")
    return Flow(tuple(bundles), loop_bundle)
