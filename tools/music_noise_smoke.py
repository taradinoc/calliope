"""Tone-derived music noise shares ownership and held state with PSG tone 3."""
import argparse,ctypes as C,json,struct
from pathlib import Path
from unittest.mock import patch
from libretro_host import Host
from calliope.furnace import parse,Cell
from calliope.compiler import compile_bank
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--pal',action='store_true');a=p.parse_args();region='pal' if a.pal else 'ntsc'
    out=ROOT/'out'/('music-noise-'+region);out.mkdir(exist_ok=True)
    source=ROOT/'examples/noise232.fur';module=parse(source);song=module.subsongs[0]
    song.rows=128;song.speeds=[1];song.orders=[[0] for _ in module.channels]
    module.instruments=[{'kind':0,'features':{}}]
    module.patterns={(0,3,0):{0:Cell(note=108,instrument=0,volume=12),10:Cell(volume=6),100:Cell(note=112),120:Cell(note=180)}}
    manifest=out/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[{'id':3,'name':'pair','source':str(source),'voices':[{'name':'noise','channel':3,'slot':'noise'}],'groups':{'all':['noise']}}],'sfx':[{'id':4,'name':'tone','source':str(ROOT/'examples/psg_ring.fur'),'resource_mask':128}]}))
    with patch('calliope.compiler.parse',side_effect=lambda path:module if Path(path)==source else parse(path)):data,_,_=compile_bank(manifest,region)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1');raw[base:base+len(data)]=data;rom=out/'pair.bin';rom.write_bytes(raw)
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':region.encode()});h.core.retro_set_controller_port_device(0,513)
    values=[];volume={};period={};latch=0
    def record(cycles,reg,value,pc):
        nonlocal latch
        if reg!=0x200:return
        values.append(value)
        if value&128:
            latch=(value>>4)&7
            if latch&1:volume[latch>>1]=value&15
            else:period[latch>>1]=(period.get(latch>>1,0)&1008)|(value&15)
        elif not latch&1:period[latch>>1]=(period.get(latch>>1,0)&15)|((value&63)<<4)
    cb=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record);h.core.clp_chip_trace_set.argtypes=[type(cb)];h.core.clp_chip_trace_set(cb)
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    def snapshot():
        s=bytearray(h.save());o=s.find(needle)-0x38;assert o>=0;return s,o
    def ram():
        s,o=snapshot();return s[o:o+8192]
    def command(op,flags,arg,token=None):
        s,o=snapshot();w=s[o+0x1d01];pos=o+0x1d40+(w&15)*8
        if token is None:token=int.from_bytes(s[o+0x1d08:o+0x1d0a],'little')
        s[pos:pos+8]=struct.pack('<BBHI',op,flags,token,arg);s[o+0x1d01]=(w+1)&255;h.load(bytes(s));h.run(3)
    try:
        h.run(100);h.run(1,(10,));h.run(4)
        assert volume[3]==3 and volume[2]==15,volume
        assert ram()[0x1b06]==0xe7 and ram()[0x1b09]&0xf0==0xc0
        descriptor=int.from_bytes(data[13:16],'little');command(9,0,descriptor|(2<<24),91)
        assert ram()[0x1f27] and volume[3]==15,'tone SFX did not silence coupled music noise'
        h.run(8);command(6,0,91);h.run(3)
        assert volume[3]==15 and ram()[0x1f38],'music resumed before a fresh note'
        command(10,0,128);assert volume[3]==15,'muting tone3 must mute derived noise'
        command(10,0,0);assert volume[3]==15,'unmute must wait for a fresh note'
        command(10,0,256);assert volume[3]==15
        command(10,0,0)
        state=ram();assert not state[0x1d05] and not state[0x1d1a]
        assert state[0x1738:0x1740]==bytes([165]*8) and state[0x1f78:0x1f80]==bytes([165]*8)
        result={'region':region,'tone_steals_noise':True,'mute_either_resource':True,'next_note_default':True,'fault':0,'underrun':0}
        (out/'report.json').write_text(json.dumps(result,indent=2)+'\n');print(result)
    finally:h.close()
if __name__=='__main__':main()
