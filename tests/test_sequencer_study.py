import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from calliope.furnace import Cell, FormatError
from sequencer_study import (CALL, decode_pattern, encode_pattern, merge_waits,
                             phrase_compress, timed_trace, tokens)


class SequencerStudy(unittest.TestCase):
    def test_wait_merge_preserves_events_and_terminal_time_over_byte_limit(self):
        stream = tokens(bytes((0, 200, 0, 100, 0x20, 7, 0, 42, 0,
                               0, 2, 0, 3, 0xfd)))
        merged = merge_waits(stream)
        self.assertEqual(merged[:2], (bytes((0, 255)), bytes((0xac,))))
        self.assertEqual(timed_trace(merged), timed_trace(stream))
        self.assertEqual(timed_trace(merged)[-1], (305, bytes((0xfd,))))

    def test_phrase_calls_preserve_selected_part_waits_and_order_end(self):
        phrase = (bytes((0x14, 2)), bytes((0x11, 0, 0x34, 0x12)),
                  bytes((0, 6)), bytes((0x13, 0)))
        stream = phrase * 20 + (bytes((0xfd,)),)
        encoded, dictionary = phrase_compress([stream])
        self.assertTrue(dictionary)
        self.assertTrue(any(t[0] == CALL for t in encoded[0]))
        expanded = tuple(t for command in encoded[0]
                         for t in (dictionary[int.from_bytes(command[1:], 'little')]
                                   if command[0] == CALL else (command,)))
        self.assertEqual(expanded, stream)
        old_size = sum(map(len, stream))
        new_size = sum(map(len, encoded[0])) + sum(sum(map(len, p)) + 1 for p in dictionary)
        self.assertLess(new_size, old_size)
        self.assertTrue(all(t[0] not in (0xfd, 0xff, CALL)
                            for p in dictionary for t in p))

    def test_sparse_pattern_retains_releases_effect_columns_and_empty_tail(self):
        cells = [Cell() for _ in range(260)]
        cells[0] = Cell(note=60, instrument=150, volume=0)
        cells[200] = Cell(note=182, effects=[(0, 4, 0), (2, 0xee, None)])
        cells[250] = Cell(note=181)
        self.assertEqual(decode_pattern(encode_pattern(cells)), cells)
        self.assertEqual(decode_pattern(encode_pattern([Cell()] * 64)), [Cell()] * 64)

    def test_truncation_and_unknown_commands_fail(self):
        for data in (bytes((0,)), bytes((0, 0, 0xfd)), bytes((0x30, 3, 1)),
                     bytes((0x11, 0)), bytes((0x7e, 0xfd))):
            with self.assertRaises(FormatError):
                tokens(data)
        with self.assertRaises(FormatError):
            decode_pattern(encode_pattern([Cell(note=60)])[:-1])


if __name__ == '__main__':
    unittest.main()
