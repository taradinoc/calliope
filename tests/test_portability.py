"""Portable discovery and linked-ROM validation must fail safely."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from toolchain import sgdk_path, sgdk_tool, core_path, furnace_path, msvc_path
from consumer import finalize, prepare_bank


class Portability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='calliope paths ')
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_sdk_explicit_over_environment_and_missing_tools(self):
        sdk = self.root / 'sdk'
        (sdk / 'bin').mkdir(parents=True)
        with patch('toolchain.sys.platform', 'win32'), patch.dict(os.environ, {'GDK': str(self.root / 'absent')}):
            with self.assertRaisesRegex(ValueError, 'SGDK tool missing'):
                sgdk_path(sdk)
            for name in ('gcc.exe', 'sjasm.exe', 'make.exe'):
                (sdk / 'bin' / name).touch()
            self.assertEqual(sgdk_path(sdk), sdk.resolve())
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'Set GDK'):
                sgdk_path()

    def test_linux_tools_use_native_path_and_compiler_prefix(self):
        sdk = self.root / 'sdk'
        with patch('toolchain.sys.platform', 'linux'), patch.dict(os.environ, {'PREFIX': 'custom-m68k-'}), patch('toolchain.shutil.which') as which:
            which.side_effect = lambda name: str(self.root / name)
            self.assertEqual(sgdk_tool(sdk, 'gcc'), self.root / 'custom-m68k-gcc')
            self.assertEqual(sgdk_tool(sdk, 'sjasm'), self.root / 'sjasm')
            self.assertEqual(sgdk_tool(sdk, 'make'), self.root / 'make')
            self.assertEqual([call.args[0] for call in which.call_args_list],
                             ['custom-m68k-gcc', 'sjasm', 'make'])

    def test_linux_does_not_fall_back_to_windows_tools(self):
        sdk = self.root / 'sdk'
        (sdk / 'bin').mkdir(parents=True)
        for name in ('gcc.exe', 'make.exe', 'sjasm.exe'):
            (sdk / 'bin' / name).touch()
        with patch('toolchain.sys.platform', 'linux'), patch('toolchain.shutil.which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'install native'):
                sgdk_path(sdk)

    def test_linux_default_compiler_matches_sgdk_make(self):
        with patch('toolchain.sys.platform', 'linux'), patch.dict(os.environ, {}, clear=True), patch('toolchain.shutil.which', return_value='/usr/bin/m68k-elf-gcc') as which:
            self.assertEqual(sgdk_tool(self.root, 'gcc'), Path('/usr/bin/m68k-elf-gcc').resolve())
            which.assert_called_once_with('m68k-elf-gcc')

    def test_optional_dependencies_accept_paths_with_spaces(self):
        suffix = '.dll' if sys.platform == 'win32' else '.dylib' if sys.platform == 'darwin' else '.so'
        core = self.root / ('custom_libretro' + suffix)
        core.touch()
        with patch.dict(os.environ, {'CALLIOPE_CORES_DIR': str(self.root),
                                     'CALLIOPE_FURNACE': str(core),
                                     'CALLIOPE_VCVARS': str(core)}):
            self.assertEqual(core_path(core), core.resolve())
            self.assertEqual(core_path(core.name), core.resolve())
            self.assertEqual(furnace_path(), core.resolve())
            self.assertEqual(msvc_path(), core.resolve())
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'Libretro core not found'):
                core_path('nonexistent')

    def test_finalization_checks_whole_bank_and_preserves_failed_rom(self):
        bank = bytearray(32)
        bank[:6] = b'BGM1\x0c\x00'
        bank[19:21] = (1016).to_bytes(2, 'little')
        rom = self.root / 'rom.bin'
        symbols = self.root / 'symbol.txt'
        config = self.root / 'calliope_config.json'
        config.write_text(json.dumps({'pcm_timer': 1016, 'region': 'ntsc'}))
        symbols.write_text('00008000 R custom_bank\n')
        (self.root / 'bank.bin').write_bytes(bank)
        rom.write_bytes(bytes(32768) + bank)
        finalize(rom, symbols, config, 'custom_bank')
        self.assertEqual(int.from_bytes(rom.read_bytes()[0x1a4:0x1a8], 'big'), rom.stat().st_size - 1)
        broken = bytearray(rom.read_bytes())
        broken[-1] = 1
        rom.write_bytes(broken)
        with self.assertRaisesRegex(ValueError, 'contents differ'):
            finalize(rom, symbols, config, 'custom_bank')
        self.assertEqual(rom.read_bytes(), broken)

    def test_preparation_cannot_overwrite_manifest(self):
        manifest = self.root / 'bank.json'
        original = '{"version":1,"songs":[],"sfx":[]}'
        manifest.write_text(original)
        with self.assertRaisesRegex(ValueError, 'overwrite an input'):
            prepare_bank(manifest, self.root, 'ntsc', False, 'clp_bank')
        self.assertEqual(manifest.read_text(), original)
        with self.assertRaisesRegex(ValueError, 'C identifier'):
            prepare_bank(manifest, self.root / 'out', 'ntsc', False, 'bad-symbol')


if __name__ == '__main__':
    unittest.main()
