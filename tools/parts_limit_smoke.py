"""Check the assembled driver at both ends of its sixteen-voice state page."""
import argparse, ctypes as C, json, struct
from pathlib import Path
from unittest.mock import patch
from calliope.compiler import compile_bank
from calliope.furnace import parse, Cell
from calliope.fm import frequency
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pal',action='store_true');args=parser.parse_args()
    region='pal' if args.pal else 'ntsc'
    out=ROOT/'out'/('parts-limit-'+region);out.mkdir(exist_ok=True)
    source=ROOT/'examples/fm_ring.fur';module=parse(source);song=module.subsongs[0]
    module.channels=[(i//6,'fm',i%6) for i in range(16)]
    names=[f'voice{i}' for i in range(16)]
    song.rows=64;song.speeds=[1];song.orders=[[0] for _ in names];song.names=names
    module.patterns={(0,i,0):{i:Cell(note=108+i,instrument=0,volume=127)} for i in range(16)}
    module.instruments[0]['features'].pop('MA',None)
    manifest=out/'bank.json'
    manifest.write_text(json.dumps({'version':1,'songs':[{'id':3,'name':'limit','source':str(source),
        'voices':[{'name':n,'channel':i,'slot':'fm0'} for i,n in enumerate(names)],
        'groups':{'all':names},'arrangements':{'first':[names[0]],'last':[names[-1]]},
        'initial_arrangement':'last'}]}))
    with patch('calliope.compiler.parse',return_value=module):data,_,_=compile_bank(manifest,region)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1');raw[base:base+len(data)]=data
    rom=out/'limit.bin';rom.write_bytes(raw)
    host=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':region.encode()})
    host.core.retro_set_controller_port_device(0,513)
    host.core.clp_zram.restype=C.POINTER(C.c_ubyte)
    registers={}
    def record(cycles,reg,value,pc):
        if reg!=0x2a:registers[reg]=value
    callback=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record)
    host.core.clp_chip_trace_set.argtypes=[type(callback)];host.core.clp_chip_trace_set(callback)
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    def ram():return bytes(host.core.clp_zram()[:8192])
    def command(mask):
        state=bytearray(host.save());o=state.find(needle)-0x38;assert o>=0
        w=state[o+0x1d01];generation=int.from_bytes(state[o+0x1d08:o+0x1d0a],'little')
        at=o+0x1d40+(w&15)*8
        state[at:at+8]=struct.pack('<BBHI',11,1,generation,mask);state[o+0x1d01]=(w+1)&255
        host.load(bytes(state))
    try:
        host.run(100);host.run(1,(10,));host.run(22)
        z=ram();assert z[0x1f1a]==16
        for i in range(16):
            p=0x1b00+16*i
            assert z[p+3]==(i==15) and z[p+7]==1,(i,z[p:p+16].hex())
            assert int.from_bytes(z[p+5:p+7],'little')==frequency(108+i,region),(i,z[p:p+16].hex())
        assert int.from_bytes(z[0x1f1b:0x1f1d],'little')==0x1bf0,'voice 15 wrapped into voice zero'
        assert registers[0xa0]|registers[0xa4]<<8==frequency(123,region)
        command(1);host.run(2)
        assert ram()[0x1b03]==1 and ram()[0x1bf3]==0
        command(32768);host.run(2)
        assert ram()[0x1b03]==0 and ram()[0x1bf3]==1
        z=ram();assert not z[0x1d05] and not z[0x1d1a]
        assert z[0x1738:0x1740]==bytes([165]*8) and z[0x1f78:0x1f80]==bytes([165]*8)
        report={'region':region,'voices':16,'silent_states_advance':True,'first_and_last_selection':True,'fault':0,'underrun':0}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:host.close()
if __name__=='__main__':main()
