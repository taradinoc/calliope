"""Trace relocatable PSG groups using a compiled consumer or audition ROM."""
import argparse
import ctypes as C
import json
from pathlib import Path
import struct
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--rom',type=Path,default=ROOT/'audition/out/rom.bin')
    ap.add_argument('--driver',type=Path,default=ROOT/'out/driver.bin');ap.add_argument('--pal',action='store_true')
    ap.add_argument('--consumer-title',action='store_true',help='hold C through the Bonk consumer logo and open its sound-test menu')
    a=ap.parse_args();out=ROOT/'out'/('tone-group-'+('pal' if a.pal else 'ntsc'));out.mkdir(exist_ok=True)
    raw=bytearray(a.rom.read_bytes());base=raw.index(b'BGM1');table=base+int.from_bytes(raw[base+13:base+16],'little')
    entries=list(range(table,table+raw[base+7]*16,16));single,pair,triple,loop=entries[:4]
    pcm=next(p for p in entries if raw[p+2]==1)
    storage=base+int.from_bytes(raw[pcm+4:pcm+7],'little');assert int.from_bytes(raw[pcm+7:pcm+10],'little')>=1024
    streams=[bytes((0x30,3,0x80,0x12,0x90,0,255,255)),
             bytes((0x30,6,0x81,0x12,0x90,0xa2,0x23,0xb3,0,4,255)),
             bytes((0x30,9,0x81,0x12,0x90,0xa2,0x23,0xb3,0xc3,0x34,0xd6,0,4,255))]
    # Deliberately cross a sequence-page boundary with every group packet.
    locations=[storage,(storage+256)&~255,((storage+512)&~255)-4]
    for descriptor,kind,count,stream,location in zip((single,pair,triple),(3,7,7),(224,2,3),streams,locations):
        raw[descriptor+2]=kind;raw[descriptor+7]=count;raw[descriptor+4:descriptor+7]=(location-base).to_bytes(3,'little')
        raw[descriptor+8:descriptor+11]=bytes(3);raw[location:location+len(stream)]=stream
    raw[loop+2:loop+11]=raw[pair+2:pair+11];raw[loop+8:loop+11]=raw[pair+4:pair+7]
    # Keep synthetic PCM away from the group stream scratch and loop it.
    raw[pcm+4:pcm+7]=(storage+768-base).to_bytes(3,'little')
    raw[pcm+7:pcm+10]=(256).to_bytes(3,'little')
    raw[pcm+10:pcm+13]=bytes(3);raw[pcm+13:pcm+16]=(256).to_bytes(3,'little')
    raw[storage+768:storage+1024]=bytes([160])*256
    rom=out/'test.bin';rom.write_bytes(raw)
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':b'pal' if a.pal else b'ntsc'})
    needle=a.driver.read_bytes()[0x38:0x50];writes=[]
    def record(cycles,reg,value,pc):
        if reg==0x200:writes.append(value)
    cb=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record)
    h.core.clp_chip_trace_set.argtypes=[type(cb)];h.core.clp_chip_trace_set(cb)
    def snapshot():
        saved=bytearray(h.save());offset=saved.find(needle)-0x38;assert offset>=0;return saved,offset
    def ram():
        saved,offset=snapshot();return saved[offset:offset+8192]
    def command(op,arg=0,token=0,frames=3):
        saved,offset=snapshot();w=saved[offset+0x1d01];p=offset+0x1d40+(w&15)*8
        saved[p:p+8]=struct.pack('<BBHI',op,0,token,arg);saved[offset+0x1d01]=(w+1)&255;h.load(bytes(saved));h.run(frames)
        if frames:assert ram()[0x1d01]==ram()[0x1d02]
    def play(descriptor,priority,token):
        command(9,(descriptor-base)|(priority<<24),token)
    def effects():
        r=ram();return {int.from_bytes(r[p+4:p+6],'little'):(r[p+1],r[p+14]) for p in range(0x1800,0x1840,16) if r[p]}
    def owners():return bytes(ram()[0x1f25:0x1f29])
    try:
        if a.consumer_title:
            h.run(480,[8]);h.run();h.run(1,[3]);h.run(3)
        else:h.run(100)
        command(3);h.run(20)
        # Place the existing single on each physical tone by retaining earlier
        # singles, then remove the fillers. The descriptor permits all tones.
        # Its initial mask is changed to all tones in the variant ROM below.
        results=[]
        for occupied in range(3):
            command(3)
            for i in range(occupied+1):play(single,8,100+i)
            for i in range(occupied):command(6,100+i)
            before=effects();assert before=={100+occupied:(5+occupied,0)},before
            writes.clear();play(pair,8,200)
            acquired=effects();mask=7^(1<<occupied)
            assert acquired[200][1]==mask and acquired[100+occupied]==before[100+occupied],acquired
            channels=[i for i in range(3) if mask&(1<<i)]
            expected=[0x81|(channels[0]<<5),0x12,0x90|(channels[0]<<5),0x82|(channels[1]<<5),0x23,0x93|(channels[1]<<5)]
            assert expected[0] in writes,(occupied,writes,acquired);start=writes.index(expected[0]);assert writes[start:start+6]==expected,(writes,expected)
            h.run(10);assert effects()==before and owners()[occupied]
            results.append({'occupied':occupied,'group_mask':mask,'exact_chip_writes':True})
        command(3)
        for i in range(3):play(single,8,300+i)
        before=effects();state=owners();play(pair,8,400);assert effects()==before and owners()==state
        play(triple,9,401);assert effects()[401][1]==7 and len(effects())==1
        command(6,401);assert not effects() and not any(owners())
        # Natural completion of three tones also releases the complete set.
        writes.clear();play(triple,8,402);h.run(10);assert not effects() and not any(owners())
        expected=[0x81,0x12,0x90,0xa2,0x23,0xb3,0xc3,0x34,0xd6];start=writes.index(0x81)
        assert writes[start:start+9]==expected,writes
        play(loop,8,403);h.run(40);assert effects()[403][1]==3
        generation=int.from_bytes(ram()[0x1d08:0x1d0a],'little');command(2,token=generation);assert 403 in effects()
        command(6,403);assert not effects() and not any(owners())
        play(triple,8,404);play(single,9,405);assert len(effects())==1 and 405 in effects()
        assert sum(bool(owner) for owner in owners())==1;command(6,405)
        # Queue independent PSG voices and PCM in one command batch. A cache
        # refill must preserve IX rather than writing PSG state into PCM0.
        command(3)
        song=base+int.from_bytes(raw[base+10:base+13],'little')
        command(1,song-base,token=1)
        command(9,(single-base)|(8<<24),501,frames=0)
        command(9,(pair-base)|(8<<24),502,frames=0)
        command(4,(pcm-base)|(8<<24),503,frames=0)
        # Allow the queued song start and three SFX commands to finish.
        h.run(4);r=ram()
        assert r[0x1ec0] and int.from_bytes(r[0x1eae:0x1eb0],'little')==503, ('PCM owner corrupted by PSG refill',r[0x1ea0:0x1eb0].hex())
        assert effects()=={501:(5,0),502:(6,6)},effects()
        command(6,503);assert not ram()[0x1ec0]
        command(3)
        r=ram();assert not r[0x1d05] and r[0x1738:0x1740]==bytes([165]*8) and r[0x1f78:0x1f80]==bytes([165]*8)
        report={'region':'pal' if a.pal else 'ntsc','overlap':results,'equal_priority_atomic_rejection':True,
                'three_tone_preemption_and_completion':True,'token_stop':True,'whole_group_preemption':True,'persistent_loop':True,'page_crossing':True,'pcm_overlap':True,'guards':True,'fault':0}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n',newline='\r\n');print(report)
    finally:h.close()
if __name__=='__main__':main()
