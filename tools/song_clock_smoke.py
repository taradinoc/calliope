"""Measure a consumer ROM's pure-Z80 song clock and DAC timing.
Uses the existing instrumented core; outputs stay in the selected directory.
The optional strict DAC gate uses trace_dac.py limits per measurement window.
"""
import argparse
import ctypes as C
import json
from pathlib import Path
from libretro_host import Host

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("--core", type=Path, default=ROOT/"out/trace-core/build/calliope_trace.dll")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=6000)
    parser.add_argument("--window", type=int, default=120)
    parser.add_argument("--timer", type=int, default=1016)
    parser.add_argument("--pal", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--reference-registers", type=Path)
    args = parser.parse_args()
    if args.window < 1 or args.frames < 2*args.window or not 0 <= args.timer < 1024:
        parser.error("need at least two positive windows and timer0..1023")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    master = 53203424 if args.pal else 53693175
    frame_clocks = 3420*(313 if args.pal else 262)
    period = 7*144*(1024-args.timer)
    expected = master/period
    region = b"pal" if args.pal else b"ntsc"
    host = Host(args.core, args.rom, args.output_dir, {b"genesis_plus_gx_region_detect": region})
    host.core.clp_zram.restype = C.POINTER(C.c_ubyte)
    ram = host.core.clp_zram()
    frame = 0
    trace_errors = 0
    previous = None
    count = elapsed = maximum = 0
    registers = bytearray()
    windows = []

    def dac(clocks, value, pc, caller):
        nonlocal previous, count, elapsed, maximum, trace_errors
        now = frame*frame_clocks+clocks
        if previous is not None:
            delta = now-previous
            if delta <= 0:
                trace_errors += 1
                previous = now
                return
            count += 1
            elapsed += delta
            maximum = max(maximum, delta)
        previous = now

    def chip(clocks, register, value, pc):
        if register not in (0x24, 0x25, 0x26, 0x27, 0x2a, 0x2b):
            registers.extend(register.to_bytes(2, "little")+bytes((value,)))

    callback = C.CFUNCTYPE(None, C.c_uint, C.c_uint, C.c_uint, C.c_uint)
    dac_callback = callback(dac)
    chip_callback = callback(chip)
    host.core.clp_trace_set.argtypes = [callback]
    host.core.clp_chip_trace_set.argtypes = [callback]
    host.core.clp_trace_set(dac_callback)
    host.core.clp_chip_trace_set(chip_callback)
    fault = underrun = pending = 0
    try:
        for frame in range(args.frames):
            host.run(1)
            fault = max(fault, ram[0x1d05])
            underrun = max(underrun, ram[0x1d1a])
            pending = max(pending, ram[0x1e80])
            if (frame+1) % args.window == 0:
                windows.append(dict(frame=frame+1, writes=count,
                    rate=master*count/elapsed if elapsed else 0,
                    maximum_interval=maximum,
                    ticks=int.from_bytes(bytes(ram[0x1d0a:0x1d0e]), "little")))
                count = elapsed = maximum = 0
        measured = windows[1:]  # omit the startup window
        minimum = min(w["rate"] for w in measured)
        maximum_rate = max(w["rate"] for w in measured)
        largest = max(w["maximum_interval"] for w in measured)
        strict = (minimum >= expected*.999 and maximum_rate <= expected*1.001 and largest <= 2*period)
        prefix_equal = None
        prefix_writes = 0
        if args.reference_registers:
            reference = args.reference_registers.read_bytes()
            prefix_writes = min(len(reference), len(registers))//3
            prefix_equal = reference[:prefix_writes*3] == registers[:prefix_writes*3]
        report = dict(expected_rate=expected, minimum_window_rate=minimum,
            maximum_window_rate=maximum_rate, maximum_interval=largest,
            strict_dac_gate=strict, fault=fault, underrun=underrun, trace_errors=trace_errors,
            maximum_pending=pending, windows=windows,
            register_writes=len(registers)//3, reference_prefix_equal=prefix_equal,
            reference_prefix_writes=prefix_writes,
            music_ticks=windows[-1]["ticks"]-windows[0]["ticks"],
            video_frames=windows[-1]["frame"]-windows[0]["frame"],
            scope="Instrumented software core and supplied consumer ROM; not stock hardware or general driver acceptance")
        (args.output_dir/"registers.bin").write_bytes(registers)
        (args.output_dir/"report.json").write_text(json.dumps(report, indent=2)+"\n")
        print(json.dumps({k:v for k,v in report.items() if k != "windows"}, indent=2))
        if trace_errors or fault or underrun or prefix_equal is False or (args.strict and not strict):
            raise SystemExit(1)
    finally:
        host.close()


if __name__ == "__main__":
    main()
