import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from calliope.furnace import Cell, parse, FormatError
from calliope.psg import VolumeEnvelope
from calliope.compiler import compile_bank
from calliope.reference import Player

class Karaoke(unittest.TestCase):
    def test_timing_header_honors_song_override(self):
        build = self.load_build()
        item = json.loads((ROOT / "examples/karaoke/bank.json").read_text())
        item["songs"][0]["marker_advance_ticks"] = 60
        with tempfile.TemporaryDirectory() as tmp:
            manifest, output = Path(tmp)/"bank.json", Path(tmp)/"timing.h"
            manifest.write_text(json.dumps(item))
            build.generate_timing(ROOT / "examples/karaoke/another_night_karaoke.fur", manifest, output)
            self.assertIn("KARAOKE_RATE5 336", output.read_text())
            self.assertIn("KARAOKE_LEAD_TICKS 60", output.read_text())

    def test_row_speed_allowed_but_tick_rate_change_rejected(self):
        build = self.load_build()
        source = ROOT / "examples/karaoke/another_night_karaoke.fur"
        module = parse(source)
        pattern = module.patterns.setdefault((0, 0, module.subsongs[0].orders[0][0]), {})
        cell = pattern.setdefault(0, Cell())
        cell.effects.append((3, 0x0f, 7))
        with tempfile.TemporaryDirectory() as tmp, patch.object(build, "parse", return_value=module):
            output = Path(tmp)/"timing.h"
            manifest = ROOT / "examples/karaoke/bank.json"
            build.generate_timing(source, manifest, output)
            cell.effects.append((4, 0xc0, 60))
            with self.assertRaisesRegex(ValueError, "constant tick rate"):
                build.generate_timing(source, manifest, output)

    def test_timing_rejects_clamped_cue(self):
        build = self.load_build()
        item = json.loads((ROOT / "examples/karaoke/bank.json").read_text())
        item["marker_advance_ticks"] = 65535
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp)/"bank.json"
            manifest.write_text(json.dumps(item))
            with self.assertRaisesRegex(ValueError, "clamp to song start"):
                build.generate_timing(ROOT / "examples/karaoke/another_night_karaoke.fur", manifest, Path(tmp)/"timing.h")

    def test_timing_rejects_simultaneous_contacts(self):
        build = self.load_build()
        source = ROOT / "examples/karaoke/another_night_karaoke.fur"
        module = parse(source)
        cell = next(cell for pattern in module.patterns.values() for cell in pattern.values()
                    if any(e == 0xee and v == 0xf0 for _, e, v in cell.effects))
        cell.effects.append((1, 0xee, 0xf1))
        with tempfile.TemporaryDirectory() as tmp, patch.object(build, "parse", return_value=module):
            with self.assertRaisesRegex(ValueError, "separate music tick"):
                build.generate_timing(source, ROOT / "examples/karaoke/bank.json", Path(tmp)/"timing.h")

    @staticmethod
    def load_build():
        spec = importlib.util.spec_from_file_location("karaoke_build", ROOT / "examples/karaoke/build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        return build

    def test_advance_preserves_original_contact_times(self):
        path = ROOT / "examples/karaoke/bank.json"
        manifest = json.loads(path.read_text())
        lead = manifest["marker_advance_ticks"]
        self.assertEqual(lead, 95)
        manifest["songs"] = manifest["songs"][:1]
        manifest["songs"][0]["source"] = str(path.parent / manifest["songs"][0]["source"])
        timelines = []
        with tempfile.TemporaryDirectory() as tmp:
            for advance in (0, lead):
                manifest["marker_advance_ticks"] = advance
                source = Path(tmp) / "bank.json"
                source.write_text(json.dumps(manifest))
                data, _, _ = compile_bank(source)
                player = Player(data)
                player.start(1)
                events = []
                for _ in range(100000):
                    player.step()
                    events.extend((tick, event, channel) for tick, _, event, channel in player.events)
                    player.events.clear()
                    if not player.playing: break
                self.assertFalse(player.playing)
                timelines.append(events)
        self.assertEqual([(tick + lead, event, channel) for tick, event, channel in timelines[1]], timelines[0])

    def test_authored_cues_match_every_word(self):
        module = parse(ROOT / "examples/karaoke/another_night_karaoke.fur")
        song = module.subsongs[0]
        words = []
        for order in range(len(song.orders[0])):
            for row in range(song.rows):
                for channel in range(len(module.channels)):
                    for _, effect, value in module.cell(0, order, row, channel).effects:
                        if effect == 0xee and value == 0xf0: words.append(1)
                        elif effect == 0xee and value == 0xf1: words[-1] += 1
        lines = self.load_build().read_lyrics(ROOT / "examples/karaoke/another_night_karaoke.txt")[1]
        self.assertEqual(words, [len(re.findall(r"[^ _-]+", line)) for line in lines if line.strip()])

    def test_build_validates_all_authored_cues(self):
        build = self.load_build()
        with tempfile.TemporaryDirectory() as tmp:
            for index, name in enumerate(("another_night_karaoke", "night_by_night_karaoke")):
                build.generate_timing(ROOT / f"examples/karaoke/{name}.fur",
                                      ROOT / "examples/karaoke/bank.json",
                                      Path(tmp) / "timing.h",
                                      ROOT / f"examples/karaoke/{name}.txt", song_index=index)

    def test_multi_song_tables_keep_independent_timing_and_ids(self):
        build = self.load_build()
        path = ROOT / "examples/karaoke/bank.json"
        bank = json.loads(path.read_text())
        for item in bank["songs"]:
            item["source"] = str(path.parent / item["source"])
        bank["songs"][1]["marker_advance_ticks"] = 60
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            manifest = folder / "bank.json"
            manifest.write_text(json.dumps(bank))
            build.generate_songs(manifest, folder)
            self.assertIn("song0_KARAOKE_RATE5 336", (folder / "song0_timing.h").read_text())
            self.assertIn("song0_KARAOKE_LEAD_TICKS 95", (folder / "song0_timing.h").read_text())
            self.assertIn("song1_KARAOKE_RATE5 252", (folder / "song1_timing.h").read_text())
            self.assertIn("song1_KARAOKE_LEAD_TICKS 60", (folder / "song1_timing.h").read_text())
            self.assertIn("{2, song1_KARAOKE_TITLE", (folder / "songs.h").read_text())

    def test_cue_validation_reports_line_and_segment_mismatches(self):
        build = self.load_build()
        with self.assertRaisesRegex(ValueError, "precedes"):
            build.validate_cues(["Hello"], [(100, 0xf1, 16)])
        with self.assertRaisesRegex(ValueError, "line count"):
            build.validate_cues(["Hello", "World"], [(100, 0xf0, 16)])
        with self.assertRaisesRegex(ValueError, "line 1: 2 segments, 1 cues"):
            build.validate_cues(["Hel_lo"], [(100, 0xf0, 16)])

    def test_break_threshold_and_flight_reservation(self):
        build = self.load_build()
        cues = [(190, 0xf0, 48), (474, 0xf1, 96), (759, 0xf0, 144)]
        self.assertEqual(build.detect_breaks(cues, 95, 16),
                         [(0, 95, 3, True), (569, 664, 2, False)])
        # A formerly qualifying two-advance gap now keeps the lyric visible.
        self.assertEqual(build.detect_breaks(
            [(190, 0xf0, 48), (380, 0xf1, 96)], 95, 16),
            [(0, 95, 3, True)])
        self.assertEqual(build.detect_breaks([], 95, 16), [])

    def test_metadata_is_separate_from_lyrics(self):
        build = self.load_build()
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp)/"lyrics.txt", Path(tmp)/"lyrics.h"
            source.write_text("TITLE: Test song\nARTIST: Test artist\n\nHel_lo\n")
            build.generate_lyrics(source, output)
            text = output.read_text()
            self.assertIn('#define KARAOKE_TITLE "Test song"', text)
            self.assertIn('#define KARAOKE_ARTIST "Test artist"', text)
            self.assertEqual(build.read_lyrics(source)[1], ["Hel_lo"])

    def test_hidden_delimiters_do_not_count_toward_display_width(self):
        build = self.load_build()
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp)/"lyrics.txt", Path(tmp)/"lyrics.h"
            line = "_".join("a" * 40)
            source.write_text(line)
            build.generate_lyrics(source, output)
            self.assertIn(json.dumps(line), output.read_text())
            source.write_text("_".join("a" * 41))
            with self.assertRaisesRegex(ValueError, "40 columns"):
                build.generate_lyrics(source, output)
            source.write_text("_ - ___")
            with self.assertRaisesRegex(ValueError, "contain a word"):
                build.generate_lyrics(source, output)

    def test_lyric_generation_ignores_blank_lines_and_preserves_hyphens(self):
        build = self.load_build()
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp)/"lyrics.txt", Path(tmp)/"lyrics.h"
            source.write_text("\n First line \n\nCoca-Cola\n")
            build.generate_lyrics(source, output)
            self.assertIn('"First line",', output.read_text())
            self.assertIn('"Coca-Cola",', output.read_text())
            self.assertNotIn('"",', output.read_text())

    def test_psg_portamento_keeps_envelope_and_reaches_target(self):
        module = parse(ROOT / "examples/karaoke/another_night_karaoke.fur")
        envelope = VolumeEnvelope(True)
        instrument = next(ins for ins in module.instruments if ins["kind"] == 0)
        envelope.begin(Cell(note=87, instrument=6), instrument)
        envelope.tick()
        cursors = envelope.cursors
        envelope.begin(Cell(note=91, instrument=6, effects=[(0,3,32)]), instrument)
        self.assertIs(envelope.cursors, cursors)
        positions = []
        for _ in range(4):
            envelope.tick()
            positions.append(envelope.pitch_note)
        self.assertEqual(positions, [88,89,90,91])
        self.assertEqual(envelope.porta_speed, 0)
        self.assertEqual(envelope.note, 91)
        envelope.begin(Cell(note=93, effects=[(0,3,16)]), instrument)
        envelope.tick()
        self.assertEqual(envelope.pitch_note, 91.5)
        envelope.begin(Cell(note=90, effects=[(0,3,0)]), instrument)
        envelope.tick()
        self.assertEqual(envelope.pitch_note, 90)

    def test_partial_psg_portamento_rejected(self):
        envelope = VolumeEnvelope(True, linear=False, partial=True)
        with self.assertRaisesRegex(FormatError, "full linear"):
            envelope.begin(Cell(note=87, effects=[(0,3,32)]), None)

if __name__ == "__main__": unittest.main()
