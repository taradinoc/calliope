"""Saturate the real cue producer/ring while the 68000 consumes bounded batches."""
import os
import argparse
import json
from pathlib import Path
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--core',default=os.environ.get('CALLIOPE_CORE','genesis_plus_gx'));parser.add_argument('--pal',action='store_true');parser.add_argument('--dma',action='store_true');parser.add_argument('--offset',type=int,choices=range(5),default=0);args=parser.parse_args()
    out=ROOT/'out'/('events-pal' if args.pal else 'events-ntsc');out.mkdir(exist_ok=True)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1')
    songs=base+int.from_bytes(raw[base+10:base+13],'little')
    song=next(songs+i*24 for i in range(raw[base+6]) if int.from_bytes(raw[songs+i*24:songs+i*24+2],'little')==5)
    table=base+int.from_bytes(raw[song+4:song+7],'little')
    # Append a fixture so growing song banks cannot occupy its padding.
    # Cue operands straddle both a resident page and cartridge-bank boundary.
    stream=((len(raw)+32767)//32768)*32768+0x7ff0+args.offset
    payload=b''.join(bytes((0x20,i,0,42,0)) for i in range(9))+bytes((0,2,0xfd))
    size=((stream+64+131071)//131072)*131072
    raw.extend(bytes(size-len(raw)))
    raw[stream:stream+len(payload)]=payload
    raw[table:table+3]=(stream-base).to_bytes(3,'little');raw[song+2]=0;raw[song+3]=0
    raw[song+14:song+16]=(300).to_bytes(2,'little') # fixture uses a fixed 60 Hz clock
    raw[0x1a4:0x1a8]=(len(raw)-1).to_bytes(4,'big')
    raw[0x18e:0x190]=(sum(int.from_bytes(raw[i:i+2],'big') for i in range(512,len(raw),2))&65535).to_bytes(2,'big')
    rom=out/'events.bin';rom.write_bytes(raw)
    h=Host(args.core,rom,out,
           {b'genesis_plus_gx_region_detect':b'pal' if args.pal else b'ntsc'})
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    def snapshot():
        state=bytearray(h.save());offset=state.find(needle)-0x38;assert offset>=0
        return state,offset
    def ram():
        state,offset=snapshot();return state[offset:offset+8192]
    try:
        h.run(120,(3,) if args.dma else ());before=int.from_bytes(ram()[0x1d0a:0x1d0e],'little');h.run(120,(3,) if args.dma else ());r=ram();lost=int.from_bytes(r[0x1d18:0x1d1a],'little')
        assert lost>0,'cue workload did not fill FIFO'
        for at in range(0x1dc0,0x1e80,12):
            record=r[at:at+12]
            assert record[:2]==bytes((1,0)) and int.from_bytes(record[2:4],'little')<9,record.hex()
            assert record[4:8]==bytes((42,0,1,0)),'cue parameter/generation corrupted: '+record.hex()
        assert r[0x1d05]==0 and r[0x1d0e]==1,('cue overflow must not fault/stop music',r[0x1d05],r[0x1d0e],r[0x1d1a],int.from_bytes(r[0x1d0a:0x1d0e],'little'))
        tick=int.from_bytes(r[0x1d0a:0x1d0e],'little');assert tick-before>=(143 if args.pal else 119),('tick progress',before,tick)
        state,o=snapshot();state[o+0x1d18:o+0x1d1a]=bytes((255,255));h.load(bytes(state));h.run(8,(3,) if args.dma else ())
        assert ram()[0x1d18:0x1d1a]==bytes((255,255)),'loss counter must saturate'
        state,o=snapshot();state[o+0x1e8a]=255;h.load(bytes(state));h.run(8,(3,) if args.dma else ());r=ram()
        assert r[0x1d16]==1 and r[0x1d14:0x1d16]==bytes((1,0)),'completion must survive cue loss'
        assert r[0x1d05]==0 and r[0x1d1a]==0,'fault or PCM underrun'
        report={'dma':args.dma,'offset':args.offset,'events_lost':lost,'ticks_before_stop':tick,'loss_saturated':True,'sticky_completion':True,'fault':0,'pcm_underrun':0}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    finally:h.close()
if __name__=='__main__':main()
