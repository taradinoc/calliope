"""Multi-channel PSG compilation and fixed-resource descriptors."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from calliope.compiler import compile_bank,infer_sfx
from calliope.furnace import parse,FormatError
from calliope.psg import compile_psg,compile_tone
from make_fixtures import make


def timeline(stream):
    tick=0;p=0;writes=[]
    while stream[p]!=255:
        op=stream[p];p+=1
        if op==0:tick+=stream[p];p+=1
        else:
            assert op==0x30
            count=stream[p];p+=1
            writes.append((tick,bytes(stream[p:p+count])));p+=count
    return tick,writes


class MultiPsg(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def fixture(self,channels,version=248,noise_mode=1,speeds=(3,5)):
        notes=(108,109,110)
        source=self.root/'effect.fur'
        source.write_bytes(make(version,3,notes=notes,event=False,
            extra_tracks=tuple((notes,f'voice{i}') for i in range(1,len(channels))),
            track_channels=channels,initial_effects={3:(0x20,noise_mode)} if 3 in channels else {},speeds=speeds))
        item={'id':9,'name':'multi','source':source.name,'priority':2}
        manifest=self.root/'bank.json'
        manifest.write_text(json.dumps({'version':1,'sfx':[item]}))
        return parse(source),manifest,item
    def test_channel_combinations_and_physical_writes(self):
        for version in (232,248):
            for channels in ((0,1),(0,2),(1,2),(0,3),(1,3),(0,1,2),(0,1,3),(0,2,3),(1,2,3),(0,1,2,3)):
                with self.subTest(version=version,channels=channels):
                    module,manifest,_=self.fixture(channels,version)
                    self.assertEqual(infer_sfx(module),('multi_psg',list(channels)))
                    for region in ('ntsc','pal'):
                        data,report,_=compile_bank(manifest,region)
                        effect=report['effects'][0];desc=int.from_bytes(data[13:16],'little')
                        self.assertEqual(data[desc+2],6)
                        self.assertEqual(data[desc+7],sum(1<<ch for ch in channels))
                        actual=timeline(data[effect['offset']:])
                        expected=[];ends=[]
                        for ch in channels:
                            stream=compile_tone(module,0,ch,region,'expected',ch==3,2 in channels,channels)
                            end,writes=timeline(stream);ends.append(end)
                            expected.extend((tick,bytes(v|(ch<<5) if v&128 else v for v in values) if ch<3 else values) for tick,values in writes)
                        self.assertEqual(actual,(min(ends),sorted(expected,key=lambda w:w[0])))
    def test_explicit_mask_and_loop(self):
        module,manifest,item=self.fixture((0,2,3))
        item.update(resource_mask=416,loop={'start':[0,0],'end':[0,3]})
        manifest.write_text(json.dumps({'version':1,'sfx':[item]}))
        data,report,_=compile_bank(manifest)
        self.assertEqual(report['effects'][0]['loop'],report['effects'][0]['offset'])
        self.assertEqual(timeline(data[report['effects'][0]['offset']:])[0],11)
        item['resource_mask']=224;manifest.write_text(json.dumps({'version':1,'sfx':[item]}))
        with self.assertRaisesRegex(FormatError,'fixed hardware'):compile_bank(manifest)
        item.pop('resource_mask');item['loop']['start']=[0,1]
        manifest.write_text(json.dumps({'version':1,'sfx':[item]}))
        with self.assertRaisesRegex(FormatError,'fresh note'):compile_bank(manifest)
    def test_derived_noise_requires_tone3(self):
        module,_,_=self.fixture((0,3),noise_mode=0x11)
        with self.assertRaisesRegex(FormatError,'tone-derived'):compile_psg(module,0,[0,3],'ntsc','bad')
        module,_,_=self.fixture((0,2,3),noise_mode=0x11)
        _,writes=timeline(compile_psg(module,0,[0,2,3],'ntsc','derived'))
        first=[values for tick,values in writes if tick==0]
        self.assertIn(bytes((0xe7,)),first)
        self.assertIn(bytes((0xdf,)),first)
        self.assertEqual(first[-1][0]&0xf0,0xf0)
    def test_global_arpeggio_speed_from_other_channel(self):
        module,manifest,_=self.fixture((0,1))
        module.patterns[(0,0,0)][0].effects=[(0,0,0x37)]
        module.patterns[(0,1,0)][0].effects=[(0,0xe0,1)]
        with patch('calliope.compiler.parse',return_value=module):
            data,report,_=compile_bank(manifest)
        _,writes=timeline(data[report['effects'][0]['offset']:])
        tone0=[tick for tick,values in writes if values[0]&0xf0==0x80]
        self.assertEqual(tone0[:3],[0,1,2])
    def test_first_terminal_row_ends_whole_effect(self):
        module,_,_=self.fixture((0,1))
        module.patterns[(0,0,0)][0].effects=[(0,255,0)]
        end,writes=timeline(compile_psg(module,0,[0,1],'ntsc','terminal'))
        self.assertEqual(end,3)
        self.assertTrue(all(tick<3 for tick,_ in writes))
    def test_reject_mixed_chips_and_note_less_tracks(self):
        module,_,_=self.fixture((0,1))
        module.channels[1]=(99,'psg',1)
        with self.assertRaisesRegex(FormatError,'one PSG'):infer_sfx(module)
        module.channels[1]=(module.channels[0][0],'fm',1)
        with self.assertRaisesRegex(FormatError,'one PSG'):infer_sfx(module)
        module,_,_=self.fixture((0,1))
        for cell in module.patterns[(0,1,0)].values():cell.note=None
        with self.assertRaisesRegex(FormatError,'without notes'):infer_sfx(module)


if __name__=='__main__':unittest.main()
