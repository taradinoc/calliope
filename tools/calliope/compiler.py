"""Compile a sound bank, retaining pattern/order structure.
BGM1 revision 12 is experimental: unsupported runtime features fail closed.
"""

from dataclasses import asdict
from pathlib import Path
import hashlib
import math
import json
import re
import struct
from .furnace import parse, Reader, FormatError, Cell
from .pcm import convert, resample, TIMER_A
from .psg import (
    partial_pitch,
    tone_period,
    compile_tone,
    compile_noise,
    compile_psg,
    VolumeEnvelope,
)
from .parts import layout as parts_layout
from .timing import Timing, CommandStream, rate5, TIMING_EFFECTS
from .sfx import apply_loop, relocate_tones
from .macros import full_linear_pitch
from .flow import resolve as resolve_flow
from .fm import (
    frequency as fm_frequency,
    Pitch as FMPitch,
    Level as FMLevel,
    pitch_options,
    volume_options,
)
from .macros import sequences, continuous_vibrato
from .sample_voice import SampleVoice
from .music_sync import align_tick
from .note_timing import cuts, delay_policy, fm_delay
from .arpeggio import options as arp_options, speed as arp_speed
from .volume import (
    options as slide_options,
    validate_loop_entry as validate_volume_loop,
)
from .channel_stream import split as split_channels
from .marker_advance import advance as advance_markers
from .panning import EFFECTS as PAN_EFFECTS, row as row_pan

OP_WAIT = 0
OP_PATCH = 0x10
OP_NOTE = 0x11
OP_VOLUME = 0x12
OP_OFF = 0x13
OP_EVENT = 0x20
OP_PATTERN_END = 0xFD
OP_END = 0xFF
SLOTS = {
    "fm0": 0,
    "fm1": 1,
    "fm2": 2,
    "fm3": 3,
    "fm4": 4,
    "tone0": 5,
    "tone1": 6,
    "tone2": 7,
    "noise": 8,
    "pcm": 9,
}


def integer(v, low, high, label):
    if type(v) != int or not low <= v <= high:
        raise FormatError(f"{label}: expected integer {low}..{high}")
    return v


def load_manifest(path):
    path = Path(path).resolve()
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise FormatError(f"{path}: {e}") from e
    return validate_manifest(m)


def validate_manifest(m):
    if not isinstance(m, dict):
        raise FormatError("bank manifest must be an object")
    if m.get("version") != 1:
        raise FormatError("bank manifest version must be 1")
    for category in ("songs", "sfx"):
        if not isinstance(m.get(category, []), list) or any(
            not isinstance(i, dict) for i in m.get(category, [])
        ):
            raise FormatError(category + " must be a list of resource objects")
    if "marker_advance_ticks" in m:
        integer(m["marker_advance_ticks"], 0, 65535, "marker_advance_ticks")
    names = set()
    ids = set()
    for category in ("songs", "sfx"):
        local = set()
        for item in m.get(category, []):
            name = item.get("name", "")
            if not re.fullmatch("[A-Za-z_][A-Za-z_0-9]*", name) or name in names:
                raise FormatError("invalid/duplicate resource name")
            n = integer(item.get("id"), 0, 65534, name + " id")
            if n in local:
                raise FormatError("duplicate " + category + " ID")
            local.add(n)
            names.add(name)
            if "marker_advance_ticks" in item:
                if category != "songs":
                    raise FormatError("marker_advance_ticks is only supported on songs")
                integer(item["marker_advance_ticks"], 0, 65535, "marker_advance_ticks")
            for field, prefix, count in [
                ("fm_steal_order", "fm", 5),
                ("psg_steal_order", "tone", 3),
            ]:
                if field in item:
                    order = item[field]
                    if (
                        category != "songs"
                        or not isinstance(order, list)
                        or len(order) != count
                        or any(not isinstance(v, str) for v in order)
                        or set(order) != {f"{prefix}{i}" for i in range(count)}
                    ):
                        raise FormatError(
                            f"{field}: expected each of {prefix}0..{prefix}{count-1} exactly once, on a song"
                        )
            if not isinstance(item.get("source"), str):
                raise FormatError("source path required")
            if "ym_lfo" in item and (
                category != "songs"
                or type(item["ym_lfo"]) != int
                or item["ym_lfo"] not in (0, 8, 9, 10, 11, 12, 13, 14, 15)
            ):
                raise FormatError("ym_lfo requires 0 or 8..15 on a song")
            if "relocatable_tones" in item:
                if category != "sfx" or type(item["relocatable_tones"]) != bool:
                    raise FormatError("relocatable_tones must be boolean on an SFX")
                if (
                    item["relocatable_tones"]
                    and Path(item["source"]).suffix.lower() != ".fur"
                ):
                    raise FormatError("relocatable_tones requires a Furnace SFX")
    return m


def ids_header(m):
    rows = ["#ifndef CALLIOPE_IDS_H", "#define CALLIOPE_IDS_H"]
    for cat in ("songs", "sfx"):
        for item in m.get(cat, []):
            rows.append(
                f'#define CLP_{cat.upper()}_{item["name"].upper()} {item["id"]}'
            )
    for item in m.get("songs", []):
        prefix = item["name"].upper()
        symbols = set()
        for category, names in [
            (
                "PART",
                [
                    (i, v["name"])
                    for i, v in enumerate(item.get("voices", []))
                    if "name" in v
                ],
            ),
            ("GROUP", list(enumerate(item.get("groups", {})))),
            ("ARRANGEMENT", list(enumerate(item.get("arrangements", {})))),
        ]:
            for i, name in names:
                if not isinstance(name, str) or not name:
                    raise FormatError(
                        "part/group/arrangement names must be nonempty strings"
                    )
                suffix = re.sub("[^A-Z0-9_]", "_", name.upper())
                symbol = f"CLP_{category}_{prefix}_{suffix}"
                if symbol in symbols:
                    raise FormatError("names collide in generated ID header")
                symbols.add(symbol)
                value = i if category == "ARRANGEMENT" else 1 << i
                rows.append(f"#define {symbol} {value}")
    return "\n".join(rows + ["#endif", ""])


def used_channels(module, sub):
    s = module.subsongs[sub]
    return [
        ch
        for ch in range(len(module.channels))
        if any(
            module.cell(sub, o, r, ch).active()
            for o in range(len(s.orders[ch]))
            for r in range(s.rows)
        )
    ]


def infer_sfx(module, sub=0):
    active = used_channels(module, sub)
    s = module.subsongs[sub]
    notes = [
        ch
        for ch in active
        if any(
            module.cell(sub, o, r, ch).note is not None
            and module.cell(sub, o, r, ch).note < 180
            for o in range(len(s.orders[ch]))
            for r in range(s.rows)
        )
    ]
    if active != notes:
        raise FormatError("SFX contains playback data on a channel without notes")
    if any(
        module.channels[ch][1] == "fm" and module.channels[ch][2] == 5 for ch in notes
    ):
        raise FormatError("FM6 is reserved for PCM; FM SFX use FM1..FM5")
    if len(notes) == 1:
        inst, kind, ch = module.channels[notes[0]]
        if kind == "noise":
            for (ss, cc, _), p in module.patterns.items():
                if (
                    ss == sub
                    and cc == notes[0]
                    and any(
                        e == 0x20 and v is not None and v & 0x10
                        for c in p.values()
                        for _, e, v in c.effects
                    )
                ):
                    raise FormatError(
                        "tone-derived noise requires tone 3 and noise tracks"
                    )
        return kind, notes
    if len(notes) == 2:
        a, b = [module.channels[ch] for ch in notes]
        if a[0] == b[0] and a[1:] == ("psg", 2) and b[1:] == ("noise", 3):
            return "coupled", notes
    if 2 <= len(notes) <= 4:
        hardware = [module.channels[ch] for ch in notes]
        if len({inst for inst, _, _ in hardware}) == 1 and all(
            kind in ("psg", "noise") for _, kind, _ in hardware
        ):
            return "multi_psg", notes
    if 2 <= len(notes) <= 5:
        hardware = [module.channels[ch] for ch in notes]
        if len({inst for inst, _, _ in hardware}) == 1 and all(
            kind == "fm" and 0 <= slot < 5 for _, kind, slot in hardware
        ):
            return "multi_fm", notes
    raise FormatError("SFX must use channels from one FM or one PSG instance")


