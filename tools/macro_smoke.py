"""PSG automation must advance silently and never steal a released SFX voice."""
import argparse,ctypes as C,json,struct
from pathlib import Path
from unittest.mock import patch
from libretro_host import Host
from calliope.furnace import parse,Cell
from calliope.compiler import compile_bank
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--pal',action='store_true');a=p.parse_args()
    out=ROOT/'out'/('macros-pal' if a.pal else 'macros-ntsc');out.mkdir(exist_ok=True)
    source=ROOT/'examples/psg_ring.fur';module=parse(source)
    module.subsongs[0].rows=64;module.subsongs[0].speeds=[1];module.subsongs[0].orders=[[0] for _ in module.channels]
    module.patterns={(0,6,0):{0:Cell(note=108,instrument=0,volume=15),50:Cell(note=112,instrument=0),63:Cell(effects=[(0,255,0)])}}
    # Relative arpeggio and absolute pitch, independent bounded sequence clocks.
    module.instruments=[{'kind':0,'features':{'MA':bytes((8,0,1,3,0,255,0,64,0,2,0,7,12,4,2,0,255,0,64,0,3,0,32,255))}}]
    manifest=out/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[{'id':3,'name':'macro','source':str(source),'voices':[{'name':'tone','channel':6,'slot':'tone0'}],'groups':{'all':['tone']},'arrangements':{'main':['tone']}}],'sfx':[{'id':4,'name':'ring','source':str(source),'resource_mask':32}]}))
    actual=parse(source);calls=iter((module,actual))
    with patch('calliope.compiler.parse',side_effect=lambda path:next(calls)):data,_,_=compile_bank(manifest,'pal' if a.pal else 'ntsc')
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1');raw[base:base+len(data)]=data
    rom=out/'macros.bin';rom.write_bytes(raw)
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':b'pal' if a.pal else b'ntsc'});h.core.retro_set_controller_port_device(0,513)
    period=0;volume=15;latch=0;periods=[]
    def record(cycles,reg,value,pc):
        nonlocal period,volume,latch
        if reg!=0x200:return
        if value&128:
            latch=(value>>4)&7
            if latch==0:period=(period&1008)|(value&15)
            elif latch==1:volume=value&15
        elif latch==0:period=(period&15)|((value&63)<<4);periods.append(period)
    cb=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record);h.core.clp_chip_trace_set.argtypes=[type(cb)];h.core.clp_chip_trace_set(cb)
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    def ram():
        s=h.save();o=s.find(needle)-0x38;assert o>=0;return s[o:o+8192]
    def command(op,flags,arg,token=None):
        s=bytearray(h.save());o=s.find(needle)-0x38;w=s[o+0x1d01];at=o+0x1d40+(w&15)*8
        if token is None:token=int.from_bytes(s[o+0x1d08:o+0x1d0a],'little')
        s[at:at+8]=struct.pack('<BBHI',op,flags,token,arg);s[o+0x1d01]=(w+1)&255;h.load(bytes(s));h.run(2)
    try:
        h.run(100);h.run(1,(10,));h.run(8);assert len(set(periods))>=3,'arpeggio/pitch did not reach hardware'
        descriptor=int.from_bytes(data[13:16],'little');command(9,0,descriptor|(2<<24),91)
        assert ram()[0x1f25], 'SFX did not acquire tone'
        h.run(3);command(6,0,91);assert not ram()[0x1f25]
        periods.clear();h.run(6);assert ram()[0x1f35]==1 and volume==15 and not periods,'pitch automation revived stolen music before a fresh note'
        command(10,0,64);assert ram()[0x1f35]==1,'unrelated mute changed pending voice'
        command(10,0,0);periods.clear();command(7,0,0);paused=ram()[0x1d0a:0x1d0e]
        h.run(3);assert ram()[0x1d0a:0x1d0e]==paused,'pause advanced macros'
        periods.clear();command(8,0,0);h.run(2)
        assert ram()[0x1f35]==1 and volume==15 and not periods,'resume revived an old macro note'
        h.run(65);r=ram();assert r[0x1d05]==0 and r[0x1d1a]==0
        assert r[0x1738:0x1740]==bytes([165]*8) and r[0x1f78:0x1f80]==bytes([165]*8)
        report={'pitch_macros':True,'sfx_next_note_policy':True,'pause_resume_next_note':True,'unrelated_mute_preserved':True,'fault':0,'underrun':0,'region':'pal' if a.pal else 'ntsc'}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:h.close()
if __name__=='__main__':main()
