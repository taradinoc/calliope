"""Synthetic consumer coverage: legacy files, raw macros, rates and many patches."""
import copy
import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import clpconvert
from calliope.furnace import parse,Cell,FormatError
from calliope.compiler import compile_bank,fm_patch
from calliope.macros import sequences
from calliope.register_psg import compile_register_psg
from calliope.psg import tone_period
from make_fixtures import make
class Consumer(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def module(self,version=156,kind=3,notes=(105,106),channels=(0,)):
        p=self.root/'source.fur';p.write_bytes(make(version,kind,notes=notes,event=False,track_channels=channels));return parse(p)
    def test_patr_matches_modern_patterns(self):
        legacy=self.module();modern=self.module(232)
        self.assertEqual(legacy.channels,modern.channels)
        self.assertEqual(legacy.patterns,modern.patterns)
        self.assertEqual(legacy.subsongs[0],modern.subsongs[0])
    def test_raw_period_legato_and_macro_length(self):
        m=self.module();m.flags['compat_legacy'][1]=0;m.subsongs[0].speeds=[1];m.subsongs[0].rows=4
        macro=struct.pack('<H',8)+bytes((4,70,255,255,0,128,0,1))+struct.pack('<70h',*range(70))+b'\xff'
        m.instruments[0]['features']['MA']=macro
        m.patterns={(0,0,0):{0:Cell(105,0,15),1:Cell(106,None,8,[(0,0xea,1)]),2:Cell(107),3:Cell(180)}}
        stream,_=compile_register_psg(m,0,[0],'ntsc',{'name':'raw'})
        p=tick=0;period=0;periods=[]
        while stream[p]!=255:
            op=stream[p];p+=1
            if op==0:tick+=stream[p];p+=1;continue
            n=stream[p];p+=1;values=stream[p:p+n];p+=n
            if values[0]&0xf0==0x80:
                period=(values[0]&15)|(values[1]<<4);periods.append((tick,period))
        self.assertEqual(periods,[(i,tone_period(105+i,m,'ntsc')-i) for i in range(3)])
        self.assertEqual(len(sequences(m.instruments[0],(4,))[4].values),70)
    def test_stationary_held_loop_and_unstable_rejection(self):
        m=self.module(notes=(105,));m.flags['compat_legacy'][1]=0;m.subsongs[0].rows=3;m.subsongs[0].speeds=[1]
        m.patterns={(0,0,0):{0:Cell(105,0,15),2:Cell(effects=[(0,0x0b,0),(1,0x0d,1)])}}
        item={'name':'hold','loop':{'start':[0,1],'end':[1,0]}}
        stream,offset=compile_register_psg(m,0,[0],'ntsc',item)
        self.assertIsNotNone(offset);self.assertEqual(stream[offset],0x30)
        m.instruments[0]['features']['MA']=struct.pack('<H',8)+bytes((4,3,255,255,0,128,0,1))+struct.pack('<3h',0,1,2)+b'\xff'
        with self.assertRaisesRegex(FormatError,'preserve macro'):compile_register_psg(m,0,[0],'ntsc',item)
    def test_fixed_noise_and_explicit_phase_reset(self):
        m=self.module(channels=(3,),notes=(108,));m.flags['compat_legacy'][1]=0;m.subsongs[0].rows=3;m.subsongs[0].speeds=[1]
        m.patterns={(0,3,0):{0:Cell(108,0,15)}}
        macro=struct.pack('<H',8)
        for code,values in ((2,(0,0,0)),(14,(0,1,1))):
            macro+=bytes((code,3,255,255,0,128,0,1))+struct.pack('<3h',*values)
        m.instruments[0]['features']['MA']=macro+b'\xff'
        stream,_=compile_register_psg(m,0,[3],'ntsc',{'name':'noise'})
        self.assertEqual(stream.count(bytes((0x30,1,0xe2))),3)
        m.patterns[(0,3,0)][0].note=114
        with self.assertRaisesRegex(FormatError,'fixed noise notes'):compile_register_psg(m,0,[3],'ntsc',{'name':'noise'})
    def test_explicit_rate_and_high_fm_note(self):
        p=self.root/'source.fur';p.write_bytes(make(232,notes=(156,),event=False,hz=57.3333))
        manifest=self.root/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'fm','source':p.name,'tick_rate':57.4,'voices':[{'channel':0,'slot':'fm0'}]}]}))
        data,_,_=compile_bank(manifest);descriptor=int.from_bytes(data[10:13],'little')
        self.assertEqual(int.from_bytes(data[descriptor+14:descriptor+16],'little'),287)
        content=json.loads(manifest.read_text());content['songs'][0]['tick_rate']=57.3333;manifest.write_text(json.dumps(content))
        with self.assertRaisesRegex(FormatError,'0.2 Hz'):compile_bank(manifest)
    def test_more_than_sixteen_rom_patches(self):
        m=self.module(232,0x83,notes=(108,));base=copy.deepcopy(m.instruments[0]);m.subsongs[0].rows=21;m.subsongs[0].speeds=[1]
        m.instruments=[];m.patterns={(0,0,0):{}}
        for i in range(20):
            ins=copy.deepcopy(base);fm=bytearray(ins['features']['FM']);fm[6]=i;ins['features']['FM']=bytes(fm);m.instruments.append(ins)
            m.patterns[(0,0,0)][i]=Cell(108,i,127)
        m.patterns[(0,0,0)][20]=Cell(180)
        manifest=self.root/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'many','source':'source.fur','voices':[{'channel':0,'slot':'fm0'}]}]}))
        with patch('calliope.compiler.parse',return_value=m):data,report,_=compile_bank(manifest)
        self.assertEqual(report['patches'],20);self.assertEqual(data[8],20)
    def test_cli_rejects_manifest_and_sidecar_collisions(self):
        manifest=self.root/'bank.json';original=json.dumps({'version':1,'songs':[]});manifest.write_text(original)
        for arguments in (['ids',str(manifest),'--output',str(manifest)],
                          ['compile',str(manifest),'--output',str(manifest.with_suffix('.bin'))],
                          ['compile',str(manifest),'--output',str(self.root/'other.json')]):
            with patch('sys.argv',['clpconvert',*arguments]),contextlib.redirect_stderr(io.StringIO()),patch('clpconvert.compile_cached') as compile_call:
                self.assertEqual(clpconvert.main(),1);compile_call.assert_not_called()
            self.assertEqual(manifest.read_text(),original)
    def test_relocatable_tones(self):
        source=self.root/'source.fur';source.write_bytes(make(232,3,notes=(105,106),event=False,track_channels=(0,2),extra_tracks=(((110,111),'harmony'),)));m=parse(source);manifest=self.root/'bank.json'
        item={'id':1,'name':'pair','source':'source.fur','relocatable_tones':True}
        def compile(item,category='sfx'):
            manifest.write_text(json.dumps({'version':1,category:[item]}))
            with patch('calliope.compiler.parse',return_value=m):return compile_bank(manifest)
        for raw in (False,True):
            if raw:m.flags['compat_legacy'][1]=0
            data,report,_=compile(item);entry=report['effects'][0]
            desc=int.from_bytes(data[13:16],'little')
            self.assertEqual(data[desc+2],7);self.assertEqual(data[desc+7],2)
            stream=data[entry['offset']:];p=0;channels=set()
            while stream[p]!=255:
                op=stream[p];p+=1
                if op==0:p+=1;continue
                self.assertEqual(op,0x30);n=stream[p];p+=1
                channels.update((b>>5)&3 for b in stream[p:p+n] if b&128);p+=n
            self.assertEqual(channels,{0,1})
        for invalid,message in ((dict(item,resource_mask=160),'resource'),(dict(item,relocatable_tones=1),'boolean')):
            with self.assertRaisesRegex(FormatError,message):compile(invalid)
        single=self.module(232,3,channels=(0,))
        with patch('calliope.compiler.parse',return_value=single):
            manifest.write_text(json.dumps({'version':1,'sfx':[item]}))
            with self.assertRaisesRegex(FormatError,'two or three'):compile_bank(manifest)

    def test_song_lfo_and_reset(self):
        self.module(232,0x83,notes=(108,));manifest=self.root/'bank.json'
        first={'id':1,'name':'lfo','source':'source.fur','voices':[{'channel':0,'slot':'fm0'}],'ym_lfo':8}
        second=dict(first,id=2,name='plain');second.pop('ym_lfo')
        manifest.write_text(json.dumps({'version':1,'songs':[first,second]}));data,_,_=compile_bank(manifest)
        from calliope.reference import Player
        player=Player(data);player.start(1);player.step();self.assertIn((1,'ym_lfo',8),player.trace)
        player.start(2);player.step();self.assertEqual(player.trace[-1][0],1);self.assertIn((1,'ym_lfo',0),player.trace)
        for value in (-1,1,7,16,True,'8'):
            first['ym_lfo']=value;manifest.write_text(json.dumps({'version':1,'songs':[first]}))
            with self.assertRaisesRegex(FormatError,'ym_lfo'):compile_bank(manifest)

    def test_inert_fm_sample_metadata(self):
        m=self.module(232,0x83);ins=m.instruments[0];before=fm_patch(ins)
        ins['features'].update(SM=bytes((0,0,0,31)),NE=bytes((1,)))
        self.assertEqual(fm_patch(ins),before)
if __name__=='__main__':unittest.main()
