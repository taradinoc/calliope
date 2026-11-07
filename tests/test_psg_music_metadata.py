"""Furnace's disabled editor fields must not alter PSG music playback."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from calliope.compiler import compile_bank
from calliope.furnace import parse,FormatError
from make_fixtures import make

class PsgMusicMetadata(unittest.TestCase):
    def test_disabled_metadata_preserves_tone_and_noise_bank(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source.fur';manifest=root/'bank.json'
            for channel,slot in ((0,'tone0'),(3,'noise')):
                source.write_bytes(make(232,3,notes=(108,109,180),event=False,track_channels=(channel,)))
                module=parse(source)
                manifest.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'psg','source':'source.fur','voices':[{'channel':channel,'slot':slot}]}]}))
                def compile(current):
                    with patch('calliope.compiler.parse',return_value=current):return compile_bank(manifest)[0]
                expected=compile(module)
                for sample in (0,65535):
                    edited=copy.deepcopy(module)
                    edited.instruments[0]['features'].update(SM=sample.to_bytes(2,'little')+bytes((0,31)),NE=bytes((0,)))
                    self.assertEqual(compile(edited),expected)
                for feature,value in (('SM',bytes((0,0,1,31))),('SM',b''),('NE',bytes((1,))),('ZZ',b'')):
                    edited=copy.deepcopy(module);edited.instruments[0]['features'][feature]=value
                    with self.assertRaisesRegex(FormatError,'requires'):compile(edited)
                edited=copy.deepcopy(module);edited.instruments[0]['kind']=1
                with self.assertRaisesRegex(FormatError,'requires'):compile(edited)

if __name__=='__main__':unittest.main()
