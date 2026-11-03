"""Output parity and state invalidation for preview preparation optimizations."""

import copy
import math
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from calliope import pcm
from calliope.compiler import fm_patch
from calliope.fm import Level
from calliope.furnace import Cell, parse


class PreviewOptimization(unittest.TestCase):
    def test_fm_reuses_calculation_but_republishes_after_patch_change(self):
        instrument = copy.deepcopy(parse(ROOT / "examples/fm_ring.fur").instruments[0])
        level = Level()
        level.begin(Cell(note=108, instrument=0, volume=100), instrument, True)
        with patch("calliope.compiler.fm_patch", wraps=fm_patch) as calculate:
            expected = level.tick()
            self.assertIsNotNone(expected)
            for _ in range(100):
                self.assertIsNone(level.tick())
            calculate.assert_called_once()
            # The compiler clears last when publishing a new instrument patch.
            level.last = None
            self.assertEqual(level.render(), expected)
            calculate.assert_called_once()
        for change in (lambda: setattr(level, "volume", 10),
                       lambda: level.operator_levels.__setitem__(0, 30),
                       lambda: setattr(level, "attenuation", 15),
                       lambda: setattr(level, "modern", False),
                       lambda: setattr(level, "ceil", True),
                       lambda: instrument["features"].__setitem__("FM", instrument["features"]["FM"][:-1] + bytes([instrument["features"]["FM"][-1] ^ 1]))):
            change()
            level.last = None
            with patch("calliope.compiler.fm_patch", wraps=fm_patch) as calculate:
                level.render()
                calculate.assert_called_once()

    def test_wav_decoding_all_widths_channels_and_extremes(self):
        with tempfile.TemporaryDirectory() as directory:
            for width in (1, 2, 3, 4):
                scale = 1 << (width * 8 - 1)
                values = [-scale, scale - 1, 0, -1, 1, scale // 3] * 2
                raw = b''.join((v + 128).to_bytes(1, "little") if width == 1
                               else v.to_bytes(width, "little", signed=True) for v in values)
                for channels in (1, 2):
                    path = Path(directory) / "sample.wav"
                    with wave.open(str(path), "wb") as stream:
                        stream.setparams((channels, width, 22050, 0, "NONE", ""))
                        stream.writeframes(raw)
                    samples, rate, info = pcm.decode(path)
                    expected = [sum(v / scale for v in values[i:i + channels]) / channels
                                for i in range(0, len(values), channels)]
                    self.assertEqual(samples, expected)
                    self.assertEqual(rate, 22050)
                    self.assertEqual(info["frames"], len(values) // channels)

    def test_vector_filter_matches_scalar_bytes_clipping_and_loops(self):
        try:
            from calliope.pcm_numpy import encode
        except ImportError:
            self.skipTest("NumPy not installed")
        rng = random.Random(724)
        signals = [[rng.uniform(-1, 1) for _ in range(257)],
                   [math.sin(i / 3) for i in range(257)],
                   [1.0] * 257, [-1.0] * 257, [0.0] * 257,
                   [1.0] + [0.0] * 256, [0.5 / 128] * 257,
                   [1.5 / 128] * 257, [0.75]]
        for region in ("ntsc", "pal"):
            for timer in (1016, 1020):
                for rate in (8000, 22050, 44100, 384000):
                    for signal in signals:
                        settings = dict(region=region, pcm_timer=timer, gain=1.5)
                        expected = pcm.resample(signal, rate, {}, **settings)
                        with patch("calliope.pcm_numpy.encode", pcm._encode_scalar):
                            reference = pcm.resample(signal, rate, {}, **settings)
                        self.assertEqual(expected, reference, (region, timer, rate, len(signal)))
        # Cross multiple chunks, including boundary taps and loop conversion.
        signal = [rng.uniform(-1, 1) for _ in range(5000)]
        expected = pcm.resample(signal, 22050, {}, loop=[0, 5000])
        with patch("calliope.pcm_numpy.encode", pcm._encode_scalar):
            self.assertEqual(expected, pcm.resample(signal, 22050, {}, loop=[0, 5000]))

    def test_scalar_fallback_without_numpy(self):
        expected = pcm.resample([0.5] * 32, 22050, {})
        with patch.dict(sys.modules, {"calliope.pcm_numpy": None}):
            self.assertEqual(pcm.resample([0.5] * 32, 22050, {}), expected)


if __name__ == "__main__":
    unittest.main()
