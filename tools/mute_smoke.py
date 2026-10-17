"""Check physical music muting against assembled code and audible output."""
import os
import argparse
import json
import struct
from pathlib import Path
import numpy as np
from libretro_host import Host
from audition_fixture import hide_song
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--core',default=os.environ.get('CALLIOPE_CORE','genesis_plus_gx'));a=p.parse_args()
    out=ROOT/'out/mute';out.mkdir(exist_ok=True)
    raw=hide_song(bytearray((ROOT/'audition/out/rom.bin').read_bytes()),5);rom=out/'mute.bin';rom.write_bytes(raw);base=raw.index(b'BGM1')
    table=base+int.from_bytes(raw[base+13:base+16],'little')
    desc=next(p for p in range(table,table+raw[base+7]*16,16) if raw[p+2]==2)-base
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    h=Host(a.core,rom,out)
    chunks=[];h.audio_sink=chunks.append
    def snapshot():
        s=bytearray(h.save());o=s.find(needle)-0x38;assert o>=0
        return s,o
    def ram():
        s,o=snapshot();return s[o:o+8192]
    def command(op,arg,token=0):
        s,o=snapshot();w=s[o+0x1d01];p=o+0x1d40+(w&15)*8
        token=int.from_bytes(s[o+0x1d08:o+0x1d0a],'little') if op in (2,7,8,10,11) else token
        s[p:p+8]=struct.pack('<BBHI',op,0,token,arg);s[o+0x1d01]=(w+1)&255
        h.load(bytes(s));h.run(4)
    def rms(frames):
        chunks.clear();h.run(frames)
        a=np.frombuffer(b''.join(chunks),dtype='<i2').astype('f8')
        return float(np.sqrt(np.mean(a*a)))
    try:
        h.run(100);h.run(2,(4,));h.run(100)
        command(10,0x2ff);h.run(60)
        tick=int.from_bytes(ram()[0x1d0a:0x1d0e],'little')
        quiet=rms(60);assert quiet<5,quiet
        assert int.from_bytes(ram()[0x1d0a:0x1d0e],'little')>tick
        command(9,desc|(2<<24),55);effect=rms(12);assert effect>100,effect
        h.run(60);assert rms(30)<5,'music must stay muted after SFX'
        command(10,0);restored=rms(120);assert restored>100,restored
        assert ram()[0x1d05]==0 and ram()[0x1d1a]==0
        report={'muted_rms':quiet,'sfx_rms':effect,'restored_rms':restored,'fault':0,'pcm_underrun':0}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    finally:h.close()
if __name__=='__main__':main()
