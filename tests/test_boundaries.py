import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from calliope.compiler import compile_bank
from calliope.furnace import Cell,parse
from calliope.reference import Player
from sequencer_study import tokens


class Boundaries(unittest.TestCase):
    def compile_sparse(self,highlights=(1,20)):
        source=ROOT/'examples/arrangements232.fur'
        module=parse(source);song=module.subsongs[0]
        song.rows=64;song.speeds=[6];song.highlights=highlights
        song.orders=[[0,1] for _ in module.channels]
        module.instruments[0]['features'].pop('MA',None)
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0),5:Cell(effects=[(0,0xec,3)])},
                         (0,0,1):{0:Cell(note=109,instrument=0)}}
        item={'id':1,'name':'sparse','source':str(source),
              'voices':[{'channel':0,'slot':'fm0','name':'melody'}],
              'groups':{'all':['melody']},
              'markers':[{'order':1,'row':5,'id':0},{'order':1,'row':16,'id':7}]}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[item]}))
            with patch('calliope.compiler.parse',return_value=module):
                return compile_bank(path)[0]

    def test_major_highlight_spans_orders_and_preserves_marker_and_cut_ticks(self):
        class Recorded(Player):
            def boundary(self,flags,marker=0):
                if flags&24:self.boundaries.append((self.tick,flags,marker))
                super().boundary(flags,marker)
        data=self.compile_sparse();p=Recorded(data);p.boundaries=[];p.start(1)
        for _ in range(769):p.step()
        expected=[(1+row*6,8,0) for row in (0,20,40,60,80,100,120)]
        expected.remove((481,8,0));expected.extend(((415,16,0),(481,24,7)))
        self.assertEqual(p.boundaries,sorted(expected))
        self.assertFalse(p.playing);self.assertEqual(p.tick,769)
        self.assertTrue(any(t[0]==34 and t[1]==0x13 for t in p.trace))
        self.assertEqual([t[0] for t in p.trace if t[1]==0x11],[1,385])
        table=int.from_bytes(data[int.from_bytes(data[10:13],'little')+4:][:3],'little')
        streams=[]
        for i in range(2):
            record=int.from_bytes(data[table+i*3:table+i*3+3],'little')
            streams.append(tokens(data,int.from_bytes(data[record:record+3],'little')))
        waits=[t for stream in streams for t in stream if t[0]==0 or 0x80<=t[0]<0xfd]
        self.assertLess(len(waits),128)
        self.assertEqual(sum(t[1] if t[0]==0 else t[0]-0x7f for t in waits),768)

    def test_disabled_major_highlight_keeps_only_explicit_markers(self):
        data=self.compile_sparse((1,0));p=Player(data);p.start(1)
        p.set_parts(0,8)
        for _ in range(769):p.step()
        self.assertTrue(p.parts[0]['enabled'])
        self.assertIsNotNone(p.pending_parts)
        descriptor=int.from_bytes(data[10:13],'little');table=int.from_bytes(data[descriptor+4:descriptor+7],'little')
        boundaries=[]
        for i in range(2):
            record=int.from_bytes(data[table+i*3:table+i*3+3],'little')
            boundaries.extend(t for t in tokens(data,int.from_bytes(data[record:record+3],'little')) if t[0]==0x15)
        self.assertEqual([t[1] for t in boundaries],[16,16])

    def test_immediate_applies_without_advancing_music_and_fine_boundaries_reject(self):
        data,_,_=compile_bank(ROOT/'examples/bank.json')
        for boundary in (1,):
            p=Player(data);p.start(3);p.step();tick=p.tick
            p.select_arrangement(1,boundary)
            self.assertEqual(p.tick,tick)
            self.assertEqual([v['enabled'] for v in p.parts],[False,True,True])
            self.assertIsNone(p.pending_parts)
            for unsupported in (2,4):
                with self.assertRaisesRegex(ValueError,'invalid boundary'):p.set_parts(4,unsupported)

    def test_immediate_request_supersedes_pending_bar(self):
        data,_,_=compile_bank(ROOT/'examples/bank.json');p=Player(data);p.start(3);p.step()
        p.select_arrangement(1,8);self.assertIsNotNone(p.pending_parts)
        p.set_parts(4)
        self.assertIsNone(p.pending_parts)
        while p.tick<100:p.step()
        self.assertEqual([v['enabled'] for v in p.parts],[False,False,True])


if __name__=='__main__':unittest.main()
