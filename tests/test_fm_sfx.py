"""Multi-channel FM descriptors, timing and independent voice expansion."""
import copy
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from calliope.compiler import compile_bank,infer_sfx,fm_patch
from calliope.furnace import Cell,parse,FormatError
from calliope.fm import frequency,volume_options
from make_fixtures import make


def timeline(stream):
    tick=0;p=0;operations=[]
    while stream[p]!=255:
        op=stream[p];p+=1
        if op==0:tick+=stream[p];p+=1;continue
        size={0x10:2,0x11:3,0x13:1,0x36:3,0x37:6}[op]
        operations.append((tick,op,stream[p],bytes(stream[p+1:p+size])));p+=size
    return tick,operations


class MultiFm(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def fixture(self,channels,version=248):
        source=self.root/'effect.fur';notes=(108,112,115)
        source.write_bytes(make(version,event=False,notes=notes,
            extra_tracks=tuple((tuple(n+i for n in notes),f'voice{i}') for i in range(1,len(channels))),
            track_channels=channels,speeds=(3,5)))
        item={'id':10,'name':'multi','source':source.name,'priority':2}
        manifest=self.root/'bank.json';manifest.write_text(json.dumps({'version':1,'sfx':[item]}))
        return parse(source),manifest,item
    def compile_module(self,module,manifest):
        with patch('calliope.compiler.parse',return_value=module):return compile_bank(manifest)
    def test_all_channel_combinations_and_regions(self):
        for version in (232,248):
            for count in range(2,6):
                for channels in itertools.combinations(range(5),count):
                    with self.subTest(version=version,channels=channels):
                        module,manifest,_=self.fixture(channels,version)
                        self.assertEqual(infer_sfx(module),('multi_fm',list(channels)))
                        for region in ('ntsc','pal'):
                            data,report,_=compile_bank(manifest,region)
                            effect=report['effects'][0];desc=int.from_bytes(data[13:16],'little')
                            self.assertEqual(data[desc+2],8);self.assertEqual(data[desc+7],sum(1<<ch for ch in channels))
                            end,ops=timeline(data[effect['offset']:]);self.assertEqual(end,64)
                            self.assertEqual({slot for _,_,slot,_ in ops},set(channels))
                            for ch in channels:
                                expected=[]
                                for row,cell in module.patterns[(0,ch,0)].items():
                                    if cell.note is not None and cell.note<180:
                                        tick=sum(module.subsongs[0].speeds[r%2] for r in range(row))
                                        expected.append((tick,frequency(cell.note,region,module.tuning)))
                                actual=[(tick,int.from_bytes(values,'little')) for tick,op,slot,values in ops if op==0x11 and slot==ch]
                                self.assertEqual(actual,expected)
                            self.assertEqual([slot for tick,op,slot,_ in ops if tick==0 and op==0x11],list(channels))
    def test_fixed_mask_and_loop_entry(self):
        module,manifest,item=self.fixture((0,2,4))
        item.update(resource_mask=21,loop={'start':[0,0],'end':[0,3]})
        manifest.write_text(json.dumps({'version':1,'sfx':[item]}))
        data,report,_=compile_bank(manifest);effect=report['effects'][0]
        self.assertEqual(effect['offset'],effect['loop']);self.assertEqual(timeline(data[effect['offset']:])[0],11)
        item['resource_mask']=31;manifest.write_text(json.dumps({'version':1,'sfx':[item]}))
        with self.assertRaisesRegex(FormatError,'fixed hardware'):compile_bank(manifest)
        item.pop('resource_mask');item['loop']['start']=[0,1]
        manifest.write_text(json.dumps({'version':1,'sfx':[item]}))
        with self.assertRaisesRegex(FormatError,'fresh note'):compile_bank(manifest)
    def test_independent_patches_volume_pitch_and_cuts(self):
        module,manifest,_=self.fixture((0,4))
        song=module.subsongs[0];song.rows=3;song.speeds=[3]
        voice=bytearray(module.instruments[0]['features']['FM'])
        for op in range(4):voice[9+op*8]|=0x40
        module.instruments[0]['features']['FM']=bytes(voice)
        second=copy.deepcopy(module.instruments[0]);voice=bytearray(second['features']['FM']);voice[6]=60
        second['features']['FM']=bytes(voice);module.instruments.append(second)
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0,volume=100,effects=[(0,4,0x13),(1,0xec,2)]),1:Cell(note=180)},
                         (0,4,0):{0:Cell(note=112,instrument=1,volume=64),1:Cell(volume=32),2:Cell(note=181)}}
        data,report,_=self.compile_module(module,manifest)
        self.assertEqual(report['patches'],2)
        _,ops=timeline(data[report['effects'][0]['offset']:])
        modern,ceil=volume_options(module)
        expected=lambda ins,volume:fm_patch(ins,volume,modern,ceil)[4:8]+fm_patch(ins,volume,modern,ceil)[31:32]
        self.assertIn((0,0x37,0,expected(module.instruments[0],100)),ops)
        self.assertIn((0,0x37,4,expected(second,64)),ops)
        self.assertIn((3,0x37,4,expected(second,32)),ops)
        self.assertIn((2,0x13,0,b''),ops);self.assertIn((6,0x13,4,b''),ops)
        self.assertTrue(any(op==0x36 and slot==0 for _,op,slot,_ in ops))
        self.assertFalse(any(op==0x36 and slot==4 for _,op,slot,_ in ops))
    def test_global_arpeggio_speed_and_whole_effect_terminal(self):
        module,manifest,_=self.fixture((0,4))
        module.patterns[(0,0,0)][0].effects=[(0,0,0x37)]
        module.patterns[(0,4,0)][0].effects=[(0,0xe0,1),(1,255,0)]
        data,report,_=self.compile_module(module,manifest)
        end,ops=timeline(data[report['effects'][0]['offset']:]);self.assertEqual(end,3)
        pitch=[(tick,int.from_bytes(values,'little')) for tick,op,slot,values in ops if op in (0x11,0x36) and slot==0]
        self.assertEqual(pitch,[(i,frequency(note)) for i,note in enumerate((108,111,115))])
    def test_mix_attenuation_covers_patches_and_held_levels(self):
        for channels in ((0,), (0,4)):
            module,manifest,item=self.fixture(channels)
            module.subsongs[0].rows=2;module.subsongs[0].speeds=[3]
            module.instruments[0]['features']['MA']=bytes((8,0,0,3,0,255,0,0,0,1,127,95,63,255))
            for ch in channels:
                module.patterns[(0,ch,0)]={0:Cell(note=108,instrument=0,volume=100),1:Cell(volume=0)}
            plain,plain_report,_=self.compile_module(module,manifest)
            item['fm_attenuation']=8
            manifest.write_text(json.dumps({'version':1,'sfx':[item]}))
            mixed,mixed_report,_=self.compile_module(module,manifest)
            p=int.from_bytes(plain[16:19],'little');q=int.from_bytes(mixed[16:19],'little')
            mask=plain[p+31]
            self.assertTrue(mask)
            expected=lambda tl:bytes(min(127,v+(8 if mask&(1<<i) else 0)) for i,v in enumerate(tl))
            self.assertEqual(mixed[q+4:q+8],expected(plain[p+4:p+8]))
            _,before=timeline(plain[plain_report['effects'][0]['offset']:])
            _,after=timeline(mixed[mixed_report['effects'][0]['offset']:])
            self.assertEqual([(t,op,ch,expected(v[:4])+v[4:] if op==0x37 else v) for t,op,ch,v in before],after)
    def test_invalid_mix_attenuation_rejected(self):
        _,manifest,item=self.fixture((0,))
        for attenuation in (-1,128,True,1.5):
            item['fm_attenuation']=attenuation
            manifest.write_text(json.dumps({'version':1,'sfx':[item]}))
            with self.assertRaisesRegex(FormatError,'FM attenuation'):compile_bank(manifest)
        item['fm_attenuation']=8
        manifest.write_text(json.dumps({'version':1,'songs':[dict(item,voices=[{'channel':0,'slot':'fm0'}])]}))
        with self.assertRaisesRegex(FormatError,'Furnace FM SFX'):compile_bank(manifest)
    def test_fm6_mixed_chips_and_note_less_tracks_rejected(self):
        for channels in ((5,),(0,5),(0,1,2,3,4,5)):
            module,manifest,_=self.fixture(channels)
            with self.assertRaisesRegex(FormatError,'reserved for PCM'):compile_bank(manifest)
        module,_,_=self.fixture((0,1));module.channels[1]=(99,'fm',1)
        with self.assertRaisesRegex(FormatError,'one FM'):infer_sfx(module)
        module.channels[1]=(module.channels[0][0],'psg',1)
        with self.assertRaisesRegex(FormatError,'one FM'):infer_sfx(module)
        module,_,_=self.fixture((0,1))
        for cell in module.patterns[(0,1,0)].values():cell.note=None
        with self.assertRaisesRegex(FormatError,'without notes'):infer_sfx(module)
    def test_single_channel_keeps_eligible_mask(self):
        _,manifest,item=self.fixture((4,));item['resource_mask']=5
        manifest.write_text(json.dumps({'version':1,'sfx':[item]}))
        data,report,_=compile_bank(manifest);effect=report['effects'][0]
        self.assertEqual(effect['kind'],'fm');self.assertEqual(effect['mask'],5)
        self.assertEqual({slot for _,_,slot,_ in timeline(data[effect['offset']:])[1]},{0})


if __name__=='__main__':unittest.main()
