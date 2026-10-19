import json,sys,tempfile,unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from calliope.furnace import Cell,FormatError,parse
from calliope.panning import linear,split as split_pan,row
from calliope.compiler import compile_bank,fm_patch
from calliope.reference import Player
from calliope.channel_stream import split
from calliope.music_sync import align_tick
from sequencer_study import tokens

class Panning(unittest.TestCase):
    def module(self):
        m=parse(ROOT/'examples/fm_ring.fur');s=m.subsongs[0]
        s.rows=8;s.speeds=[1];s.orders=[[0] for _ in m.channels]
        m.instruments[0]['features'].pop('MA',None)
        # Exercise both AMS and FMS, independently of the stereo bits.
        fm=bytearray(m.instruments[0]['features']['FM']);fm[2]=0x1d
        m.instruments[0]['features']['FM']=bytes(fm)
        m.patterns={(0,0,0):{0:Cell(note=108,instrument=0,effects=[(0,0x80,0)]),
            2:Cell(effects=[(0,0x80,255)]),3:Cell(note=110),
            4:Cell(effects=[(0,0x80,128)]),6:Cell(note=180)}}
        return m

    def compile(self,m,region='ntsc',sfx=False,**extra):
        item={'id':1,'name':'pan','source':str(ROOT/'examples/fm_ring.fur'),**extra}
        if not sfx:item['voices']=[{'channel':0,'slot':'fm0'}]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'sfx' if sfx else 'songs':[item]}))
            with patch('calliope.compiler.parse',return_value=m):return compile_bank(path,region)

    def test_genesis_quantization_and_empty_value(self):
        self.assertEqual([linear(v) for v in (None,0,1,127,128,254,255)],
                         [0x80,0x80,0xc0,0xc0,0xc0,0xc0,0x40])
        self.assertEqual(row(Cell(effects=[(0,0x80,0),(1,0x80,255)])),0x40)
        for value in (-1,256,True):
            with self.assertRaises(FormatError):linear(value)

    def test_split_panning_sides_zero_and_validation(self):
        for left in range(16):
            for right in range(16):
                expected=0xc0 if left==right==0 else (0x80 if left else 0)|(0x40 if right else 0)
                self.assertEqual(split_pan((left<<4)|right),expected)
        self.assertEqual(split_pan(None),0xc0)
        for value in (-1,256,True):
            with self.assertRaisesRegex(FormatError,'08xx'):split_pan(value)

    def test_mixed_panning_columns_last_effect_wins(self):
        self.assertEqual(row(Cell(effects=[(0,0x80,0),(1,0x08,0x0f)])),0x40)
        self.assertEqual(row(Cell(effects=[(0,0x08,0x0f),(1,0x80,0)])),0x80)
        self.assertEqual(row(Cell(effects=[(0,0x80,255),(1,0x08,None)])),0xc0)

    def test_split_panning_reuses_identical_music_and_sfx_commands(self):
        for region in ('ntsc','pal'):
            for sfx,extra in ((False,{}),(False,{'loop_order':0}),
                              (True,{'loop':{'start':[0,0],'end':[0,8]}})):
                m=self.module();baseline,_,_=self.compile(m,region,sfx,**extra)
                for cell in m.patterns[0,0,0].values():
                    cell.effects=[(col,0x08,{0:0xf0,255:0x0f,128:0xff}[value]) for col,effect,value in cell.effects]
                converted,_,_=self.compile(m,region,sfx,**extra)
                self.assertEqual(converted,baseline)

    def test_split_panning_resets_loop_entry_and_survives_patch_change(self):
        m=self.module();index=len(m.instruments);m.instruments.append(deepcopy(m.instruments[0]))
        fm=bytearray(m.instruments[index]['features']['FM']);fm[2]=0
        m.instruments[index]['features']['FM']=bytes(fm)
        m.patterns[0,0,0]={0:Cell(note=108,instrument=0,effects=[(0,0x08,0xf0)]),
            2:Cell(effects=[(0,0x08,0x0f)]),3:Cell(note=110,instrument=index)}
        data,_,_=self.compile(m);p=Player(data);p.start(1)
        for _ in range(8):p.step()
        self.assertIn((4,0x16,0,0x40),p.trace)
        m.subsongs[0].orders=[[0,1] for _ in m.channels]
        m.patterns[0,0,1]={0:Cell(effects=[(0,0x08,0xff)]),1:Cell(effects=[(0,0x08,0x0f)])}
        self.compile(m,loop_order=1)

    def test_music_held_panning_preserves_patch_sensitivity_and_note_timing(self):
        for region in ('ntsc','pal'):
            m=self.module();data,report,_=self.compile(m,region)
            low=fm_patch(m.instruments[0])[29]&0x3f
            p=Player(data);p.start(1)
            for _ in range(8):p.step()
            self.assertEqual([(e[0],e[3]) for e in p.trace if e[1]==0x16],[(1,0x80|low),(3,0x40|low),(5,0xc0|low)])
            self.assertEqual([e[0] for e in p.trace if e[1]==0x11],[1,4])
            self.assertEqual(report['patches'],1)

    def test_patch_change_reapplies_pan(self):
        m=self.module();index=len(m.instruments);m.instruments.append(deepcopy(m.instruments[0]))
        fm=bytearray(m.instruments[index]['features']['FM']);fm[2]=0
        m.instruments[index]['features']['FM']=bytes(fm)
        m.patterns[0,0,0][3]=Cell(note=110,instrument=index)
        data,_,_=self.compile(m);p=Player(data);p.start(1)
        for _ in range(8):p.step()
        self.assertIn((4,0x16,0,0x40),p.trace)

    def test_panning_before_first_instrument_and_loop_restart(self):
        m=self.module();m.patterns[0,0,0][0]=Cell(effects=[(0,0x80,255)])
        m.patterns[0,0,0][1]=Cell(note=108,instrument=0)
        data,_,_=self.compile(m);p=Player(data);p.start(1);p.step();p.step()
        self.assertEqual(p.voices[0]['pan'],fm_patch(m.instruments[0])[29]&0x3f|0x40)
        # A patch at loop entry restores the first-pass pan explicitly.
        data,_,_=self.compile(self.module(),loop_order=0);p=Player(data);p.start(1)
        for _ in range(17):p.step()
        self.assertEqual([e[0] for e in p.trace if e[1]==0x16 and e[3]&0xc0==0x80],[1,9,17])

    def test_loop_held_entry_must_reset_changed_pan(self):
        m=self.module();m.subsongs[0].orders=[[0,1] for _ in m.channels]
        m.patterns[0,0,1]={0:Cell(),1:Cell(effects=[(0,0x80,255)])}
        with self.assertRaisesRegex(FormatError,'reset changed FM panning'):self.compile(m,loop_order=1)

    def test_sfx_loop_retains_pan_operations(self):
        data,report,_=self.compile(self.module(),sfx=True,loop={'start':[0,0],'end':[0,8]})
        e=report['effects'][0];stream=data[e['offset']:data.index(b'\xff',e['offset'])]
        self.assertEqual(stream.count(bytes((0x16,0))),3)
        self.assertEqual(e['loop'],e['offset'])

    def test_pcm_alignment_keeps_pan_before_note(self):
        aligned=align_tick(bytes((0x14,0,0x10,0,0,0x16,0,0x85,0x11,0,0x34,0x12,0x14,1,0x31,1,2,3)))
        conductor,fm,pcm=split(aligned,2,0xff,True)
        self.assertEqual(tokens(fm)[:3],(bytes((0x10,0)),bytes((0x16,0x85)),bytes((0x11,0x34,0x12))))
        self.assertEqual(tokens(pcm)[0],bytes((0x31,1,2,3)))

    def test_psg_panning_is_a_noop_and_pcm_reports_its_limit(self):
        from calliope.psg import compile_tone
        m=parse(ROOT/'examples/psg_ring.fur');ch=6
        before=compile_tone(m,0,ch,'ntsc','psg')
        for effect in (0x80,0x08):
            for pattern in m.patterns.values():
                for cell in pattern.values():cell.effects.append((1,effect,255))
            self.assertEqual(compile_tone(m,0,ch,'ntsc','psg'),before)
        m=parse(ROOT/'examples/sonic_greenhillzone_annotated.fur');s=m.subsongs[0]
        s.rows=1;s.speeds=[1];s.orders=[[0] for _ in m.channels]
        m.patterns={(0,5,0):{0:Cell(effects=[(0,0x80,128)])}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'pcm','source':str(ROOT/'examples/sonic_greenhillzone_annotated.fur'),'voices':[{'channel':5,'slot':'pcm'}]}]}))
            with patch('calliope.compiler.parse',return_value=m):
                for effect in (0x80,0x08):
                    m.patterns[0,5,0][0].effects=[(0,effect,128)]
                    with self.assertRaisesRegex(FormatError,f'{effect:02X}xx.*shared mono PCM mixer'):compile_bank(path)

if __name__=='__main__':unittest.main()
