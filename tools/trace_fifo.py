"""Capture the queue and input frontiers around the first PCM underrun or sequencer fault."""
import argparse,ctypes as C,json
from collections import deque
from pathlib import Path
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--frames',type=int,default=18000);a=p.parse_args()
    out=ROOT/'out/fifo-trace';out.mkdir(exist_ok=True)
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',ROOT/'audition/out/rom.bin',out)
    h.core.clp_zram.restype=C.POINTER(C.c_ubyte);ram=h.core.clp_zram()
    h.core.clp_dac_fifo.restype=C.c_uint
    records=deque(maxlen=1024);frame=0;failed=False
    def record(cycles,value,pc,caller):
        nonlocal failed
        if failed:return
        pointers=h.core.clp_dac_fifo();write=pointers>>8;read=pointers&255
        records.append([frame,cycles,(write-read)&255,ram[0x1ea0+20],ram[0x1ec0+20],ram[0x1f10],ram[0x1f11],ram[0x1ea0+12],ram[0x1ec0+12],caller,ram[0x1e9f],ram[0x1e9b],ram[0x1e9c],ram[0x1e80]])
        if ram[0x1d1a] or ram[0x1d05]:
            failed=True
            (out/'ram.bin').write_bytes(bytes(ram[:8192]))
    cb=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record);h.core.clp_trace_set.argtypes=[type(cb)];h.core.clp_trace_set(cb)
    try:
        h.run(100);h.run(1,(4,));h.run(1)
        for frame in range(a.frames):
            h.run(1,(7,) if frame%2==0 else ())
            if failed:break
        (out/'records.json').write_text(json.dumps({'columns':['frame','cycles','queued','blocks0','blocks1','fill_busy','fill_running','active0','active1','caller','published','fill_left','segment_length','pending'],'records':list(records),'failed':failed},indent=2)+'\n')
        print({'frame':frame,'failed':failed,'last':records[-1]})
    finally:h.close()
if __name__=='__main__':main()
