"""FM macro frequency writes must preserve envelopes and next-note ownership."""
import argparse,ctypes as C,json,struct
from pathlib import Path
from unittest.mock import patch
from libretro_host import Host
from calliope.furnace import parse,Cell
from calliope.compiler import compile_bank
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pal',action='store_true');args=parser.parse_args()
    out=ROOT/'out'/('fm-macros-pal' if args.pal else 'fm-macros-ntsc');out.mkdir(exist_ok=True)
    source=ROOT/'examples/fm_ring.fur';module=parse(source);song=module.subsongs[0]
    song.rows=96;song.speeds=[1];song.orders=[[0] for _ in module.channels]
    module.flags['compat_legacy'][1]=2
    module.patterns={(0,0,0):{0:Cell(note=108,instrument=0,volume=127,effects=[(0,4,0x48)]),70:Cell(note=112,instrument=0),90:Cell(note=180)}}
    module.instruments[0]['features']['MA']=bytes((8,0,1,3,0,255,0,64,0,2,0,7,12,4,2,0,255,0,64,0,3,0,32,255))
    manifest=out/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[{'id':3,'name':'music','source':str(source),'voices':[{'name':'melody','channel':0,'slot':'fm0'}],'groups':{'all':['melody']},'arrangements':{'main':['melody']}}],'sfx':[{'id':3,'name':'ring','source':str(source),'resource_mask':1}]}))
    modules=iter((module,parse(source)))
    with patch('calliope.compiler.parse',side_effect=lambda _:next(modules)):data,_,_=compile_bank(manifest,'pal' if args.pal else 'ntsc')
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1');raw[base:base+len(data)]=data
    rom=out/'fm-macros.bin';rom.write_bytes(raw)
    host=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,
              {b'genesis_plus_gx_region_detect':b'pal' if args.pal else b'ntsc'})
    host.core.retro_set_controller_port_device(0,513)
    writes=[];high=0;frequency=0
    def record(cycles,reg,value,pc):
        nonlocal high,frequency
        if reg in (0x28,0xa0,0xa4):writes.append((reg,value))
        if reg==0xa4:high=value
        if reg==0xa0:frequency=(high<<8)|value
    callback=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record)
    host.core.clp_chip_trace_set.argtypes=[type(callback)];host.core.clp_chip_trace_set(callback)
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    def snapshot():
        state=bytearray(host.save());where=state.find(needle)-0x38;assert where>=0
        return state,where
    def ram():
        state,where=snapshot();return state[where:where+8192]
    def command(op,flags,arg,token=None):
        state,where=snapshot();write=state[where+0x1d01]
        if token is None:token=int.from_bytes(state[where+0x1d08:where+0x1d0a],'little')
        at=where+0x1d40+(write&15)*8;state[at:at+8]=struct.pack('<BBHI',op,flags,token,arg)
        state[where+0x1d01]=(write+1)&255;host.load(bytes(state));host.run(2)
    try:
        host.run(100);host.run(1,(10,));host.run(5);writes.clear();host.run(5)
        assert len({v for r,v in writes if r==0xa0})>=2,'FM macro did not reach hardware'
        assert not any(r==0x28 for r,v in writes),'automation restarted the envelope'
        descriptor=int.from_bytes(data[13:16],'little');command(9,0,descriptor|(2<<24),91)
        assert ram()[0x1f20],'SFX did not steal FM0'
        command(6,0,91);writes.clear();host.run(5)
        assert ram()[0x1f30] and not writes,'automation reclaimed music after SFX'
        host.run(85);assert ram()[0x1b07]==0,'note off retained a held gate'
        command(11,1,0);writes.clear();command(11,1,1)
        assert (0x28,0xf0) not in writes,'part selection revived a released note'
        r=ram();assert not r[0x1d05] and not r[0x1d1a]
        assert r[0x1738:0x1740]==bytes([165]*8) and r[0x1f78:0x1f80]==bytes([165]*8)
        report={'continuous_pitch':True,'envelope_preserved':True,'next_note_after_sfx':True,'note_off_gate':True,'fault':0,'underrun':0,'region':'pal' if args.pal else 'ntsc'}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:host.close()
if __name__=='__main__':main()
