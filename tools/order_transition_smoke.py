"""Measure Green Hill's actual YM key timing across its first order change."""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import wave

from libretro_host import Host

ROOT = Path(__file__).resolve().parents[1]


def measure(core, rom, out, pal, name):
    region = b"pal" if pal else b"ntsc"
    host = Host(core, rom, out, {b"genesis_plus_gx_region_detect": region})
    host.core.clp_zram.restype = C.POINTER(C.c_ubyte)
    frame_clocks = 3420 * (313 if pal else 262)
    writes, audio = [], []
    host.audio_sink = audio.append
    frame = 0
    callback = C.CFUNCTYPE(None, C.c_uint, C.c_uint, C.c_uint, C.c_uint)(
        lambda clocks, reg, value, pc: writes.append(
            (frame + clocks / frame_clocks, value)
        ) if reg == 0x28 else None
    )
    host.core.clp_chip_trace_set.argtypes = [type(callback)]
    host.core.clp_chip_trace_set(callback)
    boundary = None
    maximum_pending = 0
    try:
        for frame in range(400):
            host.run(1)
            z = bytes(host.core.clp_zram()[:8192])
            maximum_pending = max(maximum_pending, z[0x1e80])
            assert not z[0x1d05] and not z[0x1d1a], (frame, z[0x1d05], z[0x1d1a])
            if z[0x1d0e] and z[0x1e8b] == 1 and boundary is None:
                boundary = frame
                assert int.from_bytes(z[0x1d0a:0x1d0e], "little") == 97
        assert boundary is not None, "Green Hill did not reach order 1"
        assert z[0x1738:0x1740] == z[0x1f78:0x1f80] == bytes([165] * 8)
        first, gaps = {}, {}
        for channel in (0, 1, 2, 4, 5):
            channel_writes = [(when, value) for when, value in writes if value & 7 == channel]
            on = next(when for when, value in channel_writes if when >= boundary and value & 0xf0)
            first[channel] = on
            offs = [when for when, value in channel_writes if boundary <= when <= on and not value & 0xf0]
            if offs:
                gaps[channel] = (on - offs[0]) * 1000 / host.video_hz
        previous = max(when for when, value in writes if when < boundary and value == 0xf0)
        result = {
            "rom_sha256": hashlib.sha256(rom.read_bytes()).hexdigest(),
            "region": region.decode(),
            "boundary_frame": boundary,
            "lead_interval_ms": (first[0] - previous) * 1000 / host.video_hz,
            "patch_keyoff_to_keyon_ms": gaps,
            "entrance_spread_ms": (max(first.values()) - min(first.values())) * 1000 / host.video_hz,
            "maximum_pending": maximum_pending,
            "fault": z[0x1d05],
            "underrun": z[0x1d1a],
            "guards": True,
        }
        with wave.open(str(out / (name + ".wav")), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(round(host.sample_rate))
            output.writeframes(b"".join(audio))
        return result
    finally:
        host.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", type=Path, default=ROOT / "out/trace-core/build/calliope_trace.dll")
    parser.add_argument("--rom", type=Path, default=ROOT / "audition/out/rom.bin")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--pal", action="store_true")
    args = parser.parse_args()
    out = ROOT / "out" / ("order-transition-pal" if args.pal else "order-transition-ntsc")
    out.mkdir(parents=True, exist_ok=True)
    report = {"current": measure(args.core, args.rom, out, args.pal, "current")}
    if args.baseline:
        report["baseline"] = measure(args.core, args.baseline, out, args.pal, "baseline")
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    current = report["current"]
    # Six nominal 60-Hz ticks separate these notes. Allow one tick of dispatch
    # variation, but reject the previous two-plus-tick hitch and early key-offs.
    assert current["lead_interval_ms"] < 7 * 1000 / 60
    assert max(current["patch_keyoff_to_keyon_ms"].values()) < 1000 / 60
    if args.baseline:
        assert current["lead_interval_ms"] < report["baseline"]["lead_interval_ms"]


if __name__ == "__main__":
    main()
