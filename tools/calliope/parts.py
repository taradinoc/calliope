"""Validate logical parts and compile compact song control metadata."""

from .furnace import FormatError


def layout(item, slots):
    voices = item["voices"]
    enabled = bool(
        "groups" in item or "arrangements" in item or len(set(slots)) != len(slots)
    )
    if not enabled:
        return b"", [], 0
    groups = item.get("groups", {})
    if not isinstance(groups, dict) or len(groups) > 16:
        raise FormatError("groups must be a mapping of at most 16 names to voice lists")
    if any(not isinstance(n, str) or not n for n in groups):
        raise FormatError("group names must be nonempty strings")
    names = [v.get("name") for v in voices]
    if any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(
        names
    ):
        raise FormatError("logical voices require unique nonempty names")

    def mask(members):
        if (
            not isinstance(members, list)
            or any(n not in names for n in members)
            or len(set(members)) != len(members)
        ):
            raise FormatError("part list contains unknown or duplicate voices")
        return sum(1 << names.index(n) for n in members)

    group_masks = [mask(members) for members in groups.values()]
    arrangements = item.get("arrangements", {"all": names})
    if not isinstance(arrangements, dict) or not 1 <= len(arrangements) <= 16:
        raise FormatError("expected 1..16 named arrangements")
    if any(not isinstance(n, str) or not n for n in arrangements):
        raise FormatError("arrangement names must be nonempty strings")
    masks = [mask(members) for members in arrangements.values()]
    for value in masks:
        selected = [slot for i, slot in enumerate(slots) if value & (1 << i)]
        if len(set(selected)) != len(selected):
            raise FormatError("arrangement has conflicting physical slots")
    initial = item.get("initial_arrangement", next(iter(arrangements)))
    if initial not in arrangements:
        raise FormatError("unknown initial arrangement")
    initial_index = list(arrangements).index(initial)
    data = bytearray()
    for i, slot in enumerate(slots):
        membership = sum(1 << g for g, m in enumerate(group_masks) if m & (1 << i))
        data.extend(
            (
                slot,
                membership & 255,
                membership >> 8,
                1 if masks[initial_index] & (1 << i) else 0,
            )
        )
    for m in masks:
        data.extend(m.to_bytes(2, "little"))
    return bytes(data), masks, initial_index
