"""PCM reuse across bank edits must preserve bytes and conversion metadata."""

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from calliope import pcm
from calliope.editor.services import cached_compile


class PcmCache(unittest.TestCase):
    def test_reuse_invalidation_and_corruption(self):
        samples = [((i * 37) % 256 - 128) / 128 for i in range(256)]
        settings = dict(rate=22050, region="ntsc", gain=0.75,
                        loop=[10, 240], pcm_timer=1020)
        with tempfile.TemporaryDirectory() as directory:
            expected = pcm.resample(samples, info={"source": "first"}, **settings)
            with pcm.conversion_cache(directory):
                self.assertEqual(pcm.resample(samples, info={"source": "first"}, **settings), expected)
                with patch.object(pcm, "_resample", side_effect=AssertionError("cache miss")):
                    data, info = pcm.resample(samples, info={"source": "second"}, **settings)
                    self.assertEqual(data, expected[0])
                    self.assertEqual(info, dict(expected[1], source="second"))
                for field, value in (("rate", 16000), ("region", "pal"),
                                     ("gain", 1.5), ("loop", None), ("pcm_timer", 1016)):
                    changed = dict(settings, **{field: value})
                    reference = pcm._resample(samples, info={}, **changed)
                    with patch.object(pcm, "_resample", wraps=pcm._resample) as convert:
                        self.assertEqual(pcm.resample(samples, info={}, **changed), reference)
                        convert.assert_called_once()
                changed_samples = samples.copy()
                changed_samples[0] = 0.5
                with patch.object(pcm, "_resample", wraps=pcm._resample) as convert:
                    pcm.resample(changed_samples, info={}, **settings)
                    convert.assert_called_once()
                for path in Path(directory).glob("*.json"):
                    path.write_text('{"broken":true}')
                self.assertEqual(pcm.resample(samples, info={"source": "first"}, **settings), expected)
            with patch.object(pcm, "_resample", wraps=pcm._resample) as convert:
                pcm.resample(samples, info={}, **settings)
                convert.assert_called_once()

    def test_unwritable_cache_is_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            with pcm.conversion_cache(directory), patch("clpconvert.atomic", side_effect=OSError("read-only")):
                self.assertEqual(pcm.resample([0.5] * 32, 22050, {}),
                                 pcm._resample([0.5] * 32, 22050, {}))

    def test_bank_edit_reuses_pcm_and_preserves_output(self):
        document = {"version": 1, "sfx": [
            {"id": 1, "name": "click", "source": "click.wav"}]}
        with tempfile.TemporaryDirectory() as directory:
            first = cached_compile(document, ROOT / "examples", "ntsc", "hybrid", directory)
            edited = copy.deepcopy(document)
            edited["sfx"][0]["name"] = "renamed"
            reference = cached_compile(edited, ROOT / "examples", "ntsc", "hybrid")
            with patch.object(pcm, "_resample", side_effect=AssertionError("resampled unchanged PCM")):
                result = cached_compile(edited, ROOT / "examples", "ntsc", "hybrid", directory)
            self.assertFalse(first[3])
            self.assertFalse(result[3])
            self.assertEqual(result[:3], reference[:3])


if __name__ == "__main__":
    unittest.main()
