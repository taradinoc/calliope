"""Check YM stereo writes, held envelopes, SFX ownership and pan restoration."""
import argparse,ctypes as C,json,struct
from pathlib import Path
from unittest.mock import patch
from libretro_host import Host
from calliope.furnace import parse,Cell
from calliope.compiler import compile_bank
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pal',action='store_true');args=parser.parse_args()
    out=ROOT/'out'/('fm-pan-pal' if args.pal else 'fm-pan-ntsc');out.mkdir(exist_ok=True)
    source=ROOT/'examples/fm_ring.fur'
    def module():
        m=parse(source);s=m.subsongs[0];s.rows=160;s.speeds=[1];s.orders=[[0] for _ in m.channels]
        m.instruments[0]['features'].pop('MA',None)
        fm=bytearray(m.instruments[0]['features']['FM']);fm[2]=0x1d;m.instruments[0]['features']['FM']=bytes(fm)
        return m
    music=module();music.patterns={(0,0,0):{0:Cell(note=108,instrument=0,effects=[(0,0x80,0)]),
        10:Cell(effects=[(0,0x80,255)]),20:Cell(effects=[(0,0x80,128)]),
        30:Cell(effects=[(0,0x80,0)]),90:Cell(note=112,instrument=0),150:Cell(note=180)}}
    sfx=module();sfx.subsongs[0].rows=64;sfx.patterns={(0,0,0):{
        0:Cell(note=108,instrument=0,effects=[(0,0x80,255)]),5:Cell(effects=[(0,0x80,128)]),60:Cell(note=180)}}
    manifest=out/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[{'id':3,'name':'music','source':str(source),'voices':[{'name':'melody','channel':0,'slot':'fm0'}],'groups':{'all':['melody']},'arrangements':{'main':['melody']}}],'sfx':[{'id':3,'name':'ring','source':str(source),'resource_mask':1}]}))
    modules=iter((music,sfx))
    with patch('calliope.compiler.parse',side_effect=lambda _:next(modules)):data,_,_=compile_bank(manifest,'pal' if args.pal else 'ntsc')
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1');raw[base:base+len(data)]=data
    rom=out/'pan.bin';rom.write_bytes(raw)
    host=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':b'pal' if args.pal else b'ntsc'})
    host.core.retro_set_controller_port_device(0,513)
    writes=[];last_pan=None
    def record(cycles,reg,value,pc):
        nonlocal last_pan
        if reg in (0x28,0xb4):writes.append((reg,value))
        if reg==0xb4:last_pan=value
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
        host.run(100);host.run(1,(10,));host.run(4)
        assert last_pan==0xb5,hex(last_pan)
        writes.clear();host.run(20)
        assert (0xb4,0x75) in writes and (0xb4,0xf5) in writes,writes
        assert not any(r==0x28 for r,v in writes),'held pan restarted envelope'
        descriptor=int.from_bytes(data[13:16],'little');command(9,0,descriptor|(2<<24),91)
        assert ram()[0x1f20] and last_pan==0x75,'SFX did not apply right pan'
        writes.clear();host.run(10)
        assert (0xb4,0xf5) in writes and not any(r==0x28 for r,v in writes),'SFX pan restarted envelope'
        assert ram()[0x1b0e]==0xb5,'stolen logical music pan did not advance'
        command(6,0,91);writes.clear();host.run(5)
        assert ram()[0x1f30] and not writes,'pan acquired a stolen voice'
        writes.clear();host.run(60)
        assert (0x28,0xf0) in writes and last_pan==0xb5,'fresh note lost persistent pan'
        r=ram();assert not r[0x1d05] and not r[0x1d1a]
        assert r[0x1738:0x1740]==bytes([165]*8) and r[0x1f78:0x1f80]==bytes([165]*8)
        report={'held_envelope':True,'sfx_pan':True,'stolen_shadow':True,'persistent_pan':True,'ams_fms':True,'fault':0,'underrun':0,'region':'pal' if args.pal else 'ntsc'}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:host.close()
if __name__=='__main__':main()
