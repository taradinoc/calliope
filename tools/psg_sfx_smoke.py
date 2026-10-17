"""Audition controls, PSG stealing, mixed SFX capacity and token cancellation."""
import os
import argparse
import json
import struct
import subprocess
import sys
import wave
from pathlib import Path
import numpy as np
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--core',default=os.environ.get('CALLIOPE_CORE','genesis_plus_gx'));parser.add_argument('--pal',action='store_true');parser.add_argument('--probe',action='store_true',help=argparse.SUPPRESS);args=parser.parse_args()
    out=ROOT/'out'/('psg-sfx-pal' if args.pal else 'psg-sfx-ntsc');out.mkdir(exist_ok=True)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1')
    table=base+int.from_bytes(raw[base+13:base+16],'little')
    descriptors={int.from_bytes(raw[p:p+2],'little'):p for p in range(table,table+raw[base+7]*16,16)}
    # Force PSG tone 0 to prove stealing, even though Elevator leaves tone 2 free.
    raw[descriptors[4]+7]=32
    rom=out/'test.bin';rom.write_bytes(raw)
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    if not args.probe:
        subprocess.run([sys.executable,__file__,'--probe']+(['--pal'] if args.pal else []),check=True)
    h=Host(args.core,ROOT/'audition/out/rom.bin' if args.probe else rom,out,
           {b'genesis_plus_gx_region_detect':b'pal' if args.pal else b'ntsc'})
    chunks=[];h.audio_sink=chunks.append
    def snapshot():
        s=bytearray(h.save());o=s.find(needle)-0x38;assert o>=0
        return s,o
    def ram():
        s,o=snapshot();return s[o:o+8192]
    def commands(entries):
        s,o=snapshot();w=s[o+0x1d01]
        for op,token,arg in entries:
            if op in (2,7,8,10,11):token=int.from_bytes(s[o+0x1d08:o+0x1d0a],'little')
            p=o+0x1d40+(w&15)*8;s[p:p+8]=struct.pack('<BBHI',op,0,token,arg);w=(w+1)&255
        s[o+0x1d01]=w;h.load(bytes(s));h.run(1)
        for _ in range(12):
            if ram()[0x1d01]==ram()[0x1d02]: break
            h.run(1)
        h.run(2)
    def play(id,priority,token):return (9,token,(descriptors[id]-base)|(priority<<24))
    def tokens():
        r=ram();return {int.from_bytes(r[p+4:p+6],'little'):r[p+1] for p in range(0x1800,0x1840,16) if r[p]}
    def capture(name,frames):
        chunks.clear();h.run(frames);audio=b''.join(chunks)
        with wave.open(str(out/(name+'.wav')),'wb') as w:
            w.setnchannels(2);w.setsampwidth(2);w.setframerate(round(h.sample_rate));w.writeframes(audio)
        samples=np.frombuffer(audio,dtype='<i2').astype('f8')
        return float(np.sqrt(np.mean(samples*samples)))
    try:
        if args.probe:
            h.run(100);h.run(2,(4,));h.run(80);h.run(1,(6,));h.run(3)
            assert tokens().get(4)==7,'prefer unused tone 2 over Elevator music tones'
            return
        h.run(100);commands([(3,0,0)]);h.run(30)
        h.run(1,(6,));ring=capture('psg_ring',20);assert ring>100,ring
        assert tokens().get(4)==5,'LEFT must start a PSG effect on its eligible tone'
        h.run(60);assert not tokens(),'PSG terminal must release ownership'
        h.run(1,(7,));chu=capture('chu',10);assert chu>100,chu
        assert ram()[0x1ec0]!=0,'RIGHT must start PCM SFX'
        h.run(100);assert ram()[0x1ec0]==0,'finite PCM must complete'
        h.run(2,(4,));h.run(80)
        commands([play(4,2,40)]);assert tokens().get(40)==5
        assert ram()[0x1f25]!=0 and ram()[0x1f35]==1,'PSG music resource must be stolen'
        commands([play(4,2,41)]);assert 40 in tokens() and 41 not in tokens()
        commands([play(4,3,41)]);assert 41 in tokens() and 40 not in tokens()
        commands([play(3,2,50+i) for i in range(3)]);assert len(tokens())==4,tokens()
        commands([play(3,2,60)]);assert 60 not in tokens(),'four shared instances, equal priority rejects'
        commands([(2,0,0)]);assert 41 in tokens(),'music stop must preserve PSG SFX'
        commands([(6,0,41)]);assert 41 not in tokens() and ram()[0x1f25]==0
        commands([(3,0,0)]);assert not tokens()
        h.run(2,(4,));h.run(70);commands([play(4,2,70)]);h.run(120)
        assert ram()[0x1f25]==0 and ram()[0x1f35]==0 and ram()[0x1d0e]==1,'fresh music note restores PSG after release'
        assert ram()[0x1d05]==0 and ram()[0x1d1a]==0
        assert ram()[0x1738:0x1740]==bytes([165]*8) and ram()[0x1f78:0x1f80]==bytes([165]*8)
        report={'ring_rms':ring,'chu_rms':chu,'arbitration_and_controls':'passed','stack_guards':True,'fault':0,'pcm_underrun':0}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    finally:h.close()
if __name__=='__main__':main()
