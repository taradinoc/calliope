"""Trace EC note cuts and FC macro release at their authored ticks."""
import argparse,ctypes as C,json,copy
from pathlib import Path
from unittest.mock import patch
from libretro_host import Host
from calliope.furnace import parse,Cell
from calliope.compiler import compile_bank
from calliope.fm import frequency
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pal',action='store_true');parser.add_argument('--psg',action='store_true');parser.add_argument('--sfx',action='store_true');parser.add_argument('--delay',action='store_true',help='exercise FM ED note delay');args=parser.parse_args()
    if args.delay and args.psg:parser.error('--delay requires FM')
    region='pal' if args.pal else 'ntsc'
    out=ROOT/'out'/('note-timing-'+region+('-psg' if args.psg else '-fm')+('-sfx' if args.sfx else '-music'));out.mkdir(exist_ok=True)
    source=ROOT/('examples/psg_ring.fur' if args.psg else 'examples/fm_ring.fur');module=parse(source);song=module.subsongs[0]
    channel=6 if args.psg else 0
    song.rows=3;song.speeds=[6];song.arp_speed=1;song.orders=[[0] for _ in module.channels]
    module.flags['compat_legacy'][7]=0;module.flags['compat_legacy'][19]=1
    module.flags['compat_extended'][2]=0;module.flags['compat_more'][4]=0
    module.patterns={(0,channel,0):{0:Cell(note=108,instrument=0,volume=15 if args.psg else 127,effects=[(0,0xec,3)]),1:Cell(note=112,effects=[(0,0xfc,2)])}}
    if args.delay:
        module.patterns[(0,channel,0)][0].effects.append((1,0xed,2))
        module.patterns[(0,channel,0)][1].effects.append((1,0xed,1))
    if args.psg:module.instruments=[{'kind':0,'features':{'MA':bytes((8,0,0,3,0,1,0,8,0,1,15,15,0,255))}}]
    entry={'id':4 if args.sfx else 3,'name':'cuts','source':str(source)}
    if args.sfx:entry['resource_mask']=32 if args.psg else 1
    else:entry['voices']=[{'channel':channel,'slot':'tone0' if args.psg else 'fm0'}]
    content={'version':1,'sfx' if args.sfx else 'songs':[entry]}
    modules=[module]
    if args.sfx:
        clock=copy.deepcopy(module);clock.patterns={}
        content['songs']=[{'id':1,'name':'clock','source':str(source),'voices':[{'channel':channel,'slot':'tone0' if args.psg else 'fm0'}],'loop_order':0}]
        modules=[clock,module]
    manifest=out/'bank.json';manifest.write_text(json.dumps(content))
    with patch('calliope.compiler.parse',side_effect=modules):data,_,_=compile_bank(manifest,region)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1');raw[base:base+len(data)]=data
    rom=out/'pitch.bin';rom.write_bytes(raw)
    host=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':region.encode()})
    host.core.retro_set_controller_port_device(0,513)
    host.core.clp_zram.restype=C.POINTER(C.c_ubyte);zram=host.core.clp_zram()
    writes=[]
    callback=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(lambda cycles,reg,value,pc:writes.append((sum(zram[0x1d0a+i]<<(8*i) for i in range(4)),reg,value)))
    host.core.clp_chip_trace_set.argtypes=[type(callback)];host.core.clp_chip_trace_set(callback)
    try:
        host.run(100);writes.clear();host.run(1,(6 if args.sfx else 10,));host.run(100)
        if args.psg:
            changes=[(tick,value&15) for tick,reg,value in writes if reg==0x200 and value&0xf0==0x90]
            changes=[item for i,item in enumerate(changes) if i==0 or item[1]!=changes[i-1][1]]
            while changes and changes[0][1]==15:changes.pop(0)
            first=changes[0][0]
            assert [(tick-first,level) for tick,level in changes[:4]]==[(0,0),(3,15),(6,0),(9,15)],changes
        else:
            gates=[(tick,value) for tick,reg,value in writes if reg==0x28 and value in (0,0xf0)]
            while gates and gates[0][1]!=0xf0:gates.pop(0)
            first=gates[0][0]
            assert (first+3,0) in gates and (first+(7 if args.delay else 8),0) in gates,gates
            assert [tick-first for tick,value in gates if value==0xf0]==[0,5 if args.delay else 6],gates
        needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
        state=host.save();where=state.find(needle)-0x38;assert where>=0
        ram=state[where:where+8192];assert not ram[0x1d05] and not ram[0x1d1a]
        assert ram[0x1738:0x1740]==bytes([165]*8) and ram[0x1f78:0x1f80]==bytes([165]*8)
        report={'region':region,'cut_tick':3,'release_tick':7 if args.delay else 8,'delay':args.delay,'psg':args.psg,'sfx':args.sfx,'envelope_preserved':True,'fault':0,'underrun':0}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:host.close()
if __name__=='__main__':main()
