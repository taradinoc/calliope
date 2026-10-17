import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from calliope.note_timing import cuts,fm_delay
from calliope.furnace import Cell,FormatError,parse
from calliope.fm import Pitch
from calliope.psg import VolumeEnvelope
from calliope.compiler import compile_bank
from calliope.reference import Player

class NoteTiming(unittest.TestCase):
    def compile_fm(self,rows,category='songs',voices=1,macro=None):
        module=parse(ROOT/'examples/fm_ring.fur');song=module.subsongs[0]
        song.rows=3;song.speeds=[6];song.orders=[[0] for _ in module.channels]
        module.patterns={(0,ch,0):cells for ch,cells in enumerate(rows)}
        if macro is not None:module.instruments[0]['features']['MA']=macro
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json'
            entry={'id':1,'name':'delay','source':str(ROOT/'examples/fm_ring.fur'),
                   'voices':[{'channel':ch,'slot':'fm'+str(ch)} for ch in range(voices)]}
            path.write_text(json.dumps({'version':1,category:[entry]}))
            with patch('calliope.compiler.parse',return_value=module):return compile_bank(path)

    def test_fm_delay_policies(self):
        for policy in (0,1,2):
            self.assertEqual(fm_delay(Cell(effects=[(0,0xed,0)]),6,policy),0)
            self.assertEqual(fm_delay(Cell(effects=[(0,0xed,5)]),6,policy),5)
        self.assertEqual(fm_delay(Cell(effects=[(0,0xed,6)]),6,0),0)
        self.assertEqual(fm_delay(Cell(effects=[(0,0xed,7)]),6,1),0)
        self.assertEqual(fm_delay(Cell(effects=[(0,0xed,6)]),6,2),6)
        with self.assertRaisesRegex(FormatError,'cross-row'):
            fm_delay(Cell(effects=[(0,0xed,7)]),6,2)

    def test_fm_delay_moves_patch_note_and_other_row_effects(self):
        rows=[{0:Cell(note=108,instrument=0,volume=100,effects=[(0,0xed,2),(1,0x13,20),(2,0x08,0x10)]),
               1:Cell(note=180,effects=[(0,0xed,3)])},
              {0:Cell(note=112,instrument=0)}]
        data,_,_=self.compile_fm(rows,voices=2)
        player=Player(data);player.start(1)
        for _ in range(18):player.step()
        notes=[(e[0],e[2]) for e in player.trace if e[1]==0x11]
        self.assertEqual(notes,[(1,1),(3,0)])
        self.assertEqual([e[0] for e in player.trace if e[1]==0x13],[10])
        for op in (0x10,0x16,0x37):
            changes=[e for e in player.trace if e[1]==op and e[2]==0]
            self.assertEqual(changes[0][0],3)

    def test_fm_delayed_release_at_row_boundary(self):
        rows=[{0:Cell(note=108,instrument=0,effects=[(0,0xed,2)]),
               1:Cell(note=181,instrument=0,volume=90,effects=[(0,0xed,6)]),
               2:Cell(note=112)}]
        data,_,_=self.compile_fm(rows)
        player=Player(data);player.start(1)
        for _ in range(18):player.step()
        self.assertEqual([e[0] for e in player.trace if e[1]==0x11],[3,13])
        self.assertEqual([e[0] for e in player.trace if e[1]==0x13],[13])

    def test_fm_delay_in_sfx_and_relative_cut(self):
        rows=[{0:Cell(note=108,instrument=0,effects=[(0,0xed,2),(1,0xec,3)])}]
        from test_fm_sfx import timeline
        data,report,_=self.compile_fm(rows,category='sfx')
        _,operations=timeline(data[report['effects'][0]['offset']:])
        self.assertEqual([(e[0],e[1]) for e in operations if e[1] in (0x11,0x13)],[(2,0x11),(5,0x13)])
        rows[0][0].effects=[(0,0xed,4),(1,0xec,3)]
        with self.assertRaisesRegex(FormatError,'cuts/releases must stay within'):
            self.compile_fm(rows)

    def test_fm_delay_keeps_old_macro_running_then_restarts_it(self):
        # Three levels loop forever. A delayed note must not restart it early.
        macro=bytes((8,0,0,3,0,255,0,0,0,1,127,95,63,255))
        traces=[]
        for delayed in (False,True):
            rows=[{0:Cell(note=108,instrument=0),
                   1:Cell(note=112,effects=[(0,0xed,2)]) if delayed else Cell()}]
            data,_,_=self.compile_fm(rows,macro=macro)
            player=Player(data);player.start(1)
            for _ in range(18):player.step()
            traces.append([e for e in player.trace if e[1]==0x37])
        self.assertEqual([e for e in traces[0] if e[0]<9],[e for e in traces[1] if e[0]<9])
        self.assertEqual(next(e for e in traces[1] if e[0]==9)[3:],traces[1][0][3:])

    def test_zero_cut_and_zero_release(self):
        self.assertEqual(cuts(Cell(effects=[(0,0xec,0)]),6,0),{})
        self.assertEqual(cuts(Cell(effects=[(0,0xfc,0)]),6,0),{0:181})
    def test_bounds_and_last_valid_column(self):
        cell=Cell(effects=[(0,0xec,2),(1,0xfc,3),(2,0xec,0)])
        self.assertEqual(cuts(cell,6,0),{3:181})
        self.assertEqual(cuts(Cell(effects=[(0,0xec,6)]),6,0),{})
        with self.assertRaisesRegex(FormatError,'cross-row'):cuts(Cell(effects=[(0,0xec,6)]),6,2)
    def test_cut_stops_macro_release_advances_it(self):
        ins={'kind':0,'features':{'MA':bytes((8,0,0,3,0,1,0,8,0,1,15,7,0,255))}}
        envelope=VolumeEnvelope();envelope.begin(Cell(note=108),ins)
        self.assertEqual(envelope.tick(),0);self.assertEqual(envelope.tick(181),8)
        self.assertEqual(envelope.tick(),15)
        envelope=VolumeEnvelope();envelope.begin(Cell(note=108),ins);envelope.tick()
        self.assertEqual(envelope.tick(180),15);self.assertFalse(envelope.active)
        pitch=Pitch(True,'ntsc',440);pitch.begin(Cell(note=108),None);pitch.tick(180)
        self.assertFalse(pitch.gate)
    def test_pcm_cut_emits_stop_at_tick(self):
        source=ROOT/'examples/sonic_greenhillzone_annotated.fur';module=parse(source);song=module.subsongs[0]
        song.rows=2;song.speeds=[6];song.orders=[[0] for _ in module.channels]
        module.patterns={(0,5,0):{0:Cell(note=144,effects=[(0,0x17,1),(1,0xec,3)])}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'cut','source':str(source),'pcm_gain':0.9,'voices':[{'channel':5,'slot':'pcm'}]}]}))
            with patch('calliope.compiler.parse',return_value=module):data,_,_=compile_bank(path)
        player=Player(data);player.start(1)
        for _ in range(12):player.step()
        self.assertEqual([(e[0],e[1]) for e in player.trace if e[1] in (0x31,0x32)],[(1,0x31),(4,0x32)])

    def test_pcm_delay_emits_start_at_tick(self):
        source=ROOT/'examples/sonic_greenhillzone_annotated.fur';module=parse(source);song=module.subsongs[0]
        song.rows=2;song.speeds=[6];song.orders=[[0] for _ in module.channels]
        module.patterns={(0,5,0):{0:Cell(note=144,effects=[(0,0x17,1),(1,0xed,2)])}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'cut','source':str(source),'pcm_gain':0.9,'voices':[{'channel':5,'slot':'pcm'}]}]}))
            with patch('calliope.compiler.parse',return_value=module):data,_,_=compile_bank(path)
        player=Player(data);player.start(1)
        for _ in range(12):player.step()
        self.assertEqual([(e[0],e[1]) for e in player.trace if e[1] in (0x31,0x32)],[(3,0x31)])

    def test_fm_cut_releases_gate_at_authored_tick(self):
        module=parse(ROOT/'examples/fm_ring.fur');song=module.subsongs[0]
        song.rows=2;song.speeds=[6];song.orders=[[0] for _ in module.channels]
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0,effects=[(0,0xec,3)]),1:Cell(note=112,effects=[(0,0xfc,0)])}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'cut','source':str(ROOT/'examples/fm_ring.fur'),'voices':[{'channel':0,'slot':'fm0'}]}]}))
            with patch('calliope.compiler.parse',return_value=module):data,_,_=compile_bank(path)
        player=Player(data);player.start(1)
        for _ in range(12):player.step()
        self.assertEqual([e[0] for e in player.trace if e[1]==0x11],[1,7])
        self.assertEqual([e[0] for e in player.trace if e[1]==0x13],[4,7])
        self.assertIsNone(player.voices[0]['note'])

if __name__=='__main__':unittest.main()
