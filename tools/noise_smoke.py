"""Exercise atomic noise/pair allocation, preemption, music-stop isolation and end."""
import os
import argparse
import json
from pathlib import Path
import struct
import numpy as np
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--core',default=os.environ.get('CALLIOPE_CORE','genesis_plus_gx'));p.add_argument('--pal',action='store_true');a=p.parse_args()
    out=ROOT/'out'/('noise-pal' if a.pal else 'noise-ntsc');out.mkdir(exist_ok=True)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1')
    table=base+int.from_bytes(raw[base+13:base+16],'little')
    desc={int.from_bytes(raw[p:p+2],'little'):p for p in range(table,table+raw[base+7]*16,16)}
    songs=base+int.from_bytes(raw[base+10:base+13],'little')
    song=next(p for p in range(songs,songs+raw[base+6]*24,24) if int.from_bytes(raw[p:p+2],'little')==4)
    raw[desc[4]+7]=128  # Force the existing tone SFX onto tone 3.
    rom=out/'noise.bin';rom.write_bytes(raw)
    h=Host(a.core,rom,out,{b'genesis_plus_gx_region_detect':b'pal' if a.pal else b'ntsc'})
    chunks=[];h.audio_sink=chunks.append
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    def snapshot():
        s=bytearray(h.save());o=s.find(needle)-0x38;assert o>=0;return s,o
    def ram():
        s,o=snapshot();return s[o:o+8192]
    def command(op,arg=0,token=None):
        s,o=snapshot();w=s[o+0x1d01];pos=o+0x1d40+(w&15)*8
        if token is None:token=int.from_bytes(s[o+0x1d08:o+0x1d0a],'little')
        s[pos:pos+8]=struct.pack('<BBHI',op,0,token,arg);s[o+0x1d01]=(w+1)&255;h.load(bytes(s));h.run(2)
    def play(id,priority,token):command(9,(desc[id]-base)|(priority<<24),token)
    def owner(slot):return ram()[0x1f20+slot]
    try:
        h.run(100);command(3)
        play(6,2,60);assert owner(8) and not owner(7)
        chunks.clear();h.run(5);noise_rms=float(np.std(np.frombuffer(b''.join(chunks),dtype='<i2')));assert noise_rms>100,'noise is silent'
        original=owner(8);command(2);assert owner(8)==original,'music stop must preserve noise SFX'
        play(4,5,40);tone=owner(7);assert tone and tone!=original
        play(7,3,70);assert owner(8)==original and owner(7)==tone,'pair rejection must not release either owner'
        play(7,6,71);assert owner(8)==owner(7)!=0,'pair must acquire both atomically'
        play(4,6,41);assert owner(8)==owner(7)!=0,'equal priority cannot preempt pair'
        play(4,7,42);assert owner(7) and not owner(8),'stealing tone must release the entire pair'
        command(6,42);assert not owner(7) and not owner(8)
        play(7,2,72);command(6,72);assert not owner(7) and not owner(8),'token stop releases pair'
        play(7,2,73);chunks.clear();h.run(6);pair_rms=float(np.std(np.frombuffer(b''.join(chunks),dtype='<i2')));assert pair_rms>100,'pair is silent'
        h.run(140);assert not owner(7) and not owner(8),'natural completion releases pair'
        play(8,2,82);h.run(150);assert owner(7)==owner(8)!=0,'explicit loop must persist'
        command(6,82);assert not owner(7) and not owner(8),'token stop must terminate a loop'
        command(1,song-base,80);h.run(4);assert ram()[0x1f4e] in (0xe6,0xe5,0xe4)
        play(6,2,81);h.run(150);assert not owner(8) and ram()[0x1f38]==0,'noise music must restore at a fresh note'
        command(10,256);assert ram()[0x1d0e]==1,'noise mute must not stop sequencing'
        r=ram();assert r[0x1d05]==0 and r[0x1d1a]==0
        assert r[0x1738:0x1740]==bytes([165]*8) and r[0x1f78:0x1f80]==bytes([165]*8)
        report={'noise_rms':noise_rms,'pair_rms':pair_rms,'atomic_pair_checks':7,'noise_music_stop_isolation':True,'fault':0,'pcm_underrun':0,'guards':True}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    finally:h.close()
if __name__=='__main__':main()
