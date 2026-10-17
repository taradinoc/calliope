"""Rapid PCM requests during mixed Elevator playback."""
import os
import argparse
import json
from pathlib import Path
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--core',default=os.environ.get('CALLIOPE_CORE','genesis_plus_gx'));p.add_argument('--pal',action='store_true');p.add_argument('--period',type=int,default=4);p.add_argument('--frames',type=int,default=6000);a=p.parse_args()
    if a.period<2 or a.frames<1: p.error('period must be at least 2; frames must be positive')
    out=ROOT/'out'/('retrigger-pal' if a.pal else 'retrigger-ntsc');out.mkdir(exist_ok=True)
    h=Host(a.core,ROOT/'audition/out/rom.bin',out,{b'genesis_plus_gx_region_detect':b'pal' if a.pal else b'ntsc'})
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    def ram():
        s=h.save();o=s.find(needle)-0x38;assert o>=0;return s[o:o+8192]
    records=[]
    try:
        h.run(100);h.run(1,(4,));h.run(1)
        for frame in range(a.frames):
            h.run(1,(7,) if frame%a.period==0 else ())
            if frame%60==0 or frame==a.frames-1:
                r=ram();records.append({'frame':frame,'fault':r[0x1d05],'underrun':r[0x1d1a],'underrun_pc':int.from_bytes(r[0x1d1c:0x1d1e],'little'),'pending':r[0x1e80],'tick':int.from_bytes(r[0x1d0a:0x1d0e],'little'),'pcm0':r[0x1ea0:0x1ec0].hex(),'pcm1':r[0x1ec0:0x1ee0].hex(),'period':a.period,'guards':r[0x1738:0x1740].hex()+r[0x1f78:0x1f80].hex()})
                if records[-1]['fault']:break
        (out/'report.json').write_text(json.dumps(records,indent=2)+'\n');print(json.dumps(records[-1],indent=2))
        assert not records[-1]['fault'],'rapid PCM playback fault'
        assert not records[-1]['underrun'],'rapid PCM playback underrun'
        assert all(x['guards']=='a5'*16 for x in records),'stack guard'
        expected=(records[-1]['frame']+1)*(1.2 if a.pal else 1)
        assert abs(records[-1]['tick']-expected)<6,'sequencer lost time'
    finally:h.close()
if __name__=='__main__':main()
