import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from calliope.arpeggio import Arpeggio,speed
from calliope.furnace import Cell,FormatError,parse
from calliope.fm import Pitch,frequency
from calliope.psg import VolumeEnvelope
from calliope.compiler import compile_bank
from calliope.reference import Player

class TrackerArpeggio(unittest.TestCase):
    def test_speed_stage_and_stop(self):
        arp=Arpeggio();arp.period=2;arp.begin(Cell(note=108,effects=[(0,0,0x37)]))
        self.assertEqual([arp.tick(108) for _ in range(6)],[108,None,111,None,115,None])
        arp.begin(Cell(effects=[(0,0,0)]));self.assertEqual(arp.tick(108),108);self.assertIsNone(arp.tick(108))
        self.assertEqual(speed(2,[Cell(effects=[(0,0xe0,3)]),Cell(effects=[(0,0xe0,4),(1,0xe0,0)])]),4)
    def test_persistent_phase_and_compatible_yield(self):
        arp=Arpeggio({'compatibleArpeggio':True});arp.begin(Cell(note=108,effects=[(0,0,0x37)]))
        self.assertEqual(arp.tick(108),108)
        arp.begin(Cell(note=120));self.assertIsNone(arp.tick(120));self.assertEqual(arp.tick(120),123)
        arp.begin(Cell());self.assertEqual(arp.tick(120),127)
    def test_note_row_and_keyoff_resets(self):
        arp=Arpeggio({'resetArpPhaseOnNewNote':True});arp.begin(Cell(note=108,effects=[(0,0,0x37)]));arp.tick(108);arp.tick(108)
        arp.begin(Cell(note=120));self.assertEqual(arp.tick(120),120)
        arp=Arpeggio({'rowResetsArpPos':True});arp.begin(Cell(effects=[(0,0,0x37)]));arp.tick(108);arp.tick(108)
        arp.begin(Cell());self.assertEqual(arp.tick(108),108)
        arp=Arpeggio(keyoff_resets=True);arp.begin(Cell(note=108,effects=[(0,0,0x37)]));arp.tick(108)
        arp.begin(Cell(note=180));arp.begin(Cell(note=120));self.assertEqual(arp.value,0)
    def test_slide_suspends_countdown(self):
        arp=Arpeggio();arp.begin(Cell(effects=[(0,0,0x37)]));before=arp.state()
        self.assertIsNone(arp.tick(108,True));self.assertEqual(arp.state(),before)
        self.assertEqual(arp.tick(108),108)
    def test_fm_and_psg_use_non_acquiring_pitch(self):
        pitch=Pitch(True,'ntsc',440);pitch.begin(Cell(note=108,effects=[(0,0,0x37)]),None)
        self.assertEqual([pitch.tick() for _ in range(4)],[frequency(n) for n in (108,111,115,108)])
        envelope=VolumeEnvelope(True);envelope.begin(Cell(note=108,effects=[(0,0,0x37)]),None)
        notes=[]
        for _ in range(4):envelope.tick();notes.append(envelope.pitch_note)
        self.assertEqual(notes,[108,111,115,108])
    def test_loop_phase_validation(self):
        arp=Arpeggio();initial=arp.state();entry=Cell(note=108,effects=[(0,0,0x37)])
        arp.begin(entry);arp.tick(108)
        with self.assertRaisesRegex(FormatError,'phase'):arp.validate_loop(initial,entry,True)
        arp.tick(108);arp.tick(108);arp.validate_loop(initial,entry,True)
    def test_loop_must_not_change_compatible_first_note_yield(self):
        arp=Arpeggio({'compatibleArpeggio':True});initial=arp.state()
        entry=Cell(note=108,effects=[(0,0,0x37)]);arp.begin(entry)
        for _ in range(3):arp.tick(108)
        with self.assertRaisesRegex(FormatError,'first-note'):arp.validate_loop(initial,entry,True)

    def test_global_speed_from_later_channel(self):
        module=parse(ROOT/'examples/fm_ring.fur');song=module.subsongs[0]
        song.rows=3;song.speeds=[3];song.arp_speed=1;song.orders=[[0] for _ in module.channels]
        module.flags['compat_legacy'][7]=0;module.flags['compat_extended'][2]=0;module.flags['compat_more'][4]=0
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0,effects=[(0,0,0x37)])},(0,1,0):{0:Cell(effects=[(0,0xe0,2)])}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'arp','source':str(ROOT/'examples/fm_ring.fur'),'voices':[{'channel':0,'slot':'fm0'},{'channel':1,'slot':'fm1'}]}]}))
            with patch('calliope.compiler.parse',return_value=module):data,_,_=compile_bank(path)
        player=Player(data);player.start(1)
        for _ in range(9):player.step()
        self.assertEqual([e[0] for e in player.trace if e[1]==0x36],[3,5,7,9])
        self.assertEqual(len([e for e in player.trace if e[1]==0x11]),1)

if __name__=='__main__':unittest.main()
