"""Trace multi-channel FM notes/levels and assembled atomic ownership."""
import argparse
import ctypes as C
import copy
import json
import os
from pathlib import Path
import struct
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pal',action='store_true');parser.add_argument('--hybrid',action='store_true');parser.add_argument('--automation',action='store_true',help='trace distinct patches, volume macros and vibrato on five channels');a=parser.parse_args()
    out=ROOT/'out'/('multi-fm-'+('hybrid-' if a.hybrid else '')+('pal' if a.pal else 'ntsc')+('-automation' if a.automation else ''));out.mkdir(exist_ok=True)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1')
    if a.automation:
        from unittest.mock import patch
        os.environ['CALLIOPE_PCM_TIMER']=str(int.from_bytes(raw[base+19:base+21],'little'))
        from calliope.compiler import compile_bank
        from calliope.furnace import Cell,parse
        source=ROOT/'examples/multi_fm232.fur';module=parse(source)
        module.subsongs[0].rows=4;module.subsongs[0].speeds=[3]
        original=module.instruments[0];module.instruments=[];module.patterns={}
        for slot in range(5):
            ins=copy.deepcopy(original);voice=bytearray(ins['features']['FM']);voice[6]=20+slot*7
            for op in range(4):voice[9+op*8]|=0x40
            ins['features']['FM']=bytes(voice)
            ins['features']['MA']=bytes((8,0,0,4,0,2,0,0,0,1,127,112,95,80,255))
            module.instruments.append(ins)
            module.patterns[(0,slot,0)]={0:Cell(note=108+slot,instrument=slot,volume=127-slot*12,effects=[(0,4,0x11+slot),(1,0xe5,128+slot*8)]),
                1:Cell(volume=95-slot*8),2:Cell(note=182)}
        manifest=json.loads((ROOT/'examples/bank.json').read_text());manifest.pop('songs')
        for item in manifest['sfx']:item['source']=str(ROOT/'examples'/item['source'])
        path=out/'bank.json';path.write_text(json.dumps(manifest))
        with patch('calliope.compiler.parse',side_effect=lambda p:module if Path(p)==source else parse(p)):
            data,_,_=compile_bank(path,'pal' if a.pal else 'ntsc')
        raw[base:base+len(data)]=data
    table=base+int.from_bytes(raw[base+13:base+16],'little')
    desc={int.from_bytes(raw[p:p+2],'little'):p for p in range(table,table+raw[base+7]*16,16)}
    assert raw[desc[10]+2]==8 and raw[desc[10]+7]==31
    # Independent FM1/FM5 victims and a persistent five-channel loop.
    raw[desc[2]+2:desc[2]+16]=raw[desc[3]+2:desc[3]+16]
    raw[desc[2]+7]=16;raw[desc[3]+7]=1
    raw[desc[8]+2:desc[8]+16]=raw[desc[10]+2:desc[10]+16]
    raw[desc[8]+8:desc[8]+11]=raw[desc[10]+4:desc[10]+7]
    patch_table=base+int.from_bytes(raw[base+16:base+19],'little')
    p=base+int.from_bytes(raw[desc[10]+4:desc[10]+7],'little')
    expected={slot:[] for slot in range(5)};levels={};notes={slot:0 for slot in range(5)}
    while raw[p]!=255:
        op=raw[p];p+=1
        if op==0:p+=1;continue
        slot=raw[p];p+=1
        if op==0x10:
            patch=patch_table+raw[p]*32;p+=1;levels[slot]=list(raw[patch+4:patch+8])
        elif op==0x37:levels[slot]=list(raw[p:p+4]);p+=5
        elif op in (0x11,0x36):
            expected[slot].append((int.from_bytes(raw[p:p+2],'little'),list(levels[slot])));p+=2
            if op==0x11:notes[slot]+=1
        else:assert op==0x13
    if a.hybrid:
        # Hybrid suppresses unchanged frequency writes, including fresh notes;
        # key-on counts below still check every authored note independently.
        for slot,values in expected.items():
            expected[slot]=[value for i,value in enumerate(values) if not i or value[0]!=values[i-1][0]]
    rom=out/'test.bin';rom.write_bytes(raw)
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,
        {b'genesis_plus_gx_region_detect':b'pal' if a.pal else b'ntsc'})
    h.core.retro_set_controller_port_device(0,513)
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    high={slot:0 for slot in range(6)};tl={slot:[0]*4 for slot in range(6)};actual={slot:[] for slot in range(6)};keyons=[];keyoffs=[]
    def record(cycles,reg,value,pc):
        port=reg>>8;address=reg&255
        if port>1:return
        if 0xa4<=address<=0xa6:high[port*3+address-0xa4]=value
        elif 0xa0<=address<=0xa2:
            slot=port*3+address-0xa0;actual[slot].append(((high[slot]<<8)|value,list(tl[slot])))
        elif 0x40<=address<=0x4e and address&3<3:
            slot=port*3+(address&3);tl[slot][(address-0x40)//4]=value
        elif port==0 and address==0x28:
            channel=value&7;slot=channel if channel<3 else channel-1
            (keyons if value&0xf0 else keyoffs).append(slot)
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
        for _ in range(12):
            if ram()[0x1d01]==ram()[0x1d02]:break
            h.run(1)
        assert ram()[0x1d01]==ram()[0x1d02],'command queue did not drain'
    def play(id,priority,token):command(9,(desc[id]-base)|(priority<<24),token)
    def owners():return bytes(ram()[0x1f20:0x1f25])
    def tokens():
        r=ram();return {int.from_bytes(r[p+4:p+6],'little') for p in range(0x1800,0x1840,16) if r[p]}
    def all_owned():
        state=owners();assert state[0] and len(set(state))==1,state
    try:
        h.run(100)
        if a.hybrid:h.run(1,(0,));h.run(30)
        else:command(3);h.run(20)
        for slot in actual:actual[slot].clear()
        keyons.clear();keyoffs.clear()
        h.run(1,(2,1));h.run(200)
        assert {slot:actual[slot] for slot in range(5)}==expected,(actual,expected)
        assert not actual[5] and 5 not in keyons,'FM6 must remain reserved for PCM'
        assert {slot:keyons.count(slot) for slot in range(5)}==notes,keyons
        assert keyoffs[-5:]==list(range(5)),keyoffs
        if not a.hybrid and not a.automation:
            assert not tokens() and owners()==bytes(5)
            play(3,1,30);play(2,4,20);before=owners();assert before[0] and before[4]
            play(10,3,100);assert owners()==before and tokens()=={30,20}
            play(10,5,101);all_owned();assert tokens()=={101}
            play(2,5,21);all_owned();assert tokens()=={101}
            play(2,6,22);assert owners()[:4]==bytes(4) and owners()[4];assert tokens()=={22}
            command(6,22);assert not any(owners())
            play(10,2,102);command(2);all_owned();command(6,102);assert not any(owners())
            for token in range(40,43):play(4,2,token)
            play(6,2,43);assert len(tokens())==4
            play(10,3,103);assert not any(owners()) and 103 not in tokens()
            command(3);assert not tokens()
            play(8,2,80);h.run(180);all_owned();assert tokens()=={80}
            command(6,80);assert not any(owners())
            play(8,2,81);play(2,3,23);assert owners()[:4]==bytes(4) and owners()[4] and tokens()=={23}
            command(3);assert not any(owners()) and not tokens()
        r=ram();assert r[0x1d05]==0 and r[0x1d1a]==0
        guard=0xff8 if a.hybrid else 0x1738
        assert r[guard:guard+8]==bytes([165]*8) and r[0x1f78:0x1f80]==bytes([165]*8)
        report={'region':'pal' if a.pal else 'ntsc','hybrid':a.hybrid,'frequency_writes':sum(len(v) for v in expected.values()),
            'independent_levels':True,'audition_shortcut':True,'fm6_reserved':True,'fault':0,'underrun':0,'guards':True}
        report['automation']=a.automation
        if not a.hybrid and not a.automation:report.update(atomic_allocation=True,whole_effect_preemption=True,token_stop=True,capacity_rejection=True,persistent_loop=True)
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(report)
    finally:h.close()


if __name__=='__main__':main()
