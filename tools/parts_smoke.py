"""Exercise assembled logical parts through API buttons and boundary commands."""
import argparse
import ctypes as C
import json
import re
import struct
from pathlib import Path
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pal',action='store_true');args=parser.parse_args()
    out=ROOT/'out'/('parts-pal' if args.pal else 'parts-ntsc');out.mkdir(exist_ok=True)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1')
    table=base+int.from_bytes(raw[base+13:base+16],'little')
    descriptor=next(p for p in range(table,table+raw[base+7]*16,16) if raw[p+2]==2)
    raw[descriptor+7]=1
    rom=out/'parts.bin';rom.write_bytes(raw)
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':b'pal' if args.pal else b'ntsc'})
    h.core.retro_set_controller_port_device(0,513)  # Explicit six-button Mega Drive pad.
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    writes=[];registers={}
    def record(cycles,register,value,pc):
        if register!=0x2a:writes.append((register,value));registers[register]=value
    callback=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record)
    h.core.clp_chip_trace_set.argtypes=[type(callback)];h.core.clp_chip_trace_set(callback)
    def ram():
        s=h.save();o=s.find(needle)-0x38;assert o>=0;return s[o:o+8192]
    def tick():return int.from_bytes(ram()[0x1d0a:0x1d0e],'little')
    def enabled():return sum(1<<i for i in range(3) if ram()[0x1b03+i*16])
    def button(id):h.run(1,(id,));h.run(1)
    def until(target):
        for _ in range(300):
            if tick()>=target:return
            h.run(1)
        raise AssertionError('sequencer did not advance')
    def command(op,flags,arg,token=None):
        s=bytearray(h.save());o=s.find(needle)-0x38;w=s[o+0x1d01];p=o+0x1d40+(w&15)*8
        token=int.from_bytes(s[o+0x1d08:o+0x1d0a],'little') if token is None else token
        s[p:p+8]=struct.pack('<BBHI',op,flags,token,arg);s[o+0x1d01]=(w+1)&255;h.load(bytes(s))
    def frequency(note):
        from calliope.fm import frequency as compile_frequency
        return compile_frequency(note+60,"pal" if args.pal else "ntsc")
    try:
        h.run(100);button(10);until(3)
        assert ram()[0x1f1a]==3 and enabled()==5,('initial happy+bass arrangement',ram()[0x1f1a],enabled(),tick())
        command(11,1,6,65535);h.run(2);assert enabled()==5,'stale arrangement must be ignored'
        writes.clear();button(9);until(14)
        assert enabled()==6,'Y must select sad+bass when the request is consumed'
        assert (0x28,1) not in writes,'arrangement change must not key off bass'
        assert ram()[0x1f30]==1,'new melody must wait for a fresh note'
        button(11);h.run(3);assert enabled()==4,'group mute should preserve bass'
        button(11);h.run(3);assert enabled()==6,'group unmute preserves selected arrangement'
        until(33)
        assert registers[0xa0]|(registers[0xa4]<<8)==frequency(51),('silent sad track must have advanced to E-flat',tick(),hex(registers[0xa0]|(registers[0xa4]<<8)),hex(frequency(51)),ram()[0x1b00:0x1b30].hex(),writes[-20:])
        command(9,0,(descriptor-base)|(2<<24),70);h.run(3)
        assert ram()[0x1f20]!=0,'FM SFX must steal melody'
        button(9);h.run(6);assert enabled()==5 and ram()[0x1f20]!=0,'switch must preserve SFX owner'
        until(96)
        assert ram()[0x1f20]==0 and ram()[0x1f30]==0,'current part must restore after SFX'
        assert registers[0xa0]|(registers[0xa4]<<8)==frequency(60),'restoration must use current happy note'
        boundaries=[]
        for flag,marker,wanted in ((8,0,97),(16,2,61)):
            button(10);until(3);command(11,flag,6|(marker<<16))
            for _ in range(180):
                h.run(1)
                if enabled()==6:break
            applied=tick();assert wanted<=applied<=wanted+2,(flag,applied,wanted)
            boundaries.append(applied)
        assert ram()[0x1d05]==0 and ram()[0x1d1a]==0,'driver fault/underrun'
        assert ram()[0x1738:0x1740]==bytes([165]*8) and ram()[0x1f78:0x1f80]==bytes([165]*8)
        report={'api_group_and_arrangement_controls':True,'bass_uninterrupted':True,'alternate_progression':True,'sfx_restoration':True,'boundary_ticks':boundaries,'fault':0,'pcm_underrun':0}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    finally:h.close()
if __name__=='__main__':main()
