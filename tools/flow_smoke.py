"""Compare compiled Furnace jumps/cues with the assembled sequencer, including ring wrap."""
import os
import argparse,json
from pathlib import Path
from unittest.mock import patch
from libretro_host import Host
from calliope.furnace import parse,Cell
from calliope.compiler import compile_bank
from calliope.reference import Player
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--core',default=os.environ.get('CALLIOPE_CORE','genesis_plus_gx'));parser.add_argument('--pal',action='store_true');args=parser.parse_args()
    out=ROOT/'out'/('flow-pal' if args.pal else 'flow-ntsc');out.mkdir(exist_ok=True)
    source=ROOT/'examples/fm_ring.fur';module=parse(source);song=module.subsongs[0]
    song.rows=4;song.orders=[[0,1,2] for _ in module.channels];song.speeds=[3]
    module.flags['compat_extended'][23]=0
    module.patterns={(0,0,0):{0:Cell(effects=[(0,0xee,0xa1)]),1:Cell(effects=[(0,0x0b,2),(1,0x0d,1)])},
                     (0,0,2):{1:Cell(effects=[(0,0xee,0xb1)]),3:Cell(effects=[(0,0x0b,2),(1,0x0d,1)])}}
    manifest=out/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'flow','source':str(source),'voices':[{'channel':0,'slot':'fm0'}],'external_commands':'passthrough'}]}))
    with patch('calliope.compiler.parse',return_value=module):data,_,_=compile_bank(manifest,'pal' if args.pal else 'ntsc')
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1');raw[base:base+len(data)]=data
    rom=out/'flow.bin';rom.write_bytes(raw)
    host=Host(args.core,rom,out,
              {b'genesis_plus_gx_region_detect':b'pal' if args.pal else b'ntsc'})
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50];read=0;events=[]
    try:
        for frame in range(2600):
            host.run(1);state=host.save();where=state.find(needle)-0x38
            if where<0:continue
            ram=state[where:where+8192]
            if ram[0x1d00]!=128:continue
            write=ram[0x1d03];count=(write-read)&255
            assert count<=16,'test lost a ring publication'
            for index in range(count):
                at=0x1dc0+((read+index)&15)*12;event=ram[at:at+12]
                assert event[0]==1 and event[6:8]==bytes((1,0))
                events.append((int.from_bytes(event[8:12],'little'),int.from_bytes(event[2:4],'little'),int.from_bytes(event[4:6],'little')))
            read=write
            assert not ram[0x1d05] and not ram[0x1d1a], 'driver fault/underrun'
        assert len(events)>256,'counter did not wrap'
        reference=Player(data);reference.start(1);expected=[]
        for _ in range(events[-1][0]):
            reference.step();expected.extend((tick,eid,param) for tick,_,eid,param in reference.events);reference.events.clear()
        assert events==expected,(events[:4],expected[:4])
        assert ram[0x1738:0x1740]==bytes([165]*8) and ram[0x1f78:0x1f80]==bytes([165]*8)
        report={'cues':len(events),'source_route_and_loop':True,'counter_wrap':True,'reference_match':True,'fault':0,'underrun':0,'region':'pal' if args.pal else 'ntsc'}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:host.close()
if __name__=='__main__':main()
