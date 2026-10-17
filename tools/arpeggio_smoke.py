"""Trace tracker arpeggios in FM/PSG music and SFX without envelope restarts."""
import argparse,ctypes as C,json
from pathlib import Path
from unittest.mock import patch
from libretro_host import Host
from calliope.furnace import parse,Cell
from calliope.compiler import compile_bank
from calliope.fm import frequency
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pal',action='store_true');parser.add_argument('--psg',action='store_true');parser.add_argument('--sfx',action='store_true');args=parser.parse_args()
    region='pal' if args.pal else 'ntsc'
    out=ROOT/'out'/('arpeggio-'+region+('-psg' if args.psg else '-fm')+('-sfx' if args.sfx else '-music'));out.mkdir(exist_ok=True)
    source=ROOT/('examples/psg_ring.fur' if args.psg else 'examples/fm_ring.fur');module=parse(source);song=module.subsongs[0]
    channel=6 if args.psg else 0
    song.rows=6;song.speeds=[3];song.arp_speed=1;song.orders=[[0] for _ in module.channels]
    module.flags['compat_legacy'][7]=0;module.flags['compat_legacy'][19]=1
    module.flags['compat_extended'][2]=0;module.flags['compat_more'][4]=0
    module.patterns={(0,channel,0):{0:Cell(note=108,instrument=0,volume=15 if args.psg else 127,effects=[(0,0,0x37),(1,0xe0,2)]),4:Cell(effects=[(0,0,0)]),5:Cell(note=180)}}
    if args.psg:module.instruments=[{'kind':0,'features':{}}]
    entry={'id':4 if args.sfx else 3,'name':'arp','source':str(source)}
    if args.sfx:entry['resource_mask']=32 if args.psg else 1
    else:entry['voices']=[{'channel':channel,'slot':'tone0' if args.psg else 'fm0'}]
    manifest=out/'bank.json';manifest.write_text(json.dumps({'version':1,'sfx' if args.sfx else 'songs':[entry]}))
    with patch('calliope.compiler.parse',return_value=module):data,_,_=compile_bank(manifest,region)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1');raw[base:base+len(data)]=data
    rom=out/'pitch.bin';rom.write_bytes(raw)
    host=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':region.encode()})
    host.core.retro_set_controller_port_device(0,513)
    writes=[]
    callback=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(lambda cycles,reg,value,pc:writes.append((reg,value)))
    host.core.clp_chip_trace_set.argtypes=[type(callback)];host.core.clp_chip_trace_set(callback)
    try:
        host.run(100);writes.clear();host.run(1,(6 if args.sfx else 10,));host.run(100)
        high=0;actual=[];latch=0;period=0
        for reg,value in writes:
            if args.psg:
                if reg!=0x200:continue
                if value&128:
                    latch=(value>>4)&7
                    if latch==0:period=(period&1008)|(value&15)
                elif latch==0:
                    period=(period&15)|((value&63)<<4);actual.append(period)
            else:
                if reg==0xa4:high=value
                if reg==0xa0:actual.append((high<<8)|value)
        notes=[108,111,115,108,111,115,108]
        clock=(53203424 if args.pal else 53693175)/15
        expected=[int(clock/(32*440*2**((n-93)/12))+.5) if args.psg else frequency(n,region) for n in notes]
        assert actual==expected,(actual,expected)
        if not args.psg:assert sum(reg==0x28 and value==0xf0 for reg,value in writes)==1,'arpeggio restarted envelope'
        needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
        state=host.save();where=state.find(needle)-0x38;assert where>=0
        ram=state[where:where+8192];assert not ram[0x1d05] and not ram[0x1d1a]
        assert ram[0x1738:0x1740]==bytes([165]*8) and ram[0x1f78:0x1f80]==bytes([165]*8)
        report={'region':region,'frequency_writes':len(actual),'psg':args.psg,'sfx':args.sfx,'envelope_preserved':True,'fault':0,'underrun':0}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:host.close()
if __name__=='__main__':main()
