"""Verify live carrier attenuation preserves frequency, envelope and SFX volume."""
import ctypes as C
import json,struct
from pathlib import Path
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]
def main():
    out=ROOT/'out/volume';out.mkdir(exist_ok=True)
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',ROOT/'audition/out/rom.bin',out)
    writes=[];regs={}
    def record(cycles,reg,value,pc):
        if reg!=0x2a:writes.append((reg,value));regs[reg]=value
    cb=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record)
    h.core.clp_chip_trace_set.argtypes=[type(cb)];h.core.clp_chip_trace_set(cb)
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    def command(op,arg):
        s=bytearray(h.save());base=s.find(needle)-0x38;assert base>=0
        w=s[base+0x1d01];p=base+0x1d40+(w&15)*8;gen=int.from_bytes(s[base+0x1d08:base+0x1d0a],'little')
        s[p:p+8]=struct.pack('<BBHI',op,0,gen,arg);s[base+0x1d01]=(w+1)&255;h.load(bytes(s));h.run(2)
    try:
        h.run(100);command(12,0)
        initial={r:regs[r] for r in (0x40,0x44,0x48,0x4c)}
        saved=h.save();base=saved.find(needle)-0x38;mask=saved[base+0x18df]
        frequency=(regs[0xa0],regs[0xa4]);writes.clear()
        command(12,20|(5<<8))
        assert all(regs[r]==min(127,v+(20 if mask&(1<<op) else 0)) for op,(r,v) in enumerate(initial.items())),(initial,regs)
        assert (regs[0xa0],regs[0xa4])==frequency
        assert not any(r==0x28 for r,v in writes),'volume must not restart FM envelopes'
        command(12,0);assert all(regs[r]==v for r,v in initial.items()),'attenuation must not accumulate'
        command(12,127);assert all(regs[r]==(127 if mask&(1<<op) else v) for op,(r,v) in enumerate(initial.items()))
        # Vary carrier masks across all five physical shadow records. A page
        # test must survive operator-address arithmetic on every channel.
        saved=bytearray(h.save());base=saved.find(needle)-0x38
        masks=(8,14,14,15,12);authored=(15,20,30,40)
        for channel,mask in enumerate(masks):
            at=base+0x18d8+channel*8
            saved[at:at+8]=bytes(authored)+bytes(3)+bytes((mask,))
        h.load(bytes(saved));writes.clear();command(12,8)
        expected={(256 if channel>=3 else 0)+0x40+operator*4+channel%3:
                  value+(8 if masks[channel]&(1<<operator) else 0)
                  for channel in range(5) for operator,value in enumerate(authored)}
        actual={}
        for reg,value in writes:
            if reg in expected and reg not in actual:actual[reg]=value
        assert actual==expected,('Physical-channel carrier gain',expected,actual)
        command(12,127)
        raw=(ROOT/'audition/out/rom.bin').read_bytes();bank=raw.index(b'BGM1');table=bank+int.from_bytes(raw[bank+13:bank+16],'little')
        desc=next(p for p in range(table,table+raw[bank+7]*16,16) if raw[p+2]==2)
        command(9,(desc-bank)|(2<<24))
        state=h.save();base=state.find(needle)-0x38
        owner=next(channel for channel in range(5) if state[base+0x1f20+channel])
        owned={(256 if owner>=3 else 0)+0x40+op*4+owner%3 for op in range(4)}
        before={reg:regs[reg] for reg in owned};assert any(value<127 for value in before.values()),'SFX must bypass music volume'
        writes.clear();command(2,0)
        assert {reg:regs[reg] for reg in owned}==before,'music stop must preserve the FM effect'
        assert not any(reg in owned for reg,value in writes),'music stop wrote an SFX-owned TL'
        assert all(regs.get((256 if ch>=3 else 0)+0x40+op*4+ch%3)==127
                   for ch in range(5) if ch!=owner for op in range(4)),'music stop must silence release tails'
        state=h.save();base=state.find(needle)-0x38;assert state[base+0x1d05]==0 and state[base+0x1d1a]==0
        report={'all_physical_carrier_masks':True,'held_volume':True,'no_envelope_restart':True,'no_accumulation':True,'sfx_bypass':True,'stop_silences_unowned_fm':True,'stop_preserves_sfx':True,'fault':0,'underrun':0}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:h.close()
if __name__=='__main__':main()
