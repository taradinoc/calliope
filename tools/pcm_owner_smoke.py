"""A PSG allocation must not corrupt PCM ownership or block later PCM notes.

Build a pure Z80 audition ROM first, or pass a pure Z80 consumer ROM.
Requires the register-tracing emulator core.
"""
import argparse
import ctypes as C
import json
import math
import struct
from pathlib import Path

from libretro_host import Host

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rom', type=Path, default=ROOT / 'audition/out/rom.bin')
    parser.add_argument('--driver-image', type=Path)
    parser.add_argument('--psg-effect', type=int, help='specific PSG effect ID (Bonk landing is 2)')
    parser.add_argument('--pal', action='store_true')
    args = parser.parse_args()
    out = ROOT / 'out' / ('pcm-owner-' + ('pal' if args.pal else 'ntsc'))
    out.mkdir(exist_ok=True)
    raw = bytearray(args.rom.read_bytes())
    if args.driver_image:
        driver = (ROOT / 'out/driver.bin').read_bytes()
        at = raw.index(driver[:256])
        image = args.driver_image.read_bytes()
        assert len(image) == 8192
        raw[at:at + 8192] = image
    base = raw.index(b'BGM1\x0c\x00')
    assert int.from_bytes(raw[base + 19:base + 21], 'little') == 1016
    assert raw[base + 21] == int(args.pal)
    table = base + int.from_bytes(raw[base + 13:base + 16], 'little')
    descriptors = list(range(table, table + raw[base + 7] * 16, 16))
    pcm = next(at for at in descriptors if raw[at + 2] == 1 and not any(raw[at + 13:at + 16]))
    psg = next(at for at in descriptors if 3 <= raw[at + 2] <= 7
               and (args.psg_effect is None or int.from_bytes(raw[at:at + 2], 'little') == args.psg_effect))
    length = int.from_bytes(raw[pcm + 7:pcm + 10], 'little')
    frames = math.ceil(length / 80) + 30
    rom = out / 'test.bin'
    rom.write_bytes(raw)
    host = Host(ROOT / 'out/trace-core/build/calliope_trace.dll', rom, out,
                {b'genesis_plus_gx_region_detect': b'pal' if args.pal else b'ntsc'})
    host.core.clp_zram.restype = C.POINTER(C.c_ubyte)
    ram = host.core.clp_zram()
    samples = bytearray()
    callback = C.CFUNCTYPE(None, C.c_uint, C.c_uint, C.c_uint, C.c_uint)(
        lambda clocks, value, pc, caller: samples.append(value))
    host.core.clp_trace_set.argtypes = [type(callback)]
    host.core.clp_trace_set(callback)

    def command(op, argument=0, token=0):
        writer = ram[0x1d01]
        assert (writer - ram[0x1d02]) & 255 < 16
        at = 0x1d40 + (writer & 15) * 8
        for i, value in enumerate(struct.pack('<BBHI', op, 0, token, argument)):
            ram[at + i] = value
        ram[0x1d01] = (writer + 1) & 255

    try:
        host.run(600)
        command(3)
        host.run(10)
        command(4, (pcm - base) | (1 << 24), 18)
        host.run(2)
        assert ram[0x1ec0] == 1, 'initial PCM effect not accepted'
        command(9, (psg - base) | (8 << 24), 2)
        host.run(2)
        assert ram[0x1ec0] in (0, 1), ('PSG allocation corrupted PCM owner', ram[0x1ec0])
        host.run(frames)
        assert ram[0x1eac] == 0 and ram[0x1ec0] == 0, 'PCM ownership not released'
        for op, owner in ((4, 1), (5, 0)):
            samples.clear()
            command(op, (pcm - base) | ((1 << 24) if op == 4 else 0), 18)
            host.run(2)
            assert ram[0x1eac] == 1 and ram[0x1ec0] == owner, 'fresh PCM request blocked'
            host.run(frames)
            assert any(v != 128 for v in samples), 'fresh PCM request silent'
            assert ram[0x1eac] == 0 and ram[0x1ec0] == 0
        # Music priority 2 protects drums against priority-1 footsteps in both
        # arrival orders; priority-8 effects still interrupt drums. SFX win ties.
        ram[0x1d1e] = 2
        command(5, pcm - base)
        host.run(2)
        command(4, (pcm - base) | (1 << 24), 18)
        host.run(2)
        assert ram[0x1ec0] == 0, 'low-priority SFX interrupted music'
        command(4, (pcm - base) | (8 << 24), 4)
        host.run(2)
        assert ram[0x1ec0] == 1 and ram[0x1ead] == 8, 'high-priority SFX blocked'
        command(5, pcm - base)
        host.run(2)
        assert ram[0x1ec0] == 1, 'music interrupted higher-priority SFX'
        command(3)
        host.run(10)
        command(4, (pcm - base) | (1 << 24), 18)
        host.run(2)
        assert ram[0x1ec0] == 1, 'footstep cannot play in a music gap'
        command(5, pcm - base)
        host.run(2)
        assert ram[0x1ec0] == 0, 'drum did not replace low-priority SFX'
        command(4, (pcm - base) | (2 << 24), 18)
        host.run(2)
        assert ram[0x1ec0] == 1, 'SFX must win ties against music'
        command(5, pcm - base)
        host.run(2)
        assert ram[0x1ec0] == 1, 'music must yield to an equal-priority SFX'
        host.run(frames)
        assert ram[0x1ec0] == 0 and ram[0x1eac] == 0
        assert not ram[0x1d05] and not ram[0x1d1a], 'driver fault/PCM underrun'
        assert bytes(ram[0x1738:0x1740]) == bytes([165] * 8)
        assert bytes(ram[0x1f78:0x1f80]) == bytes([165] * 8)
        report = {'psg_preserves_pcm_owner': True, 'equal_priority_sfx_audible': True,
                  'music_pcm_audible': True, 'music_pcm_priority': True, 'fault': 0, 'underrun': 0}
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    finally:
        host.close()


if __name__ == '__main__':
    main()
