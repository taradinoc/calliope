"""Musical events stay put while silent patch work moves off the note edge."""
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from calliope.music_schedule import prepare
from calliope.compiler import compile_bank,validate_manifest
from calliope.furnace import Cell,FormatError,parse


def note(instrument, level=20, mask=8, part=0):
    return bytes((0x14, part, 0x10, 0, instrument, 0x16, 0, 192,
                  0x37, 0, 30, 31, 32, level, mask, 0x11, 0, 0x34, 0x12))


def level(value, mask=8, operator=32):
    return bytes((0x37, 0, 30, 31, operator, value, mask))


def events(data):
    result, at, tick = [], 0, 0
    widths = {0x10: 1, 0x11: 2, 0x13: 0, 0x16: 1, 0x37: 5, 0x3A: 1, 0x3B: 1,
              **{0x50 + i: 1 for i in range(4)}, 0xFD: 0, 0xFF: 0}
    while at < len(data):
        op = data[at]
        at += 1
        if op == 0:
            tick += data[at]
            at += 1
        elif 0x80 <= op < 0xFD:
            tick += op - 0x7F
        else:
            size = widths[op]
            result.append((tick, op, data[at:at + size]))
            at += size
    return result


class MusicSchedule(unittest.TestCase):
    def test_opt_in_is_versioned_and_does_not_change_hybrid_banks(self):
        source=Path(__file__).resolve().parents[1]/'examples/fm_ring.fur'
        module=parse(source);song=module.subsongs[0]
        song.rows=4;song.speeds=[1];song.orders=[[0] for _ in module.channels]
        module.instruments[0]['features'].pop('MA',None)
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0),2:Cell(note=110)}}
        manifest={'version':1,'songs':[{'id':1,'name':'scheduled','source':str(source),
                                      'voices':[{'channel':0,'slot':'fm0'}]}]}
        with tempfile.TemporaryDirectory() as directory,patch('calliope.compiler.parse',return_value=module):
            plain=compile_bank(manifest,base_dir=directory)[0]
            scheduled=compile_bank(dict(manifest,z80_schedule=True),base_dir=directory)[0]
            hybrid=compile_bank(manifest,pcm_timer=1020,base_dir=directory)[0]
            opt_hybrid=compile_bank(dict(manifest,z80_schedule=True),pcm_timer=1020,base_dir=directory)[0]
        self.assertEqual(plain[4],12);self.assertEqual(scheduled[4],13)
        self.assertEqual(hybrid,opt_hybrid)
        with self.assertRaises(FormatError):validate_manifest(dict(manifest,z80_schedule=1))

    def test_algorithm_change_prepares_in_old_carrier_mute_without_moving_note(self):
        raw = note(0) + bytes((0, 2)) + level(127) + bytes((0, 6)) + note(1, 25, 12) + bytes((0, 4))
        conductor, fm = prepare([raw], [0], [0xFF])[0]
        actual = events(fm)
        self.assertIn((3, 0x3A, b"\x01"), actual)
        self.assertIn((8, 0x3B, b"\x01"), actual)
        self.assertEqual([t for t, op, _ in actual if op == 0x11], [0, 8])
        self.assertEqual(events(conductor)[-1][0], 12)
        self.assertEqual(actual[-1][0], 12)

    def test_old_macro_cannot_unmute_a_new_algorithm_carrier_after_preparation(self):
        raw = note(0) + bytes((0, 2)) + level(127) + bytes((0, 5)) + level(127, operator=25)
        raw += bytes((0, 1)) + note(1, 25, 12) + bytes((0, 2))
        actual = events(prepare([raw], [0], [0xFF])[0][1])
        self.assertFalse(any(op == 0x3A for _, op, _ in actual))
        self.assertIn((8, 0x10, b"\x01"), actual)

    def test_overlap_retains_ordinary_uploads_and_all_sixteen_channel_cursors(self):
        raw = note(0, part=15) + bytes((0, 2)) + level(127) + bytes((0, 6)) + note(1, part=15)
        raw += bytes((0, 2))
        group = prepare([raw], [0] * 16, [0xFF])[0]
        self.assertEqual(len(group), 17)
        self.assertFalse(any(op == 0x3A for _, op, _ in events(group[16])))
        self.assertEqual([t for t, op, _ in events(group[16]) if op == 0x11], [0, 8])
        self.assertTrue(all(events(data)[-1][0] == 10 for data in group))

    def test_one_operator_compacts_only_with_unchanged_sensitivity(self):
        raw = note(0) + bytes((0, 1)) + level(25) + bytes((0, 1)) + level(25, mask=12) + bytes((0, 1))
        actual = events(prepare([raw], [0], [0xFF])[0][1])
        self.assertIn((1, 0x53, b"\x19"), actual)
        self.assertIn((2, 0x37, bytes((30, 31, 32, 25, 12))), actual)

    def test_loop_tail_prepares_entry_and_first_pass_keeps_fallback_commit(self):
        intro = note(0) + bytes((0, 6)) + level(127) + bytes((0, 2))
        body = note(1) + bytes((0, 4)) + note(0) + bytes((0, 2)) + level(127) + bytes((0, 2))
        group = prepare([intro, body], [0], [0xFD, 0xFD], loop=1)
        first, second = events(group[0][1]), events(group[1][1])
        self.assertIn((7, 0x3A, b"\x01"), first)
        self.assertIn((0, 0x3B, b"\x01"), second)
        self.assertIn((7, 0x3A, b"\x01"), second)


if __name__ == "__main__":
    unittest.main()
