"""Check music Hz independently of the fixed SFX tick clock in assembled code."""
import os
import argparse
import json
from pathlib import Path
import struct
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--core',default=os.environ.get('CALLIOPE_CORE','genesis_plus_gx'));p.add_argument('--rate',type=float,default=120);p.add_argument('--pal',action='store_true');a=p.parse_args()
    out=ROOT/'out'/f'timing-{a.rate}-{"pal" if a.pal else "ntsc"}';out.mkdir(exist_ok=True)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1')
    song=base+int.from_bytes(raw[base+10:base+13],'little');raw[song+3]=0;raw[song+14:song+16]=round(a.rate*5).to_bytes(2,'little')
    table=base+int.from_bytes(raw[base+13:base+16],'little');desc=next(p for p in range(table,table+raw[base+7]*16,16) if int.from_bytes(raw[p:p+2],'little')==3)
    pos=base+int.from_bytes(raw[desc+4:desc+7],'little');duration=0
    while raw[pos]!=255:
        op=raw[pos];pos+=1
        if op==0:duration+=raw[pos];pos+=1
        elif op==0x10:pos+=2
        elif op in (0x11,0x36):pos+=3
        elif op==0x37:pos+=6
        elif op==0x13:pos+=1
        else:raise AssertionError('unexpected FM fixture opcode')
    rom=out/'timing.bin';rom.write_bytes(raw)
    h=Host(a.core,rom,out,{b'genesis_plus_gx_region_detect':b'pal' if a.pal else b'ntsc'})
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    def snap():
        s=bytearray(h.save());o=s.find(needle)-0x38;assert o>=0;return s,o
    def ram():
        s,o=snap();return s[o:o+8192]
    def tick():return int.from_bytes(ram()[0x1d0a:0x1d0e],'little')
    try:
        h.run(100);before=tick();h.run(60);delta=tick()-before
        expected=a.rate*(1.2 if a.pal else 1);assert abs(delta-expected)<=2,(delta,expected)
        s,o=snap();w=s[o+0x1d01];at=o+0x1d40+(w&15)*8
        s[at:at+8]=struct.pack('<BBHI',9,0,99,(desc-base)|(2<<24));s[o+0x1d01]=(w+1)&255;h.load(bytes(s))
        h.run(1);assert any(ram()[0x1f20:0x1f25]);frames=0
        while any(ram()[0x1f20:0x1f25]) and frames<200:h.run(1);frames+=1
        wanted=duration/(1.2 if a.pal else 1);assert abs(frames-wanted)<=3,(frames,wanted)
        r=ram();assert r[0x1d05]==0 and r[0x1d1a]==0
        report={'rate':a.rate,'music_tick_delta':delta,'sfx_frames':frames,'expected_sfx_frames':wanted,'fault':0,'pcm_underrun':0}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    finally:h.close()
if __name__=='__main__':main()
