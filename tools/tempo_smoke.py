"""Exercise assembled tempo arithmetic, independent boundaries, pause and SFX clocks."""
import argparse
import ctypes as C
import json
import struct
from pathlib import Path
from libretro_host import Host
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pal", action="store_true")
    parser.add_argument("--frames", type=int, default=600)
    args = parser.parse_args()
    region = "pal" if args.pal else "ntsc"
    out = ROOT / "out" / ("tempo-" + region)
    out.mkdir(exist_ok=True)
    rom = ROOT / "audition/out/rom.bin"
    host = Host(ROOT / "out/trace-core/build/calliope_trace.dll", rom, out,
                {b"genesis_plus_gx_region_detect": region.encode()})
    host.core.retro_set_controller_port_device(0, 513)
    host.core.clp_zram.restype = C.POINTER(C.c_ubyte)
    needle = (ROOT / "out/driver.bin").read_bytes()[0x38:0x50]

    def ram():
        return bytes(host.core.clp_zram()[:8192])

    def word(at):
        return int.from_bytes(ram()[at:at+2], "little")

    def tick():
        return int.from_bytes(ram()[0x1d0a:0x1d0e], "little")

    def command(op, flags=0, value=0, generation=None):
        state = bytearray(host.save())
        offset = state.find(needle) - 0x38
        assert offset >= 0
        w = state[offset+0x1d01]
        if generation is None:
            generation = int.from_bytes(state[offset+0x1d08:offset+0x1d0a], "little")
        at = offset + 0x1d40 + (w & 15) * 8
        state[at:at+8] = struct.pack("<BBHI", op, flags, generation, value)
        state[offset+0x1d01] = (w+1) & 255
        host.load(bytes(state))
        host.run(2)

    def healthy():
        z = ram()
        assert not z[0x1d05] and not z[0x1d1a], (z[0x1d05], z[0x1d1a], tick())
        assert z[0x1738:0x1740] == bytes([165]*8)
        assert z[0x1f78:0x1f80] == bytes([165]*8)

    try:
        host.run(100)
        host.run(1, (10,))  # X: start looping arrangement fixture.
        host.run(3)
        assert word(0x1d22) == 256
        baseline = host.save()
        command(7)
        paused = host.save()
        # Exercise every scale at both authored rate bounds, plus middle cases.
        cases = [(rate, scale) for rate in (125, 600) for scale in range(128, 513)]
        cases += [(rate, scale) for rate in (255, 256, 300, 599)
                  for scale in (128, 129, 255, 256, 257, 384, 511, 512)]
        for rate, scale in cases:
            state = bytearray(paused)
            offset = state.find(needle)-0x38
            state[offset+0x1f50:offset+0x1f52] = rate.to_bytes(2, "little")
            host.load(bytes(state))
            credit = word(0x1f52)
            command(13, 1, scale)
            assert word(0x1d29) == (rate*scale+128)//256, (rate, scale, word(0x1d29))
            assert word(0x1f52) == credit, "scale reset musical phase while paused"
            healthy()
        host.load(paused)
        command(13, 8, 512)
        command(13, 16, 128 | (2 << 16))
        command(13, 1, 384)
        assert word(0x1d22) == 384 and not ram()[0x1d24]
        command(13, 1, 512, 65535)
        assert word(0x1d22) == 384, "stale request changed tempo"
        # Parts and tempo can wait independently for the same marker.
        host.load(baseline)
        command(13, 16, 512 | (2 << 16))
        command(11, 16, 6 | (2 << 16))
        assert word(0x1d22) == 256
        for _ in range(150):
            host.run(1)
            if word(0x1d22) == 512:
                break
        assert 61 <= tick() <= 65 and ram()[0x1b13] == 1, (tick(), word(0x1d22))
        command(13, 8, 128)
        for _ in range(150):
            host.run(1)
            if word(0x1d22) == 128:
                break
        assert 97 <= tick() <= 100, tick()
        command(13, 16, 512 | (2 << 16))
        command(2)
        assert not ram()[0x1d24], "stop retained pending tempo"
        host.run(1, (10,)); host.run(3)
        assert word(0x1d22) == 256 and not ram()[0x1d24], "start retained tempo"
        advances = []
        for scale in (128, 512):
            host.load(baseline); command(13, 1, scale)
            before = tick(); host.run(120); advances.append(tick()-before)
        assert abs(advances[1]-4*advances[0]) <= 5, advances
        # SFX lengths do not scale with music.
        raw = rom.read_bytes(); base = raw.index(b"BGM1")
        table = base + int.from_bytes(raw[base+13:base+16], "little")
        descriptor = next(at-base for at in range(table, table+raw[base+7]*16, 16)
                          if int.from_bytes(raw[at:at+2], "little") == 3)
        lengths = []
        for scale in (128, 512):
            host.load(baseline); command(13, 1, scale)
            command(9, 0, descriptor | (2 << 24), 91)
            frames = 0
            while any(ram()[0x1800+i*16] for i in range(4)) and frames < 200:
                host.run(1); frames += 1
            assert frames < 200
            lengths.append(frames); healthy()
        # Commands and completion are sampled at video-frame edges.
        assert abs(lengths[0]-lengths[1]) <= 1, lengths
        # PCM-heavy score at the maximum scale with normal per-frame updates.
        host.load(baseline)
        songs = base + int.from_bytes(raw[base+10:base+13], "little")
        song = next(at-base for at in range(songs, songs+raw[base+6]*24, 24)
                    if int.from_bytes(raw[at:at+2], "little") == 5)
        command(1, 0, song, word(0x1d08)+1)
        command(13, 1, 512)
        for _ in range(args.frames):
            host.run(1); healthy()
        result = dict(region=region, arithmetic_cases=len(cases), boundaries=True,
                      pause_phase=True, stale_rejected=True, start_reset=True,
                      sfx_frames=lengths, tick_advances=advances, maximum_scale_frames=args.frames,
                      fault=0, underrun=0)
        (out / "report.json").write_text(json.dumps(result, indent=2)+"\n")
        print(json.dumps(result))
    finally:
        host.close()


if __name__ == "__main__":
    main()
