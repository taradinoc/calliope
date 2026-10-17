import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from calliope.macros import sequences,Cursor,Sequence,full_linear_pitch
from calliope.psg import VolumeEnvelope
from calliope.furnace import Cell,FormatError,parse
from calliope.compiler import compile_bank
from calliope.reference import Player

def instrument(*macros):
    data=bytearray((8,0))
    for code,values,mode,flags,delay,speed,loop,release in macros:
        data.extend((code,len(values),loop,release,mode,flags,delay,speed))
        size=(1,1,2,4)[flags>>6]
        for v in values:data.extend(v.to_bytes(size,'little',signed=bool(flags>>6)))
    data.append(255);return {'kind':0,'features':{'MA':bytes(data)}}

class Macros(unittest.TestCase):
    def test_format_specific_linearity(self):
        from types import SimpleNamespace
        for value in (0,1,2):
            self.assertEqual(full_linear_pitch(SimpleNamespace(version=232,flags={'compat_legacy':[0,value]})),value==2)
        for value in (0,1):
            encoded=f'linearPitch={value}\n\0'.encode().hex()
            self.assertEqual(full_linear_pitch(SimpleNamespace(version=248,flags={'compat':[encoded]})),bool(value))
        self.assertTrue(full_linear_pitch(SimpleNamespace(version=248,flags={})))

    def test_signed_width_and_bounds(self):
        for flags in (64,128,192):
            ins=instrument((1,[-12,0,12],0,flags,0,1,255,255))
            self.assertEqual(sequences(ins)[1].values,(-12,0,12))
        for ins in (instrument((1,[97],0,0,0,1,255,255)),instrument((4,[0],0,2,0,1,255,255)),instrument((0,[16],0,0,0,1,255,255)),instrument((0,[0],0,0,0,0,255,255))):
            with self.assertRaises(FormatError):sequences(ins)
    def test_delay_speed_release(self):
        c=Cursor(Sequence((15,10,5),255,255,0,2,2,False))
        self.assertEqual([c.step(False) for _ in range(7)],[15,None,None,10,None,5,None])
        c=Cursor(Sequence((15,12,8,0),0,2,0,0,1,False))
        self.assertEqual([c.step(False) for _ in range(5)],[15,12,8,15,12])
        self.assertEqual([c.step(True) for _ in range(3)],[8,0,None])
        c=Cursor(Sequence((15,12,8,0),0,2,0,9,1,True))
        self.assertEqual(c.step(False),15);self.assertEqual(c.step(True),8)
    def test_pitch_accumulation_release_and_reset(self):
        ins=instrument((1,[0,12],0,64,0,1,255,255),(4,[32,32,-32],1,64,0,1,255,255))
        env=VolumeEnvelope(True);env.begin(Cell(note=108,instrument=0),ins)
        notes=[]
        for _ in range(3):env.tick();notes.append(env.pitch_note)
        self.assertEqual(notes,[108.25,120.5,120.25])
        env.begin(Cell(note=180),ins);env.tick();self.assertFalse(env.active)
        env.begin(Cell(note=96),ins);env.tick();self.assertEqual(env.pitch_note,96.25)
        env.begin(Cell(effects=[(0,0xe5,160)]),ins);env.tick();self.assertEqual(env.pitch_note,108.75)
    def test_linear_pitch_validation(self):
        env=VolumeEnvelope(True,False)
        with self.assertRaisesRegex(FormatError,'linear pitch'):env.begin(Cell(effects=[(0,0xe5,128)]),None)
        ins=instrument((4,[0],0,0,0,1,255,255))
        with self.assertRaisesRegex(FormatError,'linear pitch'):env.begin(Cell(note=108,instrument=0),ins)
        with self.assertRaises(FormatError):VolumeEnvelope().begin(Cell(note=108,instrument=0),ins)
    def test_sparse_compiler_pitch_operations(self):
        module=parse(ROOT/'examples/psg_ring.fur');module.subsongs[0].rows=3;module.subsongs[0].speeds=[3]
        module.patterns={(0,6,0):{0:Cell(note=108,instrument=0,volume=15)}}
        module.subsongs[0].orders=[[0] for _ in module.channels]
        module.instruments=[instrument((1,[0,12],0,64,0,2,0,255))]
        with tempfile.TemporaryDirectory() as tmp:
            manifest=Path(tmp)/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'macro','source':str(ROOT/'examples/psg_ring.fur'),'voices':[{'channel':6,'slot':'tone0'}]}]}))
            with patch('calliope.compiler.parse',return_value=module):data,_,_=compile_bank(manifest)
        player=Player(data);player.start(1)
        for _ in range(10):player.step()
        writes=[e for e in player.trace if e[1] in (0x30,0x35)]
        self.assertEqual(writes[0],(1,0x30,(0x8b,6,0x90)))
        updates=[e for e in writes if e[1]==0x35]
        self.assertEqual([e[0] for e in updates],[3,5,7,9])
        self.assertEqual(updates[0][2],(0x85,3))
    def test_tone_sfx_sequence_pitch_and_release(self):
        from calliope.psg import compile_tone
        module=parse(ROOT/'examples/psg_ring.fur');song=module.subsongs[0]
        song.rows=4;song.speeds=[2];song.orders=[[0] for _ in module.channels]
        module.patterns={(0,6,0):{0:Cell(note=108,instrument=0,volume=15),2:Cell(note=181)}}
        module.instruments=[instrument((1,[0,12],0,64,0,2,0,255),(0,[15,8,0],0,0,0,1,0,1))]
        stream=compile_tone(module,0,6,'ntsc','sfx');p=0;tick=0;periods=[];levels=[]
        while stream[p]!=255:
            op=stream[p];p+=1
            if op==0:tick+=stream[p];p+=1;continue
            self.assertEqual(op,0x30);size=stream[p];p+=1;values=stream[p:p+size];p+=size
            if size==2:periods.append((tick,(values[0]&15)|(values[1]<<4)))
            else:levels.append((tick,values[0]&15))
        self.assertEqual(periods,[(0,107),(2,53),(4,107),(6,53)])
        self.assertEqual(levels,[(0,0),(1,7),(2,0),(3,7),(4,0),(5,7),(6,15)])
        self.assertEqual(tick,8)

    def test_loop_fine_pitch_requires_reset(self):
        module=parse(ROOT/'examples/psg_ring.fur');module.subsongs[0].rows=2;module.subsongs[0].speeds=[3]
        module.patterns={(0,6,0):{0:Cell(note=108,instrument=0),1:Cell(effects=[(0,0xe5,192)])}}
        module.subsongs[0].orders=[[0] for _ in module.channels];module.instruments=[{'kind':0,'features':{}}]
        with tempfile.TemporaryDirectory() as tmp:
            manifest=Path(tmp)/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'loop','loop_order':0,'source':str(ROOT/'examples/psg_ring.fur'),'voices':[{'channel':6,'slot':'tone0'}]}]}))
            with patch('calliope.compiler.parse',return_value=module):
                with self.assertRaisesRegex(FormatError,'reset changed PSG fine pitch'):compile_bank(manifest)
                module.patterns[0,6,0][0].effects=[(0,0xe5,128)]
                compile_bank(manifest)
if __name__=='__main__':unittest.main()
