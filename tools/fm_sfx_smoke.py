"""Assembled FM SFX priority, ownership and fresh-note restoration checks."""
import os
import argparse
import subprocess
import sys
import json
from pathlib import Path
import struct
from libretro_host import Host
from audition_fixture import hide_song
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--core',default=os.environ.get('CALLIOPE_CORE','genesis_plus_gx'));parser.add_argument('--pal',action='store_true');parser.add_argument('--case',choices=['baseline','restore','custom','mask','unused'],help=argparse.SUPPRESS);args=parser.parse_args()
    if args.pal and args.case is None:
        # The installed core cannot safely initialize a second PAL session.
        for case in ['baseline','restore','custom','mask','unused']:
            subprocess.run([sys.executable,__file__,'--pal','--case',case,'--core',args.core],check=True)
        return
    options={b'genesis_plus_gx_region_detect':b'pal' if args.pal else b'ntsc'}
    out=ROOT/('out/fm-sfx-pal' if args.pal else 'out/fm-sfx');out.mkdir(exist_ok=True)
    report={}
    raw=hide_song(bytearray((ROOT/'audition/out/rom.bin').read_bytes()),5);base=raw.index(b'BGM1')
    table=base+int.from_bytes(raw[base+13:base+16],'little')
    desc=next(p for p in range(table,table+raw[base+7]*16,16) if raw[p+2]==2)
    psg_desc=next(p for p in range(table,table+raw[base+7]*16,16) if raw[p+2]==3)
    song=base+int.from_bytes(raw[base+10:base+13],'little');raw[song+3]=0
    rom=out/'test.bin';rom.write_bytes(raw)
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    def snap():
        state=bytearray(h.save());offset=state.find(needle)-0x38
        assert offset>=0
        return state,offset
    def ram():
        s,o=snap();return s[o:o+8192]
    def commands(entries,frames=1):
        s,o=snap();w=s[o+0x1d01]
        for op,token,arg in entries:
            if op in (2,7,8,10,11):token=int.from_bytes(s[o+0x1d08:o+0x1d0a],'little')
            pos=o+0x1d40+(w&15)*8;s[pos:pos+8]=struct.pack('<BBHI',op,0,token,arg);w=(w+1)&255
        s[o+0x1d01]=w;h.load(bytes(s));h.run(frames)
        for _ in range(12):
            if ram()[0x1d01]==ram()[0x1d02]: break
            h.run(1)
        h.run(2)
    def active():
        r=ram();return [(r[p+1],r[p+2],r[p+3],int.from_bytes(r[p+4:p+6],'little')) for p in range(0x1800,0x1840,16) if r[p]]
    if args.case in (None,'baseline'):
        h=Host(args.core,rom,out,options)
        try:
            h.run(100)
            commands([(9,10+i,(desc-base)|(2<<24)) for i in range(4)],6)
            a=active();assert len(a)==4,a
            assert sorted(x[0] for x in a)==[1,2,3,4],a
            commands([(9,99,(desc-base)|(2<<24))]);assert all(x[3]!=99 for x in active())
            commands([(9,99,(desc-base)|(3<<24))]);a=active()
            assert any(x[3]==99 and x[0]==1 for x in a),a
            assert all(x[3]!=10 for x in a),a
            commands([(2,0,0)]);assert len(active())==4,'music stop must preserve SFX'
            commands([(6,0,99)]);assert all(x[3]!=99 for x in active())
            commands([(3,0,0)]);assert not active()
            # Force a real music steal using the resource mask in a second ROM run.
            report={'arbitration_checks':7,'fault':ram()[0x1d05],'pcm_underrun':ram()[0x1d1a]}
            assert report['fault']==0
        finally:h.close()

    raw[desc+7]=1;rom.write_bytes(raw)
    if args.case in (None,'restore'):
        h=Host(args.core,rom,out,options)
        try:
            h.run(100);commands([(9,50,(desc-base)|(2<<24))],2)
            assert active()[0][0]==0 and ram()[0x1f20]!=0,'music voice not stolen'
            assert ram()[0x1f30]==1,'music restoration must be deferred'
            h.run(90)
            assert not active(),'FF must complete SFX'
            assert ram()[0x1f20]==0 and ram()[0x1f30]==0,('fresh music note must restore current patch',ram()[0x1f20:0x1f38].hex(),ram()[0x1d00:0x1d20].hex())
            assert ram()[0x1d0e]==1,'music must continue'
            assert ram()[0x1738:0x1740]==bytes([0xa5]*8) and ram()[0x1f78:0x1f80]==bytes([0xa5]*8),'stack guard'
            report['stack_guards']=True
            report['restoration_checks']=4;report['restore_fault']=ram()[0x1d05]
            report['restore_pcm_underrun']=ram()[0x1d1a]
            assert report['restore_fault']==0
        finally:h.close()

    # A custom song order selects music victims, while masks and unused
    # resources still take precedence and SFX age arbitration is unchanged.
    raw[song+7]=255
    raw[song+21:song+24]=bytes((2,1,0))
    raw[psg_desc+7]=224
    raw[song+16:song+21]=bytes((4,3,2,1,0))
    raw[desc+7]=31;rom.write_bytes(raw)
    if args.case in (None,'custom'):
        h=Host(args.core,rom,out,options)
        try:
            h.run(100)
            commands([(9,70+i,(desc-base)|(2<<24)) for i in range(4)],6)
            a=active();assert [x[0] for x in a]==[4,3,2,1],a
            commands([(9,80,(desc-base)|(2<<24))])
            assert all(x[3]!=80 for x in active()),'equal priority must reject'
            commands([(9,81,(desc-base)|(3<<24))])
            assert any(x[3]==81 and x[0]==4 for x in active()),'oldest SFX must still win tie'
            commands([(3,0,0)])
            commands([(1,3,song-base)])
            assert ram()[0x1f70:0x1f73]==bytes((2,1,0)),'PSG order must load'
            commands([(9,100+i,(psg_desc-base)|(2<<24)) for i in range(3)],6)
            assert [x[0] for x in active()]==[7,6,5],active()
            commands([(9,110,(psg_desc-base)|(2<<24))])
            assert all(x[3]!=110 for x in active()),'PSG equal priority must reject'
            commands([(9,111,(psg_desc-base)|(3<<24))])
            assert any(x[3]==111 and x[0]==7 for x in active()),'PSG SFX priority/age must remain unchanged'
            commands([(3,0,0)])
            # Start the second descriptor with its default order: no stale ranks.
            commands([(1,2,song+24-base)])
            assert ram()[0x1f6b:0x1f70]==bytes(range(5)),'song switch must load order'
            assert ram()[0x1f70:0x1f73]==bytes(range(3)),'song switch must load PSG order'
            assert ram()[0x1d05]==0,'custom-order allocation fault'
            assert ram()[0x1738:0x1740]==bytes([0xa5]*8) and ram()[0x1f78:0x1f80]==bytes([0xa5]*8),'stack guard'
        finally:h.close()

    raw[desc+7]=5;raw[psg_desc+7]=96;rom.write_bytes(raw)
    if args.case in (None,'mask'):
        h=Host(args.core,rom,out,options)
        try:
            h.run(100);commands([(9,90,(desc-base)|(2<<24))])
            assert active()[0][0]==2,'resource mask must constrain custom order'
            commands([(9,92,(psg_desc-base)|(2<<24))])
            assert any(x[3]==92 and x[0]==6 for x in active()),'PSG resource mask must constrain order'
            assert ram()[0x1d05]==0
        finally:h.close()

    raw[song+7]=222;raw[desc+7]=31;raw[psg_desc+7]=224;rom.write_bytes(raw)
    if args.case in (None,'unused'):
        h=Host(args.core,rom,out,options)
        try:
            h.run(100);commands([(9,91,(desc-base)|(2<<24))])
            assert active()[0][0]==0,'unused channel must precede custom music order'
            commands([(9,93,(psg_desc-base)|(2<<24))])
            assert any(x[3]==93 and x[0]==5 for x in active()),'unused PSG channel must precede order'
            assert ram()[0x1d05]==0
        finally:h.close()

    if args.case is None:
        report['steal_order_checks']=14
    else:
        report[args.case]='passed'
    (out/('report-'+args.case+'.json' if args.case else 'report.json')).write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
