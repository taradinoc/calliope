"""Trace PSG part selection and live music attenuation in assembled Z80 code."""
import argparse
import ctypes as C
import json, struct, sys
from pathlib import Path
from unittest.mock import patch
from libretro_host import Host
from calliope.compiler import compile_bank
from calliope.furnace import parse
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pal',action='store_true');a=parser.parse_args()
    out=ROOT/'out'/('psg-controls-pal' if a.pal else 'psg-controls-ntsc');out.mkdir(exist_ok=True)
    source=ROOT/'examples/arrangements232.fur';module=parse(source)
    module.channels=[(0,'psg',i) for i in range(len(module.channels))]
    module.instruments=[{'kind':0,'features':{'NA':b'tone\0'}} for _ in module.instruments]
    # Synthetic tones use the existing independently loadable arrangement rows.
    for pattern in module.patterns.values():
        for cell in pattern.values():
            if cell.volume is not None or cell.note is not None and cell.note<180:cell.volume=12
    song=json.loads((ROOT/'examples/bank.json').read_text())['songs'][2]
    song['source']=str(source)
    for voice,slot in zip(song['voices'],('tone0','tone0','tone1')):voice['slot']=slot
    manifest=out/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[song]}))
    with patch('calliope.compiler.parse',return_value=module):
        data,_,_=compile_bank(manifest,'pal' if a.pal else 'ntsc')
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());bank=raw.index(b'BGM1');raw[bank:bank+len(data)]=data
    rom=out/'controls.bin';rom.write_bytes(raw)
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':b'pal' if a.pal else b'ntsc'})
    h.core.retro_set_controller_port_device(0,513)
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50];writes=[];volume={};period={};latch=0
    def record(cycles,reg,value,pc):
        nonlocal latch
        if reg!=0x200:return
        writes.append(value)
        if value&128:
            latch=(value>>4)&7
            if latch&1:volume[latch>>1]=value&15
            else:period[latch>>1]=(period.get(latch>>1,0)&1008)|(value&15)
        elif not latch&1:period[latch>>1]=(period.get(latch>>1,0)&15)|((value&63)<<4)
    cb=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record)
    h.core.clp_chip_trace_set.argtypes=[type(cb)];h.core.clp_chip_trace_set(cb)
    def ram():
        s=h.save();o=s.find(needle)-0x38;assert o>=0;return s[o:o+8192]
    def command(op,flags,arg):
        s=bytearray(h.save());o=s.find(needle)-0x38;w=s[o+0x1d01];p=o+0x1d40+(w&15)*8
        generation=int.from_bytes(s[o+0x1d08:o+0x1d0a],'little')
        s[p:p+8]=struct.pack('<BBHI',op,flags,generation,arg);s[o+0x1d01]=(w+1)&255;h.load(bytes(s));h.run(3)
    try:
        h.run(100);h.run(1,(10,));h.run(2)
        initial=dict(volume);base_period=dict(period);assert volume[0]==3 and volume[1]==3,(volume,period)
        writes.clear();command(12,0,5<<8)
        assert volume[0]==8 and volume[1]==8,(volume,writes,ram()[0x1f54:0x1f5d].hex(),ram()[0x1d00:0x1d20].hex(),ram()[0x1f1d])
        assert all(v&0x90==0x90 for v in writes),'live volume must not rewrite tone periods'
        assert period==base_period
        command(12,0,0);assert volume[0]==3 and volume[1]==3,'volume attenuation accumulated'
        writes.clear();command(11,1,6)
        assert not any(v&0xe0==0xa0 for v in writes),'unchanged bass was written'
        assert volume[0]==15,'enabled alternate must wait for a fresh note'
        command(11,1,5);assert volume[0]==15,'default activation must wait for a fresh note'
        command(12,0,0);assert volume[0]==15,'volume control revived a pending tone'
        assert ram()[0x1d05]==0 and ram()[0x1d1a]==0
        report={'region':'pal' if a.pal else 'ntsc','volume_no_period_writes':True,'unchanged_part_preserved':True,'next_note_default':True,'fault':0,'underrun':0}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:h.close()
if __name__=='__main__':main()
