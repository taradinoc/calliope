"""Trace quarter-step PSG volume slides without period or ownership changes."""
import argparse,ctypes as C,json
from pathlib import Path
from unittest.mock import patch
from libretro_host import Host
from calliope.furnace import parse,Cell
from calliope.compiler import compile_bank
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pal',action='store_true');parser.add_argument('--music',action='store_true');parser.add_argument('--noise',action='store_true');args=parser.parse_args()
    out=ROOT/'out'/('volume-slide-'+('noise-' if args.noise else 'tone-')+('music' if args.music else 'sfx')+('-pal' if args.pal else '-ntsc'));out.mkdir(exist_ok=True)
    source=ROOT/('examples/noise232.fur' if args.noise else 'examples/psg_ring.fur');module=parse(source);song=module.subsongs[0]
    channel=3 if args.noise else 6
    song.rows=4;song.speeds=[2];song.orders=[[0] for _ in module.channels]
    module.flags['compat_legacy'][6]=0;module.flags['compat_extended'][1]=0
    module.patterns={(0,channel,0):{0:Cell(note=108,instrument=0,volume=8,effects=[(0,0x0a,4)]+([(1,0x20,1)] if args.noise else [])),3:Cell(effects=[(0,0x0a,0)])}}
    module.instruments=[{'kind':0,'features':{}}]
    entry={'id':3 if args.music else 4,'name':'slide','source':str(source)}
    if args.music:entry['voices']=[{'channel':channel,'slot':'noise' if args.noise else 'tone0'}]
    else:entry['resource_mask']=256 if args.noise else 32
    manifest=out/'bank.json';manifest.write_text(json.dumps({'version':1,'songs' if args.music else 'sfx':[entry]}))
    with patch('calliope.compiler.parse',return_value=module):data,_,_=compile_bank(manifest,'pal' if args.pal else 'ntsc')
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1');raw[base:base+len(data)]=data
    rom=out/'sfx-macros.bin';rom.write_bytes(raw)
    host=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,
              {b'genesis_plus_gx_region_detect':b'pal' if args.pal else b'ntsc'})
    host.core.retro_set_controller_port_device(0,513)
    periods=[];latch=0;period=0;levels=[]
    def record(cycles,reg,value,pc):
        nonlocal latch,period
        if reg!=0x200:return
        if value&128:
            latch=(value>>4)&7
            if latch==0:period=(period&1008)|(value&15)
            elif latch==(7 if args.noise else 1):levels.append(value&15)
        elif latch==0:
            period=(period&15)|((value&63)<<4);periods.append(period)
    callback=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record)
    host.core.clp_chip_trace_set.argtypes=[type(callback)]
    try:
        host.run(100);host.core.clp_chip_trace_set(callback);host.run(1,(10 if args.music else 6,));host.run(20)
        expected=[] if args.noise else [106 if args.pal else 107]
        assert periods==expected,(periods,expected)
        assert [8,9,10,11,12,13,15]==[v for i,v in enumerate(levels) if (i==0 or levels[i-1]!=v) and not (i==0 and v==15)],levels
        state=host.save();needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50];where=state.find(needle)-0x38;assert where>=0
        ram=state[where:where+8192];assert not ram[0x1f28 if args.noise else 0x1f25] and not ram[0x1d05] and not ram[0x1d1a]
        assert ram[0x1738:0x1740]==bytes([165]*8) and ram[0x1f78:0x1f80]==bytes([165]*8)
        report={'periods':periods,'volume_slide':True,'noise':args.noise,'music':args.music,'ownership_released':True,'fault':0,'underrun':0,'region':'pal' if args.pal else 'ntsc'}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:host.close()
if __name__=='__main__':main()
