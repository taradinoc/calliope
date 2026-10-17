import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from calliope.sample_voice import SampleVoice
from calliope.furnace import Cell,FormatError,parse
from calliope.compiler import compile_bank

class SampleSelection(unittest.TestCase):
    def module(self):
        return SimpleNamespace(version=232,samples=[{'rate':8000+i,'center_rate':16000+i} for i in range(25)],instruments=[{'kind':4,'features':{'SM':bytes((0,0,0,31))}}])
    def test_legacy_pitch_class_and_bank(self):
        voice=SampleVoice(self.module());voice.begin(Cell(effects=[(0,0x17,1)]),None)
        self.assertEqual(voice.select(108,None),(0,8000))
        self.assertEqual(voice.select(144,None),(0,8000))
        self.assertEqual(voice.select(145,None),(1,8001))
        voice.begin(Cell(effects=[(0,0xeb,2)]),None);self.assertEqual(voice.select(108,None),(24,8024))
        with self.assertRaisesRegex(FormatError,'missing sample'):voice.select(109,None)
    def test_modern_instrument_overrides_legacy_and_uses_pitch(self):
        module=self.module();voice=SampleVoice(module);ins=module.instruments[0]
        voice.begin(Cell(instrument=0,effects=[(0,0x17,1)]),ins)
        self.assertEqual(voice.select(120,ins),(0,32000))
        self.assertTrue(voice.begin(Cell(effects=[(0,0x17,0)]),ins))
        with self.assertRaisesRegex(FormatError,'requires'):voice.select(108,ins)
        module.version=248
        with self.assertRaisesRegex(FormatError,'232'):voice.begin(Cell(effects=[(0,0xeb,0)]),None)
    def test_loop_state_must_reset_before_use(self):
        voice=SampleVoice(self.module());voice.mode=1;voice.bank=1
        with self.assertRaisesRegex(FormatError,'sample mode'):voice.validate_loop((0,0),[Cell(note=108)])
        with self.assertRaisesRegex(FormatError,'sample bank'):voice.validate_loop((1,0),[Cell(note=108)])
        voice.validate_loop((0,0),[Cell(),Cell(effects=[(0,0x17,1),(1,0xeb,0)]),Cell(note=108)])
        voice.mode=2;voice.validate_loop((0,0),[Cell(),Cell(note=108,instrument=0)])
    def test_green_hill_pcm_track_uses_two_unpitched_samples(self):
        source=ROOT/'examples/sonic_greenhillzone_annotated.fur';module=parse(source)
        module.patterns={key:value for key,value in module.patterns.items() if key[1]==5}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[{'id':2,'name':'drums','external_commands':'passthrough','pcm_gain':0.9,'source':str(source),'voices':[{'channel':5,'slot':'pcm'}],'loop_order':0}]}))
            with patch('calliope.compiler.parse',return_value=module):_,report,_=compile_bank(path)
        self.assertEqual(report['sample_variants'],2)
        self.assertEqual({sample['sample'] for sample in report['music_samples']},{0,1})
        self.assertTrue(all(sample['legacy'] and sample['source_rate']==22050 for sample in report['music_samples']))

if __name__=='__main__':unittest.main()
