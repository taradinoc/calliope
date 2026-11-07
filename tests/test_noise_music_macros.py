"""SMS noise instrument macros use Furnace's fixed-note flag and duty modes."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from calliope.psg import VolumeEnvelope
from calliope.furnace import Cell,FormatError
from calliope.macros import sequences

def hat():
    data=bytearray((8,0))
    def macro(code,values,loop=255,wide=False):
        data.extend((code,len(values),loop,255,0,193 if wide else 1,0,1))
        for value in values:data.extend(value.to_bytes(4 if wide else 1,'little',signed=wide))
    macro(0,[15,10,5,0]);macro(1,[0x4000005e,0],wide=True);macro(2,[3,1],1)
    data.append(255)
    return dict(kind=0,features=dict(NA=b'hat\0',MA=bytes(data),SM=b'\xff\xff\0\x1f',NE=b'\0'))

class NoiseMacros(unittest.TestCase):
    def test_fixed_note_then_relative_note_and_duty_loop(self):
        envelope=VolumeEnvelope(noise=True)
        envelope.begin(Cell(note=110,instrument=0,volume=15,effects=[]),hat())
        observed=[]
        for _ in range(5):
            level=envelope.tick();observed.append((envelope.pitch_note,envelope.noise_mode,level))
        self.assertEqual(observed,[(154,3,0),(110,1,5),(110,1,10),(110,1,15),(110,1,None)])
    def test_fixed_arpeggio_requires_explicit_noise_support(self):
        with self.assertRaises(FormatError):sequences(hat(),(0,1,2))
    def test_retrigger_restarts_envelopes(self):
        envelope=VolumeEnvelope(noise=True);cell=Cell(note=110,instrument=0,volume=15,effects=[])
        for _ in range(2):
            envelope.begin(cell,hat());self.assertEqual(envelope.tick(),0)
            self.assertEqual((envelope.pitch_note,envelope.noise_mode),(154,3))
            for _ in range(4):envelope.tick()

if __name__=='__main__':unittest.main()
