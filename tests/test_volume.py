import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from calliope.volume import Slide,validate_loop_entry
from calliope.furnace import Cell,FormatError,parse
from calliope.compiler import compile_bank,fm_patch
from calliope.reference import Player
from calliope.psg import VolumeEnvelope

class VolumeSlides(unittest.TestCase):
    def test_fractional_persistence_and_cancel(self):
        slide=Slide(127);slide.begin(Cell(volume=64,effects=[(0,0x0a,0x10)]))
        self.assertEqual([slide.tick() for _ in range(4)],[64,64,64,65])
        slide.begin(Cell());self.assertEqual([slide.tick() for _ in range(4)],[65,65,65,66])
        slide.begin(Cell(effects=[(0,0x0a,0)]));self.assertEqual(slide.tick(),66)
        self.assertEqual(slide.speed,0)
    def test_down_nibble_precedence_and_explicit_volume(self):
        slide=Slide(15);slide.begin(Cell(volume=8,effects=[(0,0x0a,0xf1)]))
        self.assertEqual([slide.tick() for _ in range(5)],[7,7,7,7,6])
        slide.begin(Cell(volume=10));self.assertEqual(slide.tick(),9)
    def test_skip_first_and_saturation(self):
        slide=Slide(15,{'skip_first':True});slide.begin(Cell(volume=1,effects=[(0,0x0a,15)]))
        self.assertEqual([slide.tick(),slide.tick()],[1,0]);self.assertEqual(slide.speed,0)
        slide=Slide(15,{'keep_speed':True});slide.begin(Cell(volume=15,effects=[(0,0x0a,0xf0)]))
        self.assertEqual(slide.tick(),15);self.assertEqual(slide.speed,960)
    def test_legacy_and_loop_validation(self):
        with self.assertRaisesRegex(FormatError,'legacy volume'):
            Slide(15,{'legacy':True}).begin(Cell(effects=[(0,0x0a,1)]))
        with self.assertRaisesRegex(FormatError,'loop entry'):
            validate_loop_entry(Cell(volume=15),{0x0a})
        validate_loop_entry(Cell(volume=15,effects=[(0,0x0a,0)]),{0x0a})
    def test_psg_slide_changes_no_pitch_or_gate(self):
        envelope=VolumeEnvelope(True);envelope.begin(Cell(note=108,volume=8,effects=[(0,0x0a,4)]),None)
        self.assertEqual(envelope.tick(),8);self.assertTrue(envelope.pitch_changed)
        self.assertEqual(envelope.tick(),9);self.assertFalse(envelope.pitch_changed)
        envelope.begin(Cell(),None);self.assertEqual(envelope.tick(),10);self.assertTrue(envelope.active)
    def test_noise_and_coupled_slides_share_volume_semantics(self):
        from calliope.psg import compile_noise
        for name,channels,mode in (('noise232',[3],1),('coupled232',[2,3],0x11)):
            module=parse(ROOT/'examples'/f'{name}.fur');song=module.subsongs[0]
            module.flags['compat_legacy'][6]=0;module.flags['compat_extended'][1]=0
            song.rows=4;song.speeds=[2];song.orders=[[0] for _ in module.channels]
            module.instruments=[{'kind':0,'features':{}}]
            module.patterns={(0,3,0):{0:Cell(note=108,instrument=0,volume=8,effects=[(0,0x20,mode),(1,0x0a,4)]),3:Cell(effects=[(0,0x0a,0)])}}
            if len(channels)==2:module.patterns[(0,2,0)]={0:Cell(note=108,instrument=0,volume=15)}
            stream=compile_noise(module,0,channels,'ntsc','noise');position=0;levels=[];controls=[]
            while stream[position]!=255:
                op=stream[position];position+=1
                if op==0:position+=1;continue
                count=stream[position];position+=1
                for value in stream[position:position+count]:
                    if value&0xf0==0xf0:levels.append(value&15)
                    if value&0xf0==0xe0:controls.append(value)
                position+=count
            self.assertEqual(levels,[8,9,10,11,12,13])
            self.assertEqual(controls,[0xe7 if mode&0x10 else 0xe6])

    def test_noise_macro_delay_and_speed(self):
        from calliope.psg import compile_noise
        module=parse(ROOT/'examples/noise232.fur');song=module.subsongs[0]
        song.rows=4;song.speeds=[2];song.orders=[[0] for _ in module.channels]
        module.instruments=[{'kind':0,'features':{'MA':bytes((8,0,0,2,0,255,0,0,2,2,15,7,255))}}]
        module.patterns={(0,3,0):{0:Cell(note=108,instrument=0,volume=15,effects=[(0,0x20,1)])}}
        stream=compile_noise(module,0,[3],'ntsc','noise');position=0;tick=0;levels=[]
        while stream[position]!=255:
            op=stream[position];position+=1
            if op==0:tick+=stream[position];position+=1;continue
            count=stream[position];position+=1
            levels.extend((tick,v&15) for v in stream[position:position+count] if v&0xf0==0xf0)
            position+=count
        self.assertEqual(levels,[(0,0),(3,8),(5,0),(7,8)])

    def test_compiled_fm_slide_keeps_one_patch_and_one_key_on(self):
        module=parse(ROOT/'examples/fm_ring.fur');song=module.subsongs[0]
        module.flags['compat_legacy'][6]=0;module.flags['compat_extended'][1]=0
        song.rows=3;song.speeds=[4];song.orders=[[0] for _ in module.channels]
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0,volume=64,effects=[(0,0x0a,0x10)]),2:Cell(effects=[(0,0x0a,0)])}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'slide','source':str(ROOT/'examples/fm_ring.fur'),'voices':[{'channel':0,'slot':'fm0'}]}]}))
            with patch('calliope.compiler.parse',return_value=module):data,report,_=compile_bank(path)
        player=Player(data);player.start(1)
        for _ in range(12):player.step()
        self.assertEqual(report['patches'],1)
        self.assertEqual(len([e for e in player.trace if e[1]==0x11]),1)
        levels=[e for e in player.trace if e[1]==0x37]
        self.assertEqual([e[0] for e in levels],[1,4,8])
        modern=bool(module.flags['compat_extended'][16]);ceil=bool(module.flags['compat_more'][5])
        for event,volume in zip(levels,(64,65,66)):
            expected=fm_patch(module.instruments[0],volume,modern,ceil)
            self.assertEqual(event[3],expected[4:8]+expected[31:32])

if __name__=='__main__':unittest.main()