def fm_patch(
    ins, volume=127, modern=True, ceil=False, attenuation=0, operator_levels=None
):
    if ins["kind"] != 1:
        raise FormatError("FM voice requires an OPN FM instrument; PCM SFX use WAV")
    features = ins["features"]
    for key, data in features.items():
        if key not in ("NA", "FM", "MA", "LD", "SM", "NE"):
            raise FormatError("unsupported instrument feature " + key)
        if key == "MA":
            sequences(ins, (0, 1, 4), 1)
    if "FM" not in features:
        raise FormatError("missing FM patch")
    r = Reader(features["FM"])
    flags = r.u8()
    algfb = r.u8()
    amsfms = r.u8()
    extra = r.u8()
    block = r.u8()
    if flags & 15 != 4 or block:
        raise FormatError("unsupported FM instrument mode")
    result = bytearray()
    # Furnace stored operator order is the YM register order 1,3,2,4.
    dtmap = (7, 6, 5, 0, 1, 2, 3, 4)
    ops = [r.take(8) for _ in range(4)]
    for row in range(7):
        for op in ops:
            if row == 0:
                v = (dtmap[(op[0] >> 4) & 7] << 4) | (op[0] & 15)
            elif row == 1:
                v = op[1] & 127
            elif row == 2:
                v = op[2] & 0xDF
            elif row == 3:
                v = op[3] & 0x9F
            elif row == 4:
                v = op[4] & 31
            elif row == 5:
                v = op[5]
            else:
                v = op[6] & 15
            result.append(v)
    if operator_levels is not None:
        for i, value in enumerate(operator_levels):
            if value is not None:
                result[4 + i] = value
    carriers = (8, 8, 8, 8, 12, 14, 14, 15)[(algfb >> 4) & 7]
    enabled = flags >> 4
    sensitivity = carriers
    for i, op in enumerate(ops):
        kvs = (op[4] >> 5) & 3
        if kvs == 1 or (kvs == 2 and carriers & (1 << i)):
            sensitivity |= 1 << i
            tl = result[4 + i]
            result[4 + i] = (
                min(127, tl + 127 - volume)
                if modern
                else 127 - (((127 - tl) * volume + (126 if ceil else 0)) // 127)
            )
    # Furnace stores enable flags in register order 1,3,2,4; key-on uses 1,2,3,4.
    # Match genesis.cpp commitState: disabled operators also retain silent TLs.
    for i in range(4):
        if not enabled & (1 << i):
            result[4 + i] = 127
    sensitivity &= enabled
    for i in range(4):
        if sensitivity & (1 << i):
            result[4 + i] = min(127, result[4 + i] + attenuation)
    keymask = (enabled & 9) | ((enabled & 2) << 1) | ((enabled & 4) >> 1)
    result.extend(
        (
            ((algfb >> 4) & 7) | ((algfb & 7) << 3),
            0xC0 | ((amsfms & 0x18) << 1) | (amsfms & 7),
            keymask,
            sensitivity,
        )
    )
    return bytes(result)


class Packer:
    def __init__(self):
        self.data = bytearray(32)
        self.cache = {}

    def add(self, data, alignment=1, bounded=True):
        key = (bytes(data), alignment, bounded)
        if key in self.cache:
            return self.cache[key]
        if bounded and len(data) > 32768:
            raise FormatError("bounded block exceeds 32 KB")
        p = (len(self.data) + alignment - 1) // alignment * alignment
        if bounded and p // 32768 != (p + max(1, len(data)) - 1) // 32768:
            p = (p + 32767) // 32768 * 32768
        self.data.extend(bytes(p - len(self.data)))
        self.data.extend(data)
        self.cache[key] = p
        if len(self.data) >= 4 * 1024 * 1024:
            raise FormatError("bank exceeds linear cartridge range")
        return p


def packed_pcm(data):
    # Bias full-range signed samples once; the single-source driver copies DAC bytes.
    return bytes(value ^ 128 for value in data)


def ptr(n):
    return n.to_bytes(3, "little")


def compile_bank(
    manifest, region="ntsc", *, base_dir=None, pcm_timer=None, progress=None
):
    """Compile a path or an in-memory document without writing source files."""
    if region not in ("ntsc", "pal"):
        raise FormatError("invalid region")
    timer = TIMER_A if pcm_timer is None else pcm_timer
    if timer not in (1016, 1017, 1018, 1019, 1020):
        raise FormatError("invalid PCM timer")
    if isinstance(manifest, dict):
        if base_dir is None:
            raise FormatError("base_dir is required for an in-memory bank")
        path = Path(base_dir).resolve() / "bank.json"
        m = validate_manifest(manifest)
        dependencies = []
    else:
        path = Path(manifest).resolve()
        m = load_manifest(path)
        dependencies = [str(path)]
    pack = Packer()
    patches = []
    songs = []
    effects = []
    sample_cache = {}
    sample_report = []
    sample_details = {}
    sample_references = set()
    debug = []

    def pack_effect(stream, item, module, sub, channels):
        stream, loop = apply_loop(stream, item, module, sub, channels)
        offset = pack.add(stream)
        return {"offset": offset, "loop": 0 if loop is None else offset + loop}

    for category in ("songs", "sfx"):
        if len(m.get(category, [])) > 255:
            raise FormatError("too many resources in bank")
        for index, item in enumerate(m.get(category, [])):
            if progress:
                progress(
                    category,
                    index,
                    len(m.get(category, [])),
                    item.get("name", "unnamed"),
                )
            source = (path.parent / item["source"]).resolve()
            dependencies.append(str(source))
            attenuation = integer(
                item.get("fm_attenuation", 0), 0, 127, "FM attenuation"
            )
            if "fm_attenuation" in item and (
                category != "sfx" or source.suffix.lower() != ".fur"
            ):
                raise FormatError("fm_attenuation requires a Furnace FM SFX")
            if category == "sfx" and source.suffix.lower() == ".wav":
                sample, info = convert(
                    source,
                    region,
                    float(item.get("gain", 1)),
                    item.get("loop"),
                    pcm_timer=timer,
                )
                address = pack.add(packed_pcm(sample), 128, False)
                effects.append(
                    {
                        "id": item["id"],
                        "kind": "pcm",
                        "offset": address,
                        "length": len(sample),
                        "info": info,
                        "priority": integer(
                            item.get("priority", 1), 0, 255, "priority"
                        ),
                    }
                )
                continue
            if source.suffix.lower() != ".fur":
                raise FormatError(
                    "music/FM/PSG inputs must be .fur; PCM SFX must be .wav"
                )
            module = parse(source)
            sub = integer(
                item.get("subsong", 0), 0, len(module.subsongs) - 1, "subsong"
            )
            s = module.subsongs[sub]
            if category == "sfx":
                kind, channels = infer_sfx(module, sub)
                if "fm_attenuation" in item and kind not in ("fm", "multi_fm"):
                    raise FormatError("fm_attenuation requires a Furnace FM SFX")
                relocatable = item.get("relocatable_tones", False)
                if type(relocatable) != bool:
                    raise FormatError("relocatable_tones must be boolean")
                if relocatable and (
                    kind != "multi_psg"
                    or any(module.channels[ch][1] != "psg" for ch in channels)
                ):
                    raise FormatError(
                        "relocatable_tones requires two or three independent tone tracks"
                    )
                if relocatable and "resource_mask" in item:
                    raise FormatError(
                        "relocatable tones use all three eligible tone resources"
                    )
                if (
                    kind in ("psg", "noise", "coupled", "multi_psg")
                    and not full_linear_pitch(module)
                    and not partial_pitch(module)
                ):
                    from .register_psg import compile_register_psg

                    stream, loop = compile_register_psg(
                        module, sub, channels, region, item
                    )
                    if relocatable:
                        stream = relocate_tones(stream, module, channels)
                    offset = pack.add(stream)
                    physical = sum(1 << module.channels[ch][2] for ch in channels)
                    mask = (
                        item.get("resource_mask", 224)
                        if kind == "psg"
                        else physical << 5
                    )
                    if kind == "psg":
                        integer(mask, 1, 255, "PSG resource mask")
                        if mask & ~224:
                            raise FormatError("tone resource mask requires PSG tones")
                    elif item.get("resource_mask", mask) != mask:
                        raise FormatError(
                            "PSG resource mask must match its fixed hardware resources"
                        )
                    effects.append(
                        {
                            "id": item["id"],
                            "kind": "tone_group" if relocatable else kind,
                            "offset": offset,
                            "loop": 0 if loop is None else offset + loop,
                            "priority": integer(
                                item.get("priority", 1), 0, 255, "priority"
                            ),
                            "mask": (
                                len(channels)
                                if relocatable
                                else (
                                    physical
                                    if kind == "multi_psg"
                                    else mask if kind == "psg" else 0
                                )
                            ),
                        }
                    )
                    continue
                if kind == "multi_psg":
                    if item.get("loop_order") is not None:
                        raise FormatError("SFX loops are not yet implemented")
                    mask = sum(1 << (5 + module.channels[ch][2]) for ch in channels)
                    if item.get("resource_mask", mask) != mask:
                        raise FormatError(
                            "multi-channel PSG resource mask must match its fixed hardware resources"
                        )
                    stream = compile_psg(module, sub, channels, region, item["name"])
                    if relocatable:
                        stream = relocate_tones(stream, module, channels)
                    effects.append(
                        {
                            "id": item["id"],
                            "kind": "tone_group" if relocatable else kind,
                            **pack_effect(stream, item, module, sub, channels),
                            "priority": integer(
                                item.get("priority", 1), 0, 255, "priority"
                            ),
                            "mask": len(channels) if relocatable else mask >> 5,
                        }
                    )
                    continue
                if kind == "psg":
                    if item.get("loop_order") is not None:
                        raise FormatError("SFX loops are not yet implemented")
                    mask = integer(
                        item.get("resource_mask", 224), 1, 255, "PSG resource mask"
                    )
                    if mask & ~224:
                        raise FormatError(
                            "PSG resource mask must contain only tone bits 5..7"
                        )
                    effects.append(
                        {
                            "id": item["id"],
                            "kind": "psg",
                            **pack_effect(
                                compile_tone(
                                    module, sub, channels[0], region, item["name"]
                                ),
                                item,
                                module,
                                sub,
                                channels,
                            ),
                            "priority": integer(
                                item.get("priority", 1), 0, 255, "priority"
                            ),
                            "mask": mask,
                        }
                    )
                    continue
                if kind in ("noise", "coupled"):
                    if item.get("loop_order") is not None:
                        raise FormatError("SFX loops are not yet implemented")
                    mask = 256 if kind == "noise" else 384
                    if item.get("resource_mask", mask) != mask:
                        raise FormatError(
                            "noise resource mask must match its fixed hardware resources"
                        )
                    effects.append(
                        {
                            "id": item["id"],
                            "kind": kind,
                            **pack_effect(
                                compile_noise(
                                    module, sub, channels, region, item["name"]
                                ),
                                item,
                                module,
                                sub,
                                channels,
                            ),
                            "priority": integer(
                                item.get("priority", 1), 0, 255, "priority"
                            ),
                            "mask": 0,
                        }
                    )
                    continue

                item = dict(
                    item,
                    voices=[
                        {
                            "channel": ch,
                            "slot": (
                                f"fm{module.channels[ch][2]}"
                                if kind == "multi_fm"
                                else "fm0"
                            ),
                        }
                        for ch in channels
                    ],
                )
                if item.get("loop_order") is not None:
                    raise FormatError("SFX loops are not yet implemented")
            if category == "songs" and "tick_rate" in item:
                rate5(item["tick_rate"])
                s.hz = item["tick_rate"]
            if category == "sfx" and s.hz != 60:
                raise FormatError("FM SFX requires a 60 Hz tick clock")
            timing = Timing(module, sub)
            loop_timing = None
            if abs(module.tuning - 440) > 0.001:
                raise FormatError("runtime requires A4=440 Hz")
            voices = item.get("voices", [])
            if not 1 <= len(voices) <= 16:
                raise FormatError("expected 1..16 logical voices")
            mapping = {}
            slots = set()
            for voice in voices:
                select = voice["channel"]
                if isinstance(select, str):
                    matches = [i for i, name in enumerate(s.names) if name == select]
                    if len(matches) != 1:
                        raise FormatError(
                            "channel name is missing or ambiguous: " + select
                        )
                    ch = matches[0]
                else:
                    ch = integer(select, 0, len(module.channels) - 1, "channel")
                slot = SLOTS.get(voice["slot"], -1)
                kind = module.channels[ch][1]
                if not (
                    (0 <= slot <= 4 and kind == "fm")
                    or (5 <= slot <= 7 and kind == "psg")
                    or (slot == 8 and kind == "noise")
                    or (slot == 9 and kind == "fm" and module.channels[ch][2] == 5)
                ):
                    raise FormatError("unsupported source/slot mapping")
                if ch in mapping:
                    raise FormatError("duplicate source channel")
                mapping[ch] = slot
                slots.add(slot)
            if item.get("external_commands") not in (None, "passthrough"):
                raise FormatError(
                    "external_commands must be passthrough when specified"
                )
            part_data, arrangements, initial_arrangement = parts_layout(
                item, list(mapping.values())
            )
            logical = bool(part_data)
            if category == "sfx" and logical:
                raise FormatError("SFX cannot define parts/groups/arrangements")
            if category == "songs" and not logical:
                mask = (1 << len(voices)) - 1
                part_data = b"".join(
                    bytes((slot, 0, 0, 1)) for slot in mapping.values()
                ) + mask.to_bytes(2, "little")
                arrangements = [mask]
                initial_arrangement = 0
                logical = True
            part_ids = {ch: i for i, ch in enumerate(mapping)}
            markers = item.get("markers", [])
            marker_rows = {}
            for mark in markers:
                order = integer(
                    mark.get("order"), 0, len(s.orders[0]) - 1, "marker order"
                )
                row = integer(mark.get("row"), 0, s.rows - 1, "marker row")
                if (order, row) in marker_rows:
                    raise FormatError("duplicate marker boundary")
                marker_rows[order, row] = integer(mark.get("id"), 0, 65535, "marker ID")
            if markers and not logical:
                raise FormatError("markers require a logical part layout")
            if set(used_channels(module, sub)) - set(mapping):
                raise FormatError("unmapped active source channel")
            if module.flags.get("timebase", 0):
                raise FormatError("unsupported timebase")
            orders = []
            streams = []
            stopped = False
            initialized = set()
            eventmap = item.get("events", {})
            envelopes = {
                ch: VolumeEnvelope(
                    slot != 8,
                    full_linear_pitch(module),
                    slide_options(module),
                    arp_options(module),
                    partial_pitch(module),
                    continuous_vibrato(module),
                )
                for ch, slot in mapping.items()
                if slot in (5, 6, 7, 8)
            }
            for envelope in envelopes.values():
                envelope.porta_options = pitch_options(module)
            pitches = {
                ch: FMPitch(
                    full_linear_pitch(module),
                    region,
                    module.tuning,
                    continuous_vibrato(module),
                    pitch_options(module),
                    arp_options(module),
                )
                for ch, slot in mapping.items()
                if slot <= 4
            }
            pcm_gain = item.get("pcm_gain", 1.0)
            if (
                type(pcm_gain) not in (int, float)
                or not math.isfinite(pcm_gain)
                or pcm_gain < 0
            ):
                raise FormatError("pcm_gain must be a finite nonnegative number")
            sample_voices = {
                ch: SampleVoice(module) for ch, slot in mapping.items() if slot == 9
            }
            loop_pcm = {}
            arp_clock = max(1, s.arp_speed)
            loop_arp_clock = arp_clock
            loop_arp = {}
            trackers = {ch: p.tracker_arp for ch, p in {**pitches, **envelopes}.items()}
            loop_psg_vibrato = {}
            loop_slides = {}
            loop_fine = {}
            loop_fm_fine = {}
            loop_fm_vibrato = {}
            loop_instruments = {}
            loop_volumes = {}
            loop_noise = {}
            fm_effects = {ch: set() for ch in pitches}
            noise_modes = {ch: 0x11 for ch, slot in mapping.items() if slot == 8}
            paired_noise = False
            instruments = {}
            lastpatch = {}
            volumes = {
                ch: (15 if slot in (5, 6, 7, 8) else 127)
                for ch, slot in mapping.items()
            }
            modern, ceil = volume_options(module)
            levels = {
                ch: FMLevel(modern, ceil, slide_options(module), attenuation)
                for ch in pitches
            }
            pans = {ch: 0xC0 for ch in pitches}
            pan_channels = {
                ch
                for ch in pitches
                if any(
                    e in PAN_EFFECTS
                    for o in range(len(s.orders[ch]))
                    for r in range(s.rows)
                    for _, e, _ in module.cell(sub, o, r, ch).effects
                )
            }
            flow = resolve_flow(module, sub, mapping, item.get("loop_order"))
            if flow.loop is not None and any(
                e == 3
                for ch in envelopes
                if mapping[ch] in (5, 6, 7)
                for o in range(len(s.orders[ch]))
                for r in range(s.rows)
                for _, e, _ in module.cell(sub, o, r, ch).effects
            ):
                raise FormatError(
                    "PSG portamento loops require loop-state validation and are not yet supported"
                )
            marker_advance = (
                item.get("marker_advance_ticks", m.get("marker_advance_ticks", 0))
                if category == "songs"
                else 0
            )
            terminals = []
            loop_coordinate = None if flow.loop is None else flow.bundles[flow.loop][0]
            for bundle_index, bundle in enumerate(flow.bundles):
                stream = CommandStream(
                    bytes((0x39, item.get("ym_lfo", 0)))
                    if category == "songs"
                    and bundle_index == 0
                    and any("ym_lfo" in song for song in m.get("songs", []))
                    else b""
                )
                for order, row in bundle:
                    row_ticks, conductor = timing.row(order, row, mapping)
                    if (order, row) == loop_coordinate:
                        loop_timing = timing.pre
                        loop_fine = {
                            ch: envelope.fine for ch, envelope in envelopes.items()
                        }
                        loop_fm_fine = {ch: pitch.fine for ch, pitch in pitches.items()}
                        loop_slides = {
                            ch: pitch.slide_speed for ch, pitch in pitches.items()
                        }
                        loop_psg_vibrato = {
                            ch: (v.vibrato_depth, v.vibrato_rate, v.vibrato_phase)
                            for ch, v in envelopes.items()
                        }
                        loop_fm_vibrato = {
                            ch: (
                                pitch.vibrato_depth,
                                pitch.vibrato_rate,
                                pitch.vibrato_phase,
                            )
                            for ch, pitch in pitches.items()
                        }
                        loop_arp = {ch: arp.state() for ch, arp in trackers.items()}
                        loop_arp_clock = arp_clock
                        loop_pcm = {
                            ch: (voice.mode, voice.bank)
                            for ch, voice in sample_voices.items()
                        }
                        loop_instruments = dict(instruments)
                        loop_volumes = dict(volumes)
                        loop_noise = dict(noise_modes)
                        loop_pans = dict(pans)
                        loop_operator_levels = {
                            ch: tuple(level.operator_levels)
                            for ch, level in levels.items()
                        }
                        # An explicit loop-entry patch must also be emitted on the first pass.
                        lastpatch.clear()
                    arp_clock = arp_speed(
                        arp_clock,
                        (module.cell(sub, order, row, ch) for ch in sorted(mapping)),
                    )
                    for tracker in trackers.values():
                        tracker.period = arp_clock
                    payload = bytearray(conductor)
                    stop = False
                    automation = {}
                    if logical:
                        absolute_row = order * s.rows + row
                        bar = s.highlights[1]
                        flags = 8 if bar and absolute_row % bar == 0 else 0
                        marker = marker_rows.get((order, row))
                        if flags or marker is not None:
                            payload.extend(
                                (0x15, flags | (16 if marker is not None else 0))
                            )
                            payload.extend((marker or 0).to_bytes(2, "little"))
                    for ch, slot in sorted(mapping.items()):
                        row_automation = automation
                        automation = {}
                        channel_at = len(payload)
                        cell = module.cell(sub, order, row, ch)
                        context = (
                            f'{item["name"]}: order {order}, row {row}, channel {ch}'
                        )
                        try:
                            delay = (
                                fm_delay(cell, row_ticks, delay_policy(module))
                                if slot <= 4
                                else 0
                            )
                        except FormatError as e:
                            raise FormatError(context + ": " + str(e)) from e
                        boundary_release = None
                        if delay == row_ticks:
                            if cell.note not in (180, 181, 182) or any(
                                e != 0xED for _, e, _ in cell.effects
                            ):
                                raise FormatError(
                                    context
                                    + ": FM delay at the row boundary requires a cut/release with no other effects"
                                )
                            boundary_release = cell
                            cell = Cell()
                            delay = 0
                        if delay and any(
                            e in TIMING_EFFECTS or e in (0x0B, 0x0D, 0xE0, 0xFF)
                            for _, e, _ in cell.effects
                        ):
                            raise FormatError(
                                context
                                + ": FM note delay cannot combine with global timing, flow or stop effects"
                            )
                        pcm_delay = 0
                        for _, effect, value in cell.effects:
                            if effect == 0xED:
                                if slot <= 4:
                                    continue
                                if slot != 9:
                                    raise FormatError(
                                        context
                                        + ": ED note delay requires an FM or PCM voice"
                                    )
                                pcm_delay = value or 0
                                if pcm_delay >= row_ticks:
                                    raise FormatError(
                                        "PCM note delay must stay within its row"
                                    )
                                if any(
                                    e not in (0xED, 0x17, 0xEB)
                                    for _, e, _ in cell.effects
                                ):
                                    raise FormatError(
                                        "PCM note delay cannot combine with other row effects"
                                    )
                        if logical and (
                            cell.note is not None
                            or cell.instrument is not None
                            or cell.volume is not None
                            or cell.effects
                        ):
                            payload.extend((0x14, part_ids[ch]))
                        try:
                            cut = {
                                tick + delay: kind
                                for tick, kind in cuts(
                                    cell, row_ticks, delay_policy(module)
                                ).items()
                            }
                            if delay and any(tick >= row_ticks for tick in cut):
                                raise FormatError(
                                    "delayed FM cuts/releases must stay within their row"
                                )
                        except FormatError as e:
                            raise FormatError(context + ": " + str(e)) from e
                        if delay:
                            # The old voice keeps running until the delayed row is read.
                            old_ins = (
                                module.instruments[instruments[ch]]
                                if ch in instruments
                                else None
                            )
                            pitch = pitches[ch]
                            level = levels[ch]
                            pitch.begin(Cell(), old_ins)
                            level.begin(Cell(), old_ins, False)
                            for tick in range(delay):
                                prefix = bytes((0x14, part_ids[ch])) if logical else b""
                                value = pitch.tick()
                                if value is not None:
                                    automation.setdefault(tick, bytearray()).extend(
                                        prefix
                                        + bytes((0x36, slot))
                                        + value.to_bytes(2, "little")
                                    )
                                value = level.tick()
                                if value is not None:
                                    automation.setdefault(tick, bytearray()).extend(
                                        prefix + bytes((0x37, slot)) + value
                                    )
                        if slot <= 4 or slot == 9:
                            for tick in cut:
                                prefix = bytes((0x14, part_ids[ch])) if logical else b""
                                automation.setdefault(tick, bytearray()).extend(
                                    prefix
                                    + (
                                        bytes((OP_OFF, slot))
                                        if slot <= 4
                                        else bytes((0x32,))
                                    )
                                )
                        if cell.instrument is not None:
                            if cell.instrument >= len(module.instruments):
                                raise FormatError(context + ": invalid instrument")
                            instruments[ch] = cell.instrument
                        if cell.volume is not None:
                            if ch in initialized and slot == 9 and cell.note is None:
                                raise FormatError(
                                    context
                                    + ": held-note volume changes not yet supported"
                                )
                            volumes[ch] = integer(
                                cell.volume,
                                0,
                                15 if slot in (5, 6, 7, 8) else 127,
                                "volume",
                            )
                        ins = (
                            module.instruments[instruments[ch]]
                            if ch in instruments
                            else None
                        )
                        if slot <= 4:
                            try:
                                pan = row_pan(cell)
                            except FormatError as e:
                                raise FormatError(context + ": " + str(e)) from e
                            if pan is not None:
                                pans[ch] = pan
                            patch_written = False
                            pitch = pitches[ch]
                            fm_effects[ch].update(e for _, e, _ in cell.effects)
                            if cell.note is not None and cell.note < 0:
                                raise FormatError(
                                    context + ": note outside Furnace range"
                                )
                            try:
                                pitch.begin(cell, ins)
                            except FormatError as e:
                                raise FormatError(context + ": " + str(e)) from e
                            if (
                                cell.instrument is not None
                                and cell.note is None
                                and ch in initialized
                            ):
                                raise FormatError(
                                    context
                                    + ": instrument-only FM changes not yet supported"
                                )
                            if cell.instrument is not None:
                                patch = fm_patch(ins, 127, modern, ceil, attenuation)
                                if patch not in patches:
                                    patches.append(patch)
                                if len(patches) > 255:
                                    raise FormatError("bank exceeds 255 FM patches")
                                if lastpatch.get(ch) != patches.index(patch) or (
                                    category == "sfx"
                                    and item.get("loop", {}).get("start")
                                    == [order, row]
                                ):
                                    if (
                                        ch in initialized
                                        and cell.note is not None
                                        and cell.note < 180
                                        and not pitch.fresh
                                    ):
                                        raise FormatError(
                                            context
                                            + ": patch changes during legato/portamento are not yet supported"
                                        )
                                    levels[ch].last = None
                                    payload.extend(
                                        (OP_PATCH, slot, patches.index(patch))
                                    )
                                    patch_written = True
                                    lastpatch[ch] = patches.index(patch)
                                initialized.add(ch)
                            if pan is not None or (
                                patch_written and ch in pan_channels
                            ):
                                sensitivity = (
                                    fm_patch(ins, 127, modern, ceil)[29] & 0x3F
                                    if ins is not None
                                    else 0
                                )
                                payload.extend((0x16, slot, pans[ch] | sensitivity))
                            note_at = len(payload)
                            try:
                                levels[ch].begin(cell, ins, pitch.fresh)
                            except FormatError as e:
                                raise FormatError(context + ": " + str(e)) from e
                            if delay:
                                pitch.row_tick = delay
                                levels[ch].slide.first = False
                            if cell.note is not None:
                                if cell.note < 180 and ch not in initialized:
                                    raise FormatError(
                                        context + ": first note needs an instrument"
                                    )
                                if cell.note in (180, 181):
                                    payload.extend((OP_OFF, slot))
                                elif cell.note < 180 and pitch.fresh:
                                    payload.extend((OP_NOTE, slot))
                                    payload.extend(
                                        fm_frequency(
                                            cell.note, region, module.tuning
                                        ).to_bytes(2, "little")
                                    )
                        elif slot == 8:
                            if ins is not None and (
                                ins["kind"] != 0 or set(ins["features"]) - {"NA", "MA"}
                            ):
                                raise FormatError(
                                    context + ": music noise requires a PSG instrument"
                                )
                            force = False
                            for _, effect, value in cell.effects:
                                if effect == 0x20:
                                    if (
                                        value not in (0, 1, 0x10, 0x11)
                                        or cell.note is None
                                    ):
                                        raise FormatError(
                                            context
                                            + ": music noise mode requires a note and 2000/2001/2010/2011"
                                        )
                                    noise_modes[ch] = value
                                    force = True
                            if cell.note is not None:
                                if cell.note >= 180:
                                    pass
                                else:
                                    mode = noise_modes[ch]
                                    derived = bool(mode & 0x10)
                                    if derived:
                                        if 7 in slots:
                                            raise FormatError(
                                                context
                                                + ": tone-derived music noise reserves tone2 for the entire song"
                                            )
                                        paired_noise = True
                                    elif cell.note % 12 > 2:
                                        raise FormatError(
                                            context
                                            + ": fixed noise notes must be C, C# or D"
                                        )
                                    control = (
                                        0xE0
                                        | ((mode & 1) << 2)
                                        | (3 if derived else 2 - cell.note % 12)
                                    )
                                    payload.extend(
                                        (
                                            0x33,
                                            control | (8 if force else 0),
                                            0xF0 | (15 - volumes[ch]),
                                        )
                                    )
                                    if derived:
                                        clock = (
                                            53693175 if region == "ntsc" else 53203424
                                        ) / 15
                                        period = tone_period(
                                            cell.note, module, region, noise=True
                                        )
                                        payload.extend(
                                            (
                                                0x30,
                                                3,
                                                0xC0 | (period & 15),
                                                (period >> 4) & 63,
                                                0xDF,
                                            )
                                        )

                        elif slot in (5, 6, 7):
                            if ins is not None and (
                                ins["kind"] != 0 or set(ins["features"]) - {"NA", "MA"}
                            ):
                                raise FormatError(
                                    context + ": PSG requires a tone instrument"
                                )
                            latch = 0x80 + ((slot - 5) << 5)

                            if cell.note is not None:
                                if cell.note >= 180:
                                    pass
                                else:
                                    clock = (
                                        53693175 if region == "ntsc" else 53203424
                                    ) / 15
                                    # Furnace SMS uses its historical PSG octave base:
                                    # note 108 (C-4) is about 1046.5 Hz, not MIDI C4.
                                    # See pitchTable.cpp and the independent WAV oracle test.
                                    hz = module.tuning * 2 ** ((cell.note - 93) / 12)
                                    period = round(clock / (32 * hz))
                                    # Furnace SMS backend clamps long periods; it does not reject low notes.
                                    period = min(1023, max(0, period))
                                    if (
                                        module.flags.get("compat_extended", [0] * 28)[
                                            21
                                        ]
                                        and period < 8
                                    ):
                                        period = 1
                                    if not (
                                        envelopes[ch].active
                                        and (
                                            envelopes[ch].porta_speed
                                            or any(
                                                e == 3 and v for _, e, v in cell.effects
                                            )
                                        )
                                    ):
                                        payload.extend(
                                            (
                                                0x30,
                                                2,
                                                latch | (period & 15),
                                                (period >> 4) & 63,
                                            )
                                        )
                        elif slot == 9:
                            pcm_at = len(payload)
                            voice = sample_voices[ch]
                            try:
                                disabled = voice.begin(cell, ins)
                            except FormatError as e:
                                raise FormatError(context + ": " + str(e)) from e
                            if disabled or cell.note in (180, 181):
                                payload.append(0x32)
                            if cell.note is not None and cell.note < 180:
                                initialized.add(ch)
                                try:
                                    index, rate = voice.select(cell.note, ins)
                                except FormatError as e:
                                    raise FormatError(context + ": " + str(e)) from e
                                sample = module.samples[index]
                                if (
                                    sample["depth"] not in (8, 16)
                                    or sample["direction"] != 0
                                ):
                                    raise FormatError(
                                        context + ": only forward integer PCM supported"
                                    )
                                gain = pcm_gain * volumes[ch] / 127
                                key = (str(source), index, rate, gain, region)
                                if key not in sample_cache:
                                    width = sample["depth"] // 8
                                    data = sample["data"]
                                    values = [
                                        int.from_bytes(
                                            data[i : i + width], "little", signed=True
                                        )
                                        / (1 << (sample["depth"] - 1))
                                        for i in range(0, len(data), width)
                                    ]
                                    try:
                                        encoded, info = resample(
                                            values,
                                            rate,
                                            {},
                                            region,
                                            gain,
                                            sample["loop"],
                                            pcm_timer=timer,
                                        )
                                    except FormatError as e:
                                        raise FormatError(
                                            context
                                            + ": "
                                            + str(e)
                                            + " (configure pcm_gain)"
                                        ) from e
                                    address = pack.add(packed_pcm(encoded), 128, False)
                                    loop = info["loop"] or [0, 0]
                                    descriptor = (
                                        bytes((0, 0, 1, 0))
                                        + ptr(address)
                                        + ptr(len(encoded))
                                        + ptr(loop[0])
                                        + ptr(loop[1])
                                    )
                                    sample_cache[key] = pack.add(descriptor)
                                    sample_details[key] = {
                                        "source": str(source),
                                        "sample": index,
                                        "note": cell.note,
                                        "source_rate": rate,
                                        "gain": gain,
                                        "clipped_samples": info["clipped_samples"],
                                        "legacy": voice.mode == 1,
                                        "descriptor": sample_cache[key],
                                        "offset": address,
                                        "bytes": len(encoded),
                                        "rate": info["rate"],
                                        "loop": info["loop"],
                                    }
                                reference = (category, item["id"], key)
                                if reference not in sample_references:
                                    sample_references.add(reference)
                                    sample_report.append(dict(
                                        sample_details[key],
                                        category=category,
                                        resource_id=item["id"],
                                        resource_name=item.get("name", ""),
                                        sample_name=sample.get("name", ""),
                                    ))
                                payload.append(0x31)
                                payload.extend(ptr(sample_cache[key]))
                            if pcm_delay:
                                prefix = bytes((0x14, part_ids[ch])) if logical else b""
                                automation.setdefault(pcm_delay, bytearray()).extend(
                                    prefix + payload[pcm_at:]
                                )
                                del payload[pcm_at:]
                        if ch in pitches:
                            pitch = pitches[ch]
                            for tick in range(delay, row_ticks):
                                value = pitch.tick(cut.get(tick))
                                if value is None:
                                    continue
                                if tick == delay and pitch.fresh:
                                    payload[-2:] = value.to_bytes(2, "little")
                                else:
                                    operation = (
                                        bytes((0x14, part_ids[ch])) if logical else b""
                                    )
                                    automation.setdefault(tick, bytearray()).extend(
                                        operation
                                        + bytes((0x36, slot))
                                        + value.to_bytes(2, "little")
                                    )
                            for tick in range(delay, row_ticks):
                                level = levels[ch].tick(cut.get(tick))
                                if level is None:
                                    continue
                                operation = bytes((0x37, slot)) + level
                                if tick == delay and pitch.fresh:
                                    payload[note_at:note_at] = operation
                                else:
                                    prefix = (
                                        bytes((0x14, part_ids[ch])) if logical else b""
                                    )
                                    automation.setdefault(tick, bytearray()).extend(
                                        prefix + operation
                                    )
                        if ch in envelopes:
                            envelope = envelopes[ch]
                            try:
                                envelope.begin(cell, ins)
                            except FormatError as e:
                                raise FormatError(context + ": " + str(e)) from e
                            for tick in range(row_ticks):
                                level = envelope.tick(cut.get(tick))
                                if slot in (5, 6, 7) and envelope.pitch_changed:
                                    clock = (
                                        53693175 if region == "ntsc" else 53203424
                                    ) / 15
                                    period = tone_period(
                                        envelope.pitch_note,
                                        module,
                                        region,
                                        envelope.effective_fine,
                                    )
                                    values = bytes(
                                        (
                                            0x80 + ((slot - 5) << 5) | (period & 15),
                                            (period >> 4) & 63,
                                        )
                                    )
                                    if (
                                        tick == 0
                                        and len(payload) >= 4
                                        and payload[-4:-2] == bytes((0x30, 2))
                                    ):
                                        payload[-2:] = values
                                    else:
                                        operation = (
                                            bytes((0x14, part_ids[ch]))
                                            if logical
                                            else b""
                                        )
                                        automation.setdefault(tick, bytearray()).extend(
                                            operation + bytes((0x35, 2)) + values
                                        )
                                if level is not None:
                                    value = 0x90 + ((slot - 5) << 5) | level
                                    if (
                                        tick == 0
                                        and len(payload) >= 4
                                        and payload[-4:-2] == bytes((0x30, 2))
                                    ):
                                        payload[-3] = 3
                                        payload.append(value)
                                        continue
                                    if (
                                        tick == 0
                                        and len(payload) >= 3
                                        and payload[-3] == 0x33
                                    ):
                                        payload[-1] = value
                                        continue
                                    operation = (
                                        bytes((0x14, part_ids[ch])) if logical else b""
                                    )
                                    operation += bytes(
                                        (0x30, 1, 0x90 + ((slot - 5) << 5) | level)
                                    )
                                    automation.setdefault(tick, bytearray()).extend(
                                        operation
                                    )
                        for col, effect, value in cell.effects:
                            if effect in PAN_EFFECTS:
                                if slot == 9:
                                    raise FormatError(
                                        context
                                        + f": {effect:02X}xx panning is unsupported on the shared mono PCM mixer"
                                    )
                                row_pan(
                                    cell
                                )  # Validate ignored panning on the mono PSG too.
                                continue
                            if effect in TIMING_EFFECTS:
                                if category == "sfx":
                                    raise FormatError(
                                        context + ": SFX tempo changes are forbidden"
                                    )
                                continue
                            if effect in (0x0B, 0x0D) and category == "songs":
                                continue
                            if effect == 0x20 and slot == 8:
                                continue
                            if effect in (0xEC, 0xFC):
                                continue
                            if effect == 0xE0:
                                continue
                            if effect == 0 and slot < 8:
                                continue
                            if effect in (0x17, 0xEB, 0xED) and slot == 9:
                                continue
                            if effect == 0x0A and slot < 9:
                                continue
                            if effect in (1, 2, 3, 4, 0xEA) and slot <= 4:
                                continue
                            if effect == 0xED and slot <= 4:
                                continue
                            if effect in (0x12, 0x13, 0x14, 0x15) and slot <= 4:
                                continue
                            if effect in (3, 4) and slot in (5, 6, 7):
                                continue
                            if effect == 0xE5 and slot in (0, 1, 2, 3, 4, 5, 6, 7):
                                continue
                            if effect == 0xFF:
                                stop = True
                            elif effect == 0xEE:
                                if category == "sfx":
                                    raise FormatError(
                                        context
                                        + ": external commands are forbidden in SFX"
                                    )
                                value = 0 if value is None else value
                                event = eventmap.get(str(value))
                                if (
                                    event is None
                                    and item.get("external_commands") == "passthrough"
                                ):
                                    event = {"id": value, "parameter": ch}
                                if event is None:
                                    raise FormatError(
                                        context + ": unmapped external event"
                                    )
                                eid = integer(event["id"], 0, 65535, "event ID")
                                param = integer(
                                    event.get("parameter", 0),
                                    0,
                                    65535,
                                    "event parameter",
                                )
                                payload.append(OP_EVENT)
                                payload.extend(struct.pack("<HH", eid, param))
                            else:
                                raise FormatError(
                                    context + f": effect {effect:02X} not yet supported"
                                )
                        if delay:
                            automation[delay] = payload[channel_at:] + automation.get(
                                delay, bytearray()
                            )
                            del payload[channel_at:]
                        if boundary_release is not None:
                            prefix = bytes((0x14, part_ids[ch])) if logical else b""
                            if boundary_release.instrument is not None:
                                index = boundary_release.instrument
                                if not 0 <= index < len(module.instruments):
                                    raise FormatError(context + ": invalid instrument")
                                instruments[ch] = index
                                ins = module.instruments[index]
                                patch = fm_patch(ins, 127, modern, ceil, attenuation)
                                if patch not in patches:
                                    patches.append(patch)
                                if len(patches) > 255:
                                    raise FormatError("bank exceeds 255 FM patches")
                                if lastpatch.get(ch) != patches.index(patch):
                                    automation.setdefault(
                                        row_ticks, bytearray()
                                    ).extend(
                                        prefix
                                        + bytes((OP_PATCH, slot, patches.index(patch)))
                                    )
                                    if ch in pan_channels:
                                        sensitivity = patch[29] & 0x3F
                                        automation[row_ticks].extend(
                                            prefix
                                            + bytes(
                                                (0x16, slot, pans[ch] | sensitivity)
                                            )
                                        )
                                    lastpatch[ch] = patches.index(patch)
                                    levels[ch].last = None
                                initialized.add(ch)
                            else:
                                ins = (
                                    module.instruments[instruments[ch]]
                                    if ch in instruments
                                    else None
                                )
                            levels[ch].begin(boundary_release, ins, False)
                            if boundary_release.volume is not None:
                                volumes[ch] = integer(
                                    boundary_release.volume, 0, 127, "volume"
                                )
                            value = levels[ch].render()
                            if value is not None:
                                automation.setdefault(row_ticks, bytearray()).extend(
                                    prefix + bytes((0x37, slot)) + value
                                )
                            if boundary_release.note in (180, 181):
                                automation.setdefault(row_ticks, bytearray()).extend(
                                    prefix + bytes((OP_OFF, slot))
                                )
                                pitches[ch].release(boundary_release.note)
                            else:
                                pitches[ch].released = True
                        for tick, operations in automation.items():
                            row_automation.setdefault(tick, bytearray()).extend(
                                operations
                            )
                        automation = row_automation
                    if len(payload) > 192:
                        raise FormatError("row exceeds 192 bytes")
                    payload.extend(automation.pop(0, b""))
                    if category == "songs" and 9 in mapping.values():
                        payload = bytearray(align_tick(payload))
                    debug.append(
                        {
                            "song": item["id"],
                            "order": order,
                            "row": row,
                            "bytes": len(payload),
                        }
                    )
                    if len(payload) > 192:
                        raise FormatError("row tick exceeds 192 bytes")
                    stream.extend(payload)
                    previous = 0
                    for tick, operations in sorted(automation.items()):
                        if category == "songs" and 9 in mapping.values():
                            operations = align_tick(operations)
                        if len(operations) > 192:
                            raise FormatError("automation tick exceeds 192 bytes")
                        stream.wait(tick - previous)
                        stream.extend(operations)
                        previous = tick
                    stream.wait(row_ticks - previous)
                    if stop:
                        stopped = True
                        break
                compiled = stream.finish()
                streams.append(compiled)
                terminal = OP_END if stopped else OP_PATTERN_END
                terminals.append(terminal)
                if category == "songs" and not marker_advance:
                    addresses = [
                        pack.add(pattern, alignment=16)
                        for pattern in split_channels(
                            compiled, len(voices), terminal, 9 in mapping.values(),
                            max((i for i, slot in enumerate(mapping.values()) if slot != 9), default=0)
                        )
                    ]
                    orders.append(
                        pack.add(b"".join(ptr(address) for address in addresses))
                    )
                elif category == "sfx":
                    orders.append(pack.add(compiled + bytes((terminal,))))
                if stopped:
                    break
            if marker_advance:
                streams = advance_markers(streams, marker_advance, flow.loop)
                for compiled, terminal in zip(streams, terminals):
                    addresses = [
                        pack.add(pattern, alignment=16)
                        for pattern in split_channels(
                            compiled, len(voices), terminal, 9 in mapping.values(),
                            max((i for i, slot in enumerate(mapping.values()) if slot != 9), default=0)
                        )
                    ]
                    orders.append(
                        pack.add(b"".join(ptr(address) for address in addresses))
                    )
            if category == "sfx":
                effect_kind = "multi_fm" if len(channels) > 1 else "fm"
                mask = (
                    sum(1 << slot for slot in mapping.values())
                    if effect_kind == "multi_fm"
                    else integer(
                        item.get("resource_mask", 31), 1, 31, "FM resource mask"
                    )
                )
                if (
                    effect_kind == "multi_fm"
                    and item.get("resource_mask", mask) != mask
                ):
                    raise FormatError(
                        "multi-channel FM resource mask must match its fixed hardware resources"
                    )
                effects.append(
                    {
                        "id": item["id"],
                        "kind": effect_kind,
                        **pack_effect(
                            b"".join(streams) + bytes((OP_END,)),
                            item,
                            module,
                            sub,
                            channels,
                        ),
                        "priority": integer(
                            item.get("priority", 1), 0, 255, "priority"
                        ),
                        "mask": mask,
                    }
                )
                continue
            table = pack.add(b"".join(ptr(p) for p in orders))
            loop = flow.loop
            if loop is not None:
                for ch in mapping:
                    validate_volume_loop(
                        module.cell(sub, *loop_coordinate, ch),
                        {
                            e
                            for segment in flow.bundles
                            for o, r in segment
                            for _, e, _ in module.cell(sub, o, r, ch).effects
                        },
                    )
                    needs = {
                        "instrument": instruments.get(ch) != loop_instruments.get(ch),
                        "volume": volumes[ch] != loop_volumes[ch],
                        "noise mode": noise_modes.get(ch) != loop_noise.get(ch),
                    }
                    for segment in flow.bundles[loop:]:
                        for coordinate in segment:
                            entry = module.cell(sub, *coordinate, ch)
                            if entry.instrument is not None:
                                needs["instrument"] = False
                            if entry.volume is not None:
                                needs["volume"] = False
                            if any(e == 0x20 for _, e, _ in entry.effects):
                                needs["noise mode"] = False
                            if entry.note is not None and entry.note < 180:
                                for state, changed in needs.items():
                                    if changed:
                                        raise FormatError(
                                            f"channel {ch}: loop must reset changed {state} before its first note"
                                        )
                                break
                        else:
                            continue
                        break
                resets_arp_speed = any(
                    e == 0xE0 and v
                    for ch in mapping
                    for _, e, v in module.cell(sub, *loop_coordinate, ch).effects
                )
                if arp_clock != loop_arp_clock and not resets_arp_speed:
                    raise FormatError("loop entry must reset changed arpeggio speed")
                for ch, tracker in trackers.items():
                    used = any(
                        e == 0 and v
                        for segment in flow.bundles
                        for o, r in segment
                        for _, e, v in module.cell(sub, o, r, ch).effects
                    )
                    tracker.validate_loop(
                        loop_arp[ch], module.cell(sub, *loop_coordinate, ch), used
                    )
                for ch, voice in sample_voices.items():
                    voice.validate_loop(
                        loop_pcm[ch],
                        (
                            module.cell(sub, o, r, ch)
                            for segment in flow.bundles[loop:]
                            for o, r in segment
                        ),
                    )
                for ch, pitch in pitches.items():
                    entry = module.cell(sub, *loop_coordinate, ch)
                    for op, (before, after) in enumerate(
                        zip(loop_operator_levels[ch], levels[ch].operator_levels)
                    ):
                        effect = (0x12, 0x14, 0x13, 0x15)[op]
                        resets = any(e == effect for _, e, _ in entry.effects)
                        changes_instrument = (
                            entry.instrument is not None
                            and entry.instrument != instruments.get(ch)
                        )
                        if before != after and not resets and not changes_instrument:
                            raise FormatError(
                                f"channel {ch}: loop entry must reset changed FM operator level ({effect:02X}xx)"
                            )
                    if (
                        pans[ch] != loop_pans[ch]
                        and row_pan(entry) is None
                        and entry.instrument is None
                    ):
                        raise FormatError(
                            f"channel {ch}: loop entry must reset changed FM panning"
                        )
                    from .fm import validate_loop_entry

                    used = fm_effects[ch]
                    if not pitch.slide_speed and not loop_slides[ch]:
                        used = used - {1, 2, 3}
                    validate_loop_entry(entry, used)
                    initial = loop_fm_vibrato[ch]
                    explicit = any(effect == 0x04 for _, effect, _ in entry.effects)
                    if (pitch.vibrato_depth, pitch.vibrato_rate) != initial[
                        :2
                    ] and not explicit:
                        raise FormatError(
                            f"channel {ch}: loop entry must reset changed FM vibrato"
                        )
                    resetting = (
                        entry.note is not None
                        and entry.note < 180
                        and not pitch.continuous
                    )
                    disabled = any(
                        effect == 0x04 and not value
                        for _, effect, value in entry.effects
                    )
                    if (
                        pitch.vibrato_depth
                        and pitch.vibrato_phase != initial[2]
                        and not resetting
                        and not disabled
                    ):
                        raise FormatError(
                            f"channel {ch}: loop must preserve or reset FM vibrato phase"
                        )
                    if pitch.fine != loop_fm_fine[ch] and not any(
                        effect == 0xE5 for _, effect, _ in entry.effects
                    ):
                        raise FormatError(
                            f"channel {ch}: loop entry must reset changed FM fine pitch"
                        )
                    if (pitch.active and pitch.cursors or levels[ch].cursor) and not (
                        entry.note is not None and entry.note < 180
                    ):
                        raise FormatError("FM macro loop entry requires a fresh note")
                for ch, envelope in envelopes.items():
                    entry = module.cell(sub, *loop_coordinate, ch)
                    initial = loop_psg_vibrato[ch]
                    explicit = any(e == 4 for _, e, _ in entry.effects)
                    if (envelope.vibrato_depth, envelope.vibrato_rate) != initial[
                        :2
                    ] and not explicit:
                        raise FormatError(
                            f"channel {ch}: loop entry must reset changed PSG vibrato"
                        )
                    resetting = (
                        entry.note is not None
                        and entry.note < 180
                        and not envelope.continuous
                    )
                    disabled = any(e == 4 and not v for _, e, v in entry.effects)
                    if (
                        envelope.vibrato_depth
                        and envelope.vibrato_phase != initial[2]
                        and not resetting
                        and not disabled
                    ):
                        raise FormatError(
                            f"channel {ch}: loop must preserve or reset PSG vibrato phase"
                        )
                    if envelope.fine != loop_fine[ch] and not any(
                        effect == 0xE5 for _, effect, _ in entry.effects
                    ):
                        raise FormatError(
                            f"channel {ch}: loop entry must reset changed PSG fine pitch"
                        )
                    if (
                        envelope.active
                        and envelope.macro is not None
                        and not (entry.note is not None and entry.note <= 180)
                    ):
                        raise FormatError("PSG macro loop entry requires a fresh note")
                timing.row(*loop_coordinate, mapping)
                if timing.pre != loop_timing:
                    raise FormatError(
                        "loop must preserve groove phase/tempo or reset it explicitly at entry"
                    )
            steal_order = item.get("fm_steal_order", [f"fm{i}" for i in range(5)])
            psg_order = item.get("psg_steal_order", [f"tone{i}" for i in range(3)])
            steal_ranks = bytes(steal_order.index(f"fm{i}") for i in range(5)) + bytes(
                psg_order.index(f"tone{i}") for i in range(3)
            )
            songs.append(
                struct.pack(
                    "<HBB", item["id"], len(orders) - 1, 255 if loop is None else loop
                )
                + ptr(table)
                + bytes(
                    (
                        (
                            sum(1 << slot for slot in slots if slot < 8)
                            | (128 if paired_noise else 0)
                        ),
                    )
                )
                + (
                    ptr(pack.add(part_data))
                    + bytes(
                        (
                            len(voices) | (128 if 9 in mapping.values() else 0),
                            len(arrangements),
                            initial_arrangement,
                        )
                    )
                    + rate5(s.hz).to_bytes(2, "little")
                    if logical
                    else bytes(6) + rate5(s.hz).to_bytes(2, "little")
                )
                + steal_ranks
            )
    if timer == 1020 and len(patches) > 63:
        raise FormatError("hybrid bank exceeds 63 resident FM patches")
    patchaddr = pack.add(b"".join(patches) if patches else bytes(32))
    songaddr = pack.add(b"".join(songs))
    descriptors = []
    for e in effects:
        head = struct.pack(
            "<HBB",
            e["id"],
            {
                "pcm": 1,
                "fm": 2,
                "psg": 3,
                "noise": 4,
                "coupled": 5,
                "multi_psg": 6,
                "tone_group": 7,
                "multi_fm": 8,
            }[e["kind"]],
            e["priority"],
        ) + ptr(e["offset"])
        if e["kind"] == "pcm":
            loop = e["info"]["loop"] or [0, 0]
            descriptors.append(head + ptr(e["length"]) + ptr(loop[0]) + ptr(loop[1]))
        else:
            descriptors.append(
                head + bytes((e["mask"],)) + ptr(e.get("loop", 0)) + bytes(5)
            )
    effectaddr = pack.add(b"".join(descriptors))
    # Descriptor ordinals fit in a byte (at most 255 effects); zero is absent.
    pages = {}
    for ordinal, e in enumerate(effects, 1):
        pages.setdefault(e["id"] >> 8, bytearray(256))[e["id"] & 255] = ordinal
    directory = bytearray(768)
    for high, page in sorted(pages.items()):
        directory[high * 3 : high * 3 + 3] = ptr(pack.add(page))
    effectindex = pack.add(directory) if effects else 0
    pack.data[:32] = (
        b"BGM1"
        + bytes((12, 0, len(songs), len(effects), len(patches), 0))
        + ptr(songaddr)
        + ptr(effectaddr)
        + ptr(patchaddr)
        + (
            timer.to_bytes(2, "little")
            + bytes((region == "pal",))
            + ptr(effectindex)
            + bytes(7)
        )
    )
    report = {
        "format": "BGM1-12",
        "runtime_profile": "channel-patterns-exclusive-pcm",
        "region": region,
        "bytes": len(pack.data),
        "sha256": hashlib.sha256(pack.data).hexdigest(),
        "patches": len(patches),
        "songs": len(songs),
        "effects": effects,
        "sample_variants": len(sample_cache),
        "music_samples": sample_report,
        "dependencies": sorted(set(dependencies)),
        "rows": debug,
    }
    return bytes(pack.data), report, ids_header(m)
