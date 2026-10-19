import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from calliope.compiler import compile_bank
from calliope.furnace import Cell, parse
from calliope.reference import Player
from make_fixtures import make


class SampleDedup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self, sources, **options):
        path = self.root / 'bank.json'
        path.write_text(json.dumps({'version': 1, 'songs': [
            dict(id=i + 1, name=f'song{i}', source=str(source), **options)
            for i, source in enumerate(sources)
        ]}))
        return path

    def triggers(self, data, song):
        player = Player(data)
        player.start(song)
        for _ in range(64):
            player.step()
        return [event[2] for event in player.trace if event[1] == 0x31]

    def test_identical_samples_in_different_song_files_share_rom_bytes(self):
        source = ROOT / 'examples/elevator.fur'
        other = self.root / 'other.fur'
        other.write_bytes(source.read_bytes())
        voices = [{'channel': ch, 'slot': slot} for ch, slot in
                  [(0, 'fm0'), (1, 'fm1'), (5, 'pcm'), (6, 'tone0'), (7, 'tone1')]]
        manifest = self.manifest([source, other], voices=voices, loop_order=0)
        for region in ('ntsc', 'pal'):
            with self.subTest(region=region):
                data, report, _ = compile_bank(manifest, region)
                first = [s for s in report['music_samples'] if s['source'] == str(source.resolve())]
                second = [s for s in report['music_samples'] if s['source'] == str(other.resolve())]
                self.assertTrue(first)
                self.assertEqual(len(first), len(second))
                for a, b in zip(first, second):
                    self.assertEqual(a['offset'], b['offset'])
                    self.assertEqual(a['descriptor'], b['descriptor'])
                    payload = data[a['offset']:a['offset'] + a['bytes']]
                    self.assertEqual(data.count(payload), 1)
                self.assertTrue(self.triggers(data, 1))
                self.assertEqual(self.triggers(data, 1), self.triggers(data, 2))

    def pcm_module(self, name, loop=None, rate=22050, level=12000, index=0):
        source = self.root / name
        source.write_bytes(make(event=False, notes=(108,)))
        module = parse(source)
        module.patterns = {(0, 5, 0): {0: Cell(note=108, instrument=0), 1: Cell(note=180)}}
        module.instruments = [{'kind': 4, 'features': {'SM': bytes((index, 0, 0, 31))}}]
        sample = {'depth': 16, 'direction': 0, 'data': struct.pack('<h', level) * 256,
                  'center_rate': rate, 'rate': rate, 'loop': loop}
        module.samples = [dict(sample) for _ in range(index + 1)]
        return source, module

    def test_same_source_is_reported_for_each_song(self):
        source, module = self.pcm_module('shared.fur')
        module.samples[0]['name'] = 'Kick'
        manifest = self.manifest([source, source], voices=[{'channel': 5, 'slot': 'pcm'}])
        with patch('calliope.compiler.parse', return_value=module):
            _, report, _ = compile_bank(manifest)
        a, b = report['music_samples']
        self.assertEqual([a['resource_id'], b['resource_id']], [1, 2])
        self.assertEqual([a['resource_name'], b['resource_name']], ['song0', 'song1'])
        self.assertEqual(a['sample_name'], 'Kick')
        self.assertEqual(a['offset'], b['offset'])
        self.assertEqual(a['descriptor'], b['descriptor'])

    def test_shared_bytes_preserve_each_songs_loop_and_sample_index(self):
        source, first = self.pcm_module('first.fur')
        other, second = self.pcm_module('second.fur', loop=[64, 192], index=1)
        manifest = self.manifest([source, other], voices=[{'channel': 5, 'slot': 'pcm'}])
        with patch('calliope.compiler.parse', side_effect=[first, second]):
            data, report, _ = compile_bank(manifest)
        a, b = report['music_samples']
        self.assertEqual(a['offset'], b['offset'])
        self.assertEqual(data.count(data[a['offset']:a['offset'] + a['bytes']]), 1)
        self.assertNotEqual(a['descriptor'], b['descriptor'])
        self.assertEqual((a['sample'], b['sample']), (0, 1))
        self.assertIsNone(a['loop'])
        self.assertIsNotNone(b['loop'])
        for song, sample in enumerate((a, b), 1):
            self.assertEqual(self.triggers(data, song), [sample['descriptor']])
            descriptor = sample['descriptor']
            fields = [int.from_bytes(data[descriptor + offset:descriptor + offset + 3], 'little')
                      for offset in (4, 7, 10, 13)]
            self.assertEqual(fields, [sample['offset'], sample['bytes'], *(sample['loop'] or [0, 0])])

    def test_different_encoded_samples_remain_distinct(self):
        for options, gain in (({'level': -12000}, 1), ({'rate': 11025}, 1), ({}, 0.5)):
            with self.subTest(options=options, gain=gain):
                source, first = self.pcm_module('first.fur')
                other, second = self.pcm_module('second.fur', **options)
                manifest = self.manifest([source, other], voices=[{'channel': 5, 'slot': 'pcm'}])
                content = json.loads(manifest.read_text())
                content['songs'][1]['pcm_gain'] = gain
                manifest.write_text(json.dumps(content))
                with patch('calliope.compiler.parse', side_effect=[first, second]):
                    data, report, _ = compile_bank(manifest)
                a, b = report['music_samples']
                self.assertNotEqual(a['offset'], b['offset'])
                self.assertNotEqual(data[a['offset']:a['offset'] + a['bytes']],
                                    data[b['offset']:b['offset'] + b['bytes']])


if __name__ == '__main__':
    unittest.main()
