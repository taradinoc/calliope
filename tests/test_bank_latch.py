"""A serial nine-bit bank selection cannot contain recursive ROM work."""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]

def write_bank(latch,bank,bits=9):
    for bit in range(bits):latch=(latch>>1)|(((bank>>bit)&1)<<8)
    return latch

class BankLatch(unittest.TestCase):
    def test_full_selection_discards_previous_latch(self):
        for initial in range(512):
            for target in range(256):
                self.assertEqual(write_bank(initial,target),target)

    def test_recursive_restore_inside_selection_is_not_a_restore(self):
        # A queued patch saves/restores the software bank but completes its own
        # nine hardware writes. Remaining outer bits then rotate that bank.
        for consumed in (2,4,6,8):
            target=0x77
            latch=write_bank(0,target,consumed)
            latch=write_bank(latch,0x20)
            latch=write_bank(latch,target)
            for bit in range(consumed,9):latch=(latch>>1)|(((target>>bit)&1)<<8)
            self.assertNotEqual(latch,target)

    def test_both_real_latch_transactions_have_no_software_service(self):
        for name,start,end in (('calliope.s80','bank_bits:','read_mapped:'),
                               ('pcm.i80','pcm_resume_bank:','pcm_resume_bank_ready:')):
            body=(ROOT/'driver'/name).read_text(encoding='utf-8').split(start,1)[1].split(end,1)[0]
            instructions='\n'.join(line.split(';',1)[0]for line in body.splitlines())
            self.assertIsNone(re.search(r'\b(?:RST|CALL|EI)\b',instructions),name)
            self.assertIn('LD (HL),A',instructions)

if __name__=='__main__':unittest.main()
