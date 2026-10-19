"""Five already-playing FM channels change every instrument together."""
import argparse
import copy
import ctypes as C
import json
from pathlib import Path
from unittest.mock import patch

from calliope.compiler import compile_bank, fm_patch
from calliope.furnace import Cell, parse
from libretro_host import Host

ROOT = Path(__file__).resolve().parents[1]


def fixture(out, pal):
    source = ROOT / "examples/fm_ring.fur"
    module = parse(source)
    song = module.subsongs[0]
    song.rows = 16
    song.speeds = [3]
    song.hz = 60
    song.orders = [[0, 1, 0, 1] for _ in module.channels]
    module.flags["compat_legacy"][1] = 2
    originals, replacements = [], []
    for channel in range(5):
        first = copy.deepcopy(module.instruments[0])
        first["features"].pop("MA", None)
        fm = bytearray(first["features"]["FM"])
        fm[5] = (fm[5] + channel) & 15
        first["features"]["FM"] = bytes(fm)
        second = copy.deepcopy(first)
        fm[1] ^= 0x11
        fm[2] ^= 0x11
        for operator in range(4):
            at = 5 + operator * 8
            for field in range(7):
                fm[at + field] ^= 1
        second["features"]["FM"] = bytes(fm)
        originals.append(first)
        replacements.append(second)
    module.instruments = originals + replacements
    module.patterns = {}
    for channel in range(5):
        for pattern in (0, 1):
            module.patterns[0, channel, pattern] = {
                0: Cell(note=108 + channel, instrument=channel + pattern * 5, volume=127,
                        effects=[(0, 0xee, 0xa0 + channel), (1, 0xee, 0xb0 + channel)]),
                14: Cell(note=110 + channel),
            }
    voices = [{"channel": i, "slot": "fm" + str(i)} for i in range(5)]
    # Reserve the PCM path to exercise the same output frontier as a mixed song.
    voices.append({"channel": 5, "slot": "pcm"})
    manifest = out / "bank.json"
    manifest.write_text(json.dumps({"version": 1, "songs": [{
        "id": 1, "name": "burst", "source": str(source), "voices": voices,
        "external_commands": "passthrough",
    }]}))
    with patch("calliope.compiler.parse", return_value=module):
        bank, _, _ = compile_bank(manifest, "pal" if pal else "ntsc")
    raw = bytearray((ROOT / "audition/out/rom.bin").read_bytes())
    base = raw.index(b"BGM1")
    raw[base:base + len(bank)] = bank
    rom = out / "burst.bin"
    rom.write_bytes(raw)
    expected = [[fm_patch(module.instruments[channel + pattern * 5])
                 for channel in range(5)] for pattern in range(2)]
    return rom, expected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", type=Path, default=ROOT / "out/trace-core/build/calliope_trace.dll")
    parser.add_argument("--pal", action="store_true")
    parser.add_argument("--gain", type=int, default=0, choices=range(128))
    args = parser.parse_args()
    out = ROOT / "out" / ("instrument-burst-" + ("pal" if args.pal else "ntsc") + "-" + str(args.gain))
    out.mkdir(parents=True, exist_ok=True)
    rom, expected = fixture(out, args.pal)
    host = Host(args.core, rom, out, {b"genesis_plus_gx_region_detect": b"pal" if args.pal else b"ntsc"})
    host.core.clp_zram.restype = C.POINTER(C.c_ubyte)
    zram = host.core.clp_zram()
    frame_clocks = 3420 * (313 if args.pal else 262)
    writes = []
    frame = 0
    callback = C.CFUNCTYPE(None, C.c_uint, C.c_uint, C.c_uint, C.c_uint)(
        lambda clocks, reg, value, pc: writes.append(
            (frame + clocks / frame_clocks, reg, value)
        ) if reg != 0x2a and reg != 0x27 else None
    )
    host.core.clp_chip_trace_set.argtypes = [type(callback)]
    host.core.clp_chip_trace_set(callback)
    boundaries = {}
    gain_sent = False
    try:
        for frame in range(350):
            host.run(1)
            z = bytes(zram[:8192])
            tick = int.from_bytes(z[0x1d0a:0x1d0e], "little")
            if args.gain and tick >= 20 and not gain_sent:
                w = z[0x1d01]
                data = bytes((12, 0, z[0x1d08], z[0x1d09], args.gain, 0, 0, 0))
                for i, value in enumerate(data):
                    zram[0x1d40 + (w & 15) * 8 + i] = value
                zram[0x1d01] = (w + 1) & 255
                gain_sent = True
            if z[0x1d0e] and z[0x1e8b] in (1, 2, 3):
                boundaries.setdefault(z[0x1e8b], frame)
            assert not z[0x1d05] and not z[0x1d1a], (frame, z[0x1d05], z[0x1d1a])
        assert len(boundaries) == 3, boundaries
        assert z[0x1738:0x1740] == z[0x1f78:0x1f80] == bytes([165] * 8)
        result = []
        for order, boundary in boundaries.items():
            first, gaps, intervals = [], [], []
            for voice, channel in enumerate((0, 1, 2, 4, 5)):
                keys = [(when, value) for when, reg, value in writes if reg == 0x28 and value & 7 == channel]
                on = next(when for when, value in keys if when >= boundary and value & 0xf0)
                # Every voice has already played in the preceding order.
                assert any(when < boundary and value & 0xf0 for when, value in keys)
                previous = max(when for when, value in keys if when < boundary and value & 0xf0)
                intervals.append((on - previous) * 1000 / host.video_hz)
                off = next(when for when, value in keys if boundary <= when < on and not value & 0xf0)
                # Check every instrument register at the actual key-on. This
                # catches omitted operator groups and shared-cache corruption.
                state = {reg: value for when, reg, value in writes if when <= on}
                patch_data = expected[order & 1][voice]
                port = 0x100 if voice >= 3 else 0
                local = voice - 3 if voice >= 3 else voice
                for index, value in enumerate(patch_data[:30]):
                    reg = (0x30 + index * 4 if index < 28 else
                           0xb0 + (index - 28) * 4) + local + port
                    if 4 <= index < 8 and patch_data[31] & (1 << (index - 4)):
                        value = min(127, value + args.gain)
                    assert state.get(reg) == value, (order, voice, hex(reg), state.get(reg), value)
                assert next(value for when, value in keys if when == on) & 0xf0 == (patch_data[30] << 4) & 0xf0
                first.append(on)
                gaps.append((on - off) * 1000 / host.video_hz)
            result.append({"order": order, "maximum_keyoff_gap_ms": max(gaps),
                           "note_intervals_ms": intervals,
                           "entrance_spread_ms": (max(first) - min(first)) * 1000 / host.video_hz})
        report = {"region": "pal" if args.pal else "ntsc", "gain": args.gain,
                  "boundaries": result, "fault": z[0x1d05], "underrun": z[0x1d1a], "guards": True, "instrument_registers": True}
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        assert all(row["maximum_keyoff_gap_ms"] < 1000 / 60 for row in result)
        # Hardware writes are serial, but no voice may lose more than two
        # ticks to a burst; all voices were already sounding before the change.
        assert all(row["entrance_spread_ms"] < 2 * 1000 / 60 for row in result)
        assert all(100 - 1000 / 60 <= interval < 100 + 2 * 1000 / 60
                   for row in result for interval in row["note_intervals_ms"])
    finally:
        host.close()


if __name__ == "__main__":
    main()
