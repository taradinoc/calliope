import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from calliope.compiler import compile_bank,load_manifest
from calliope.furnace import Cell,FormatError,parse
from calliope.reference import Player


class MarkerAdvance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'bank.json'
        self.module=parse(ROOT/'examples/fm_ring.fur')
        s=self.module.subsongs[0]
        s.rows=4;s.orders=[[0,1] for _ in self.module.channels];s.speeds=[3]
        self.module.patterns={}
        self.item={'id':1,'name':'advance','source':str(ROOT/'examples/fm_ring.fur'),
                   'voices':[{'channel':0,'slot':'fm0'}],'external_commands':'passthrough'}

    def effect(self,order,row,*effects):
        self.module.patterns[0,0,order]=self.module.patterns.get((0,0,order),{})
        self.module.patterns[0,0,order][row]=Cell(effects=[(i,e,v) for i,(e,v) in enumerate(effects)])

    def compile(self,bank=None,item=None,region='ntsc'):
        self.path.write_text(json.dumps({'version':1,'songs':[dict(self.item,**(item or {}))],**(bank or {})}))
        with patch('calliope.compiler.parse',return_value=self.module):
            return compile_bank(self.path,region)[0]

    def play(self,data,ticks=30,marker=None):
        p=Player(data);p.start(1)
        if marker is not None:p.set_parts(0,16,marker)
        for _ in range(ticks):p.step()
        return p

    def test_cross_order_and_subrow_advance_preserves_repeated_events(self):
        self.effect(1,0,(0xee,7),(0xee,7))
        self.effect(1,1,(0xee,8))
        for region in ('ntsc','pal'):
            p=self.play(self.compile({'marker_advance_ticks':5},region=region))
            self.assertEqual(p.events,[(8,1,7,0),(8,1,7,0),(11,1,8,0)])

    def test_song_override_zero_and_default_bytes(self):
        self.effect(1,0,(0xee,7))
        baseline=self.compile()
        self.assertEqual(baseline,self.compile({'marker_advance_ticks':0}))
        self.assertEqual(baseline,self.compile({'marker_advance_ticks':5},{'marker_advance_ticks':0}))
        p=self.play(self.compile({'marker_advance_ticks':5},{'marker_advance_ticks':2}))
        self.assertEqual(p.events,[(11,1,7,0)])

    def test_clamp_start_preserves_source_order_and_mapping(self):
        self.effect(0,0,(0xee,7));self.effect(0,1,(0xee,8))
        p=self.play(self.compile({'marker_advance_ticks':100},{'events':{'7':{'id':1000,'parameter':42}}}))
        self.assertEqual(p.events,[(1,1,1000,42),(1,1,8,0)])

    def test_loop_cues_remain_in_loop_and_repeat(self):
        self.effect(1,0,(0xee,7));self.effect(1,2,(0xee,8))
        p=self.play(self.compile({'marker_advance_ticks':4},{'loop_order':1}),40)
        self.assertEqual([(e[0],e[2]) for e in p.events],[(13,7),(15,8),(25,7),(27,8),(37,7),(39,8)])

    def test_flow_and_speed_changes_use_resolved_ticks(self):
        self.effect(0,1,(0x0b,1),(0x0d,2),(0x0f,2))
        self.effect(1,0,(0xee,99)) # Skipped by the pattern break.
        self.effect(1,3,(0xee,7),(0xff,0))
        p=self.play(self.compile({'marker_advance_ticks':4}))
        self.assertEqual(p.events,[(4,1,7,0)])

    def test_music_and_arrangement_markers_keep_original_timing(self):
        self.module=parse(ROOT/'examples/fm_ring.fur')
        self.module.patterns[0,0,1][2].effects.append((0,0xee,7))
        item={'markers':[{'order':0,'row':2,'id':42}]}
        baseline=self.play(self.compile(item=item),50)
        advanced=self.play(self.compile({'marker_advance_ticks':5},item),50)
        self.assertEqual(baseline.trace,advanced.trace)
        self.assertEqual(advanced.events[0][0],baseline.events[0][0]-5)
        for settings in ({},{'marker_advance_ticks':5}):
            p=self.play(self.compile(settings,item),8,marker=42)
            self.assertIsNotNone(p.pending_parts)
            p.step()
            self.assertIsNone(p.pending_parts)

    def test_advanced_tick_overflow_is_rejected(self):
        for row in range(4):self.effect(0,row,*[(0xee,7)]*10)
        with self.assertRaisesRegex(FormatError,'marker advance tick exceeds'):
            self.compile({'marker_advance_ticks':100})

    def test_invalid_settings(self):
        for value in (-1,65536,True,1.5,'5',None):
            for bank,item in (({'marker_advance_ticks':value},{}),({}, {'marker_advance_ticks':value})):
                with self.assertRaisesRegex(FormatError,'marker_advance_ticks'):
                    self.compile(bank,item)
        self.path.write_text(json.dumps({'version':1,'sfx':[dict(self.item,marker_advance_ticks=3)]}))
        with self.assertRaisesRegex(FormatError,'only supported on songs'):load_manifest(self.path)


if __name__=='__main__':unittest.main()
