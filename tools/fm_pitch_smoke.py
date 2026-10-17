"""Register-level FM slides, portamento, legato and macro-release regression."""
import argparse,ctypes as C,json
from pathlib import Path
from unittest.mock import patch
from libretro_host import Host
from calliope.furnace import parse,Cell
from calliope.compiler import compile_bank
from calliope.fm import frequency
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pal',action='store_true');args=parser.parse_args()
    region='pal' if args.pal else 'ntsc'
    out=ROOT/'out'/('fm-pitch-'+region);out.mkdir(exist_ok=True)
    source=ROOT/'examples/fm_ring.fur';module=parse(source);song=module.subsongs[0]
    song.rows=36;song.speeds=[2];song.orders=[[0] for _ in module.channels]
    module.flags['compat_legacy'][1]=2;module.flags['compat_extended'][4]=0
    module.flags['compat_legacy'][9]=1;module.flags['compat_more'][0]=0
    module.flags['compat_extended'][1]=0;module.flags['compat_extended'][13]=4
    module.patterns={(0,0,0):{
        0:Cell(note=108,instrument=0,volume=127),4:Cell(effects=[(0,1,16)]),
        8:Cell(note=110,effects=[(0,3,16)]),16:Cell(note=112,effects=[(0,0xea,1)]),
        20:Cell(note=114),24:Cell(note=182),28:Cell(note=116,effects=[(0,0xea,0)]),
        32:Cell(note=180),35:Cell(effects=[(0,0xff,0)])}}
    manifest=out/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[{'id':3,'name':'pitch','source':str(source),'voices':[{'name':'melody','channel':0,'slot':'fm0'}],'groups':{'all':['melody']},'arrangements':{'main':['melody']}}]}))
    with patch('calliope.compiler.parse',return_value=module):data,_,_=compile_bank(manifest,region)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1');raw[base:base+len(data)]=data
    rom=out/'pitch.bin';rom.write_bytes(raw)
    host=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':region.encode()})
    host.core.retro_set_controller_port_device(0,513)
    writes=[]
    callback=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(lambda cycles,reg,value,pc:writes.append((reg,value)))
    host.core.clp_chip_trace_set.argtypes=[type(callback)];host.core.clp_chip_trace_set(callback)
    try:
        host.run(100);writes.clear();host.run(1,(10,));host.run(100)
        high=0;actual=[]
        for reg,value in writes:
            if reg==0xa4:high=value
            if reg==0xa0:actual.append((high<<8)|value)
        expected=[frequency(n,region) for n in [108]+[108+.5*i for i in range(1,9)]+[111.5,111,110.5,110,112,114,116]]
        assert actual==expected,(actual,expected)
        assert sum(reg==0x28 and value==0xf0 for reg,value in writes)==2,'porta/legato/release restarted envelope'
        needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
        state=host.save();where=state.find(needle)-0x38;assert where>=0
        ram=state[where:where+8192];assert not ram[0x1d05] and not ram[0x1d1a]
        assert ram[0x1738:0x1740]==bytes([165]*8) and ram[0x1f78:0x1f80]==bytes([165]*8)
        report={'region':region,'frequency_writes':len(actual),'fresh_key_ons':2,'fault':0,'underrun':0}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:host.close()
if __name__=='__main__':main()
