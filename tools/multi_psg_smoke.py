"""Trace multi-channel PSG playback in both modes and Z80 atomic ownership."""
import argparse
import ctypes as C
import json
from pathlib import Path
import struct
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pal',action='store_true');parser.add_argument('--hybrid',action='store_true');a=parser.parse_args()
    out=ROOT/'out'/('multi-psg-'+('hybrid-' if a.hybrid else '')+('pal' if a.pal else 'ntsc'));out.mkdir(exist_ok=True)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1')
    table=base+int.from_bytes(raw[base+13:base+16],'little')
    desc={int.from_bytes(raw[p:p+2],'little'):p for p in range(table,table+raw[base+7]*16,16)}
    raw[desc[4]+7]=128  # Last resource in the set, for late rejection/preemption.
    # Reuse the looping descriptor to test an actual four-channel loop.
    raw[desc[8]+2:desc[8]+16]=raw[desc[9]+2:desc[9]+16]
    raw[desc[8]+8:desc[8]+11]=raw[desc[9]+4:desc[9]+7]
    stream=base+int.from_bytes(raw[desc[9]+4:desc[9]+7],'little')
    expected=[];p=stream
    while raw[p]!=255:
        op=raw[p];p+=1
        if op==0:p+=1
        else:
            assert op==0x30
            n=raw[p];p+=1;expected.extend(raw[p:p+n]);p+=n
    rom=out/'test.bin';rom.write_bytes(raw)
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,
        {b'genesis_plus_gx_region_detect':b'pal' if a.pal else b'ntsc'})
    h.core.retro_set_controller_port_device(0,513)
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50];writes=[]
    def record(cycles,reg,value,pc):
        if reg==0x200:writes.append(value)
    cb=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record)
    h.core.clp_chip_trace_set.argtypes=[type(cb)];h.core.clp_chip_trace_set(cb)
    def snapshot():
        s=bytearray(h.save());o=s.find(needle)-0x38;assert o>=0;return s,o
    def ram():
        s,o=snapshot();return s[o:o+8192]
    def command(op,arg=0,token=None):
        s,o=snapshot();w=s[o+0x1d01];p=o+0x1d40+(w&15)*8
        if token is None:token=int.from_bytes(s[o+0x1d08:o+0x1d0a],'little')
        s[p:p+8]=struct.pack('<BBHI',op,0,token,arg);s[o+0x1d01]=(w+1)&255;h.load(bytes(s));h.run(3)
        assert ram()[0x1d01]==ram()[0x1d02],'command queue did not drain'
    def play(id,priority,token):command(9,(desc[id]-base)|(priority<<24),token)
    def owners():return bytes(ram()[0x1f25:0x1f29])
    def tokens():
        r=ram();return {int.from_bytes(r[p+4:p+6],'little') for p in range(0x1800,0x1840,16) if r[p]}
    def all_owned():
        state=owners();assert state[0] and len(set(state))==1,state
    try:
        if a.hybrid:
            h.run(100);h.run(1,(0,));h.run(30);writes.clear()
            h.run(1,(2,5));h.run(200)
            # Hybrid suppresses redundant attenuation writes, retaining pitch
            # and noise controls in source order. All channels begin muted.
            volumes=[15]*4;filtered=[]
            for value in expected:
                if value&0x90==0x90:
                    channel=(value>>5)&3;level=value&15
                    if volumes[channel]==level:continue
                    volumes[channel]=level
                filtered.append(value)
            start=next(i for i,value in enumerate(writes) if value&0xf0==0x80)
            assert writes[start:]==filtered,(writes[start:],filtered)
            r=ram();assert r[0x1d05]==0 and r[0x1d1a]==0
            assert r[0xff8:0x1000]==bytes([165]*8) and r[0x1f78:0x1f80]==bytes([165]*8)
            report={'region':'pal' if a.pal else 'ntsc','hybrid':True,'authored_psg_bytes':len(filtered),
                'audition_shortcut':True,'fault':0,'underrun':0,'guards':True}
            (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
            return
        h.run(100);command(3);h.run(20)
        writes.clear();play(9,2,90);all_owned();assert tokens()=={90}
        h.run(180);assert owners()==bytes(4) and not tokens()
        start=next(i for i,value in enumerate(writes) if value&0xf0==0x80)
        assert writes[start:start+len(expected)]==expected,'authored PSG writes changed'
        assert writes[start+len(expected):]==[0x9f,0xbf,0xdf,0xff],'completion did not mute all resources'
        # Two independent victims; a rejection at tone 3 must preserve both.
        play(6,1,60);play(4,4,40);before=owners();assert before[2] and before[3]
        play(9,3,91);assert owners()==before and tokens()=={60,40}
        play(9,5,92);all_owned();assert tokens()=={92}
        play(4,5,41);all_owned();assert tokens()=={92}
        play(4,6,42);assert owners()[:2]==bytes(2) and owners()[2] and not owners()[3]
        assert tokens()=={42};command(6,42);assert not any(owners())
        play(9,2,93);command(2);all_owned();command(6,93);assert not any(owners())
        # Unrelated FM instances occupy capacity even when PSG channels are free.
        for token in range(30,34):play(3,2,token)
        assert len(tokens())==4;play(9,3,94);assert not any(owners()) and 94 not in tokens()
        command(3);assert not tokens()
        play(8,2,80);h.run(180);all_owned();assert tokens()=={80}
        command(6,80);assert not any(owners()) and not tokens()
        play(8,2,81);h.run(180);all_owned()
        play(9,3,95);all_owned();assert tokens()=={95}
        command(3);assert not any(owners()) and not tokens()
        # The public audition shortcut exercises adapter acceptance of kind 6.
        h.run(1,(2,5));h.run(3);all_owned();assert tokens()=={9}
        command(3)
        r=ram();assert r[0x1d05]==0 and r[0x1d1a]==0
        assert r[0x1738:0x1740]==bytes([165]*8) and r[0x1f78:0x1f80]==bytes([165]*8)
        report={'region':'pal' if a.pal else 'ntsc','authored_psg_bytes':len(expected),
            'atomic_allocation':True,'whole_effect_preemption':True,'token_stop':True,
            'capacity_rejection':True,'persistent_loop':True,'audition_shortcut':True,'fault':0,'underrun':0,'guards':True}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:h.close()


if __name__=='__main__':main()
