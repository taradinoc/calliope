"""Exercise assembled PCM command ownership through the serialized Z80 mailbox.
Run pcm_stress.py --loop --no-dma first to generate the long looping fixture.
This checks Z80 command semantics, not 68000 bus-handshake timing.
"""
import os
import argparse
import json
from pathlib import Path
import struct
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--core',default=os.environ.get('CALLIOPE_CORE','genesis_plus_gx'));a=p.parse_args()
    out=ROOT/'out/pcm-commands'; out.mkdir(exist_ok=True)
    rom=ROOT/'out/pcm-stress-genesis_plus_gx-ntsc-loop-no-dma/stress.bin'
    raw=rom.read_bytes(); base=raw.index(b'BGM1'); descriptor=int.from_bytes(raw[base+13:base+16],'little')
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    h=Host(a.core,rom,out)
    def snapshot():
        state=bytearray(h.save()); offset=state.find(needle)-0x38
        assert offset>=0,'Z80 RAM signature missing'
        return state,offset
    def ram():
        state,offset=snapshot(); return state[offset:offset+8192]
    def command(op,argument=0,token=0):
        state,offset=snapshot(); w=state[offset+0x1d01]
        assert (w-state[offset+0x1d02])&255<16
        pos=offset+0x1d40+(w&15)*8
        if op in (2,7,8,10,11):token=int.from_bytes(state[offset+0x1d08:offset+0x1d0a],'little')
        state[pos:pos+8]=struct.pack('<BBHI',op,0,token,argument)
        state[offset+0x1d01]=(w+1)&255
        h.load(bytes(state)); h.run(4)
    try:
        h.run(100)
        assert ram()[0x1eac]==1 and ram()[0x1ec0]==1,'initial looping SFX'
        command(10,512) # suppress authored drum triggers; keep music clock running
        command(5,descriptor)
        assert ram()[0x1ec0]==1,'music requests must not interrupt PCM SFX'
        command(4,descriptor|(1<<24),99)
        assert int.from_bytes(ram()[0x1eae:0x1eb0],'little')==2,'equal priority must not preempt'
        command(4,descriptor|(2<<24),99)
        assert int.from_bytes(ram()[0x1eae:0x1eb0],'little')==99,'higher priority must preempt'
        command(6,98); assert ram()[0x1eac]==1,'wrong token must not stop'
        command(6,99); assert ram()[0x1eac]==0,'matching token must stop'
        assert ram()[0x1ec0]==0,'SFX stop releases ownership'
        h.run(30)
        assert ram()[0x1eac]==0,'preempted music must not resume'
        command(5,descriptor)
        assert ram()[0x1eac]==1 and ram()[0x1ec0]==0,'a fresh music request can play'
        command(4,descriptor|(2<<24),100)
        command(7); tick=ram()[0x1d0a:0x1d0e]; h.run(10)
        assert ram()[0x1d0a:0x1d0e]==tick,'pause freezes music tick'
        assert ram()[0x1eac]==1 and ram()[0x1ec0]==1,'pause leaves SFX running'
        command(8); assert ram()[0x1d0a:0x1d0e]!=tick,'resume advances music'
        command(2); assert ram()[0x1d0e]==0 and ram()[0x1eac]==1 and ram()[0x1ec0]==1,'stop music preserves SFX'
        command(3); assert ram()[0x1eac]==0 and ram()[0x1ec0]==0,'stop all releases PCM'
        finite=next(p-base for p in range(base+descriptor,base+descriptor+raw[base+7]*16,16) if raw[p+2]==1 and p-base!=descriptor)
        command(4,finite|(2<<24),101)
        command(5,descriptor)
        assert ram()[0x1ec0]==1,'fresh music is suppressed during finite SFX'
        h.run(180)
        assert ram()[0x1ec0]==0 and ram()[0x1eac]==0,'finite SFX ends without resuming music'
        assert ram()[0x1738:0x1740]==bytes([165]*8) and ram()[0x1f78:0x1f80]==bytes([165]*8)
        report={'checks':14,'fault':ram()[0x1d05],'pcm_underrun':ram()[0x1d1a]}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2)); assert report['fault']==0
        assert report['pcm_underrun']==0,'command transition underrun'
    finally: h.close()
if __name__=='__main__':main()
