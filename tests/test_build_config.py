"""Mode selection must keep compiler, assembler and converter in agreement."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import time
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from configure import resolve,prepare
from toolchain import sgdk_path, sgdk_tool
SDK=Path(os.environ.get('GDK','__sgdk_not_configured__'))


try:
    SDK = sgdk_path(SDK)
    SDK_READY = True
except ValueError:
    SDK_READY = False


@unittest.skipUnless(SDK_READY,'SGDK required for build-configuration tests')
class BuildConfig(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='calliope config ');self.root=Path(self.temp.name)
        self.header=self.root/'project audio.h';self.header.write_text('#ifndef CALLIOPE_HYBRID\n#define CALLIOPE_HYBRID (1 == 1)\n#endif\n')
    def tearDown(self):self.temp.cleanup()
    def test_public_default_and_header_expression(self):
        self.assertEqual(resolve(SDK,ROOT/'include/calliope_config.h')['driver'],'calliope.s80')
        config=resolve(SDK,self.header);self.assertEqual((config['hybrid'],config['pcm_timer'],config['driver']),(True,1020,'hybrid_rom.s80'))
    def test_override_and_host_comparison(self):
        config=resolve(SDK,self.header,0);self.assertEqual((config['hybrid'],config['pcm_timer']),(False,1016))
        self.assertEqual(resolve(SDK,self.header,1,1)['driver'],'hybrid.s80')
    def test_invalid_modes_rejected(self):
        for defines in ('#define CALLIOPE_HYBRID typo\n','#define CALLIOPE_HYBRID 2\n','#define CALLIOPE_HYBRID 0\n#define CALLIOPE_HOST_PCM 1\n','#define CALLIOPE_HOST_PCM -1\n'):
            self.header.write_text(defines)
            with self.assertRaisesRegex(ValueError,'invalid Calliope configuration'):resolve(SDK,self.header)
    def test_unguarded_conflicting_header_rejected(self):
        self.header.write_text('#define CALLIOPE_HYBRID 1\n')
        with self.assertRaisesRegex(ValueError,'guarded'):resolve(SDK,self.header,0)
    def test_mode_switch_reassembles_and_stable_outputs(self):
        out=self.root/'build';assembly=self.root/'driver.s'
        hybrid=prepare(SDK,self.header,out,assembly);image=(out/'driver.bin').read_bytes()
        self.assertEqual(len(image),8192)
        first={p:p.stat().st_mtime_ns for p in out.iterdir()}
        prepare(SDK,self.header,out,assembly)
        self.assertEqual(first,{p:p.stat().st_mtime_ns for p in out.iterdir()})
        damaged=bytearray(image);damaged[100]^=255;(out/'driver.bin').write_bytes(damaged)
        prepare(SDK,self.header,out,assembly);self.assertEqual(image,(out/'driver.bin').read_bytes())
        z80=prepare(SDK,self.header,out,assembly,0)
        self.assertNotEqual(image,(out/'driver.bin').read_bytes())
        self.assertEqual((z80['driver'],z80['pcm_timer']),('calliope.s80',1016))
        self.assertIn('override CALLIOPE_PCM_TIMER := 1016',(out/'calliope_build.mk').read_text())
        self.assertIn('#define CALLIOPE_HYBRID 0',(out/'calliope_build.h').read_text())
        prepare(SDK,self.header,out,assembly)
        self.assertEqual(image,(out/'driver.bin').read_bytes())
    def test_generated_header_catches_mismatched_c_flags(self):
        out=self.root/'build';prepare(SDK,self.header,out,None)
        result=subprocess.run([str(sgdk_tool(SDK, 'gcc')),'-B'+str(SDK/'bin')+'/','-E','-x','c','-I'+str(ROOT/'include'),'-include',str(out/'calliope_build.h'),'-DCALLIOPE_HYBRID=0','-'],input='',capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertIn('disagrees',result.stderr)
    def test_make_header_and_command_overrides(self):
        # SGDK Make 3.81 must restart once, keep unchanged timestamps, and export
        # the resolved rate even when a caller supplies a stale timer variable.
        make=self.root/'Makefile';home=ROOT.as_posix();sdk=SDK.as_posix()
        # Keep generated Make paths relative: quoted subprocess arguments handle
        # the temporary project's root and configuration header spaces.
        make.write_text('GDK := '+sdk+'\nCALLIOPE_HOME := '+home+'\nCALLIOPE_PYTHON := '+sys.executable+'\nCALLIOPE_CONFIG_HEADER := project audio.h\nCALLIOPE_BUILD_DIR := output\nCALLIOPE_DRIVER_ASM := driver.s\nOUT := output\ninclude '+home+'/make/calliope.mk\nprobe: calliope-config\n\t"$(CALLIOPE_PYTHON)" -c "import os;print(\'TIMER=\'+os.environ[\'CALLIOPE_PCM_TIMER\'])"\n')
        command=[str(sgdk_tool(SDK, 'make')),'-f',str(make),'probe']
        result=subprocess.run(command,cwd=self.root,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('TIMER=1020',result.stdout)
        first=(self.root/'output/calliope_build.h').stat().st_mtime_ns
        result=subprocess.run(command,cwd=self.root,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertEqual(first,(self.root/'output/calliope_build.h').stat().st_mtime_ns);self.assertNotIn('configure.py',result.stdout)
        result=subprocess.run(command+['CALLIOPE_HYBRID=0','CALLIOPE_PCM_TIMER=1020'],cwd=self.root,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('TIMER=1016',result.stdout)
        self.assertFalse(json.loads((self.root/'output/calliope_config.json').read_text())['hybrid'])
        original=make.read_text();make.write_text('CALLIOPE_HYBRID := 0\n'+original)
        result=subprocess.run(command,cwd=self.root,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('TIMER=1016',result.stdout)
        result=subprocess.run(command+['CALLIOPE_HYBRID=1'],cwd=self.root,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('TIMER=1020',result.stdout)
        make.write_text(original)
        # Edit the input header, rather than the CLI, and observe the same switch.
        self.header.write_text('#define CALLIOPE_HYBRID 0\n')
        result=subprocess.run(command,cwd=self.root,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('TIMER=1016',result.stdout)
    def test_bank_wrappers_nested_headers_and_idle_builds(self):
        shutil.copytree(ROOT/'examples/minimal/audio', self.root/'audio')
        nested=self.root/'nested audio.h'
        nested.write_text('#ifndef CALLIOPE_HYBRID\n#define CALLIOPE_HYBRID 1\n#endif\n')
        self.header.write_text('#include "nested audio.h"\n')
        make=self.root/'Makefile'
        make.write_text('GDK := '+SDK.as_posix()+'\nCALLIOPE_HOME := '+ROOT.as_posix()+'\nCALLIOPE_PYTHON := '+sys.executable+'\nCALLIOPE_CONFIG_HEADER := project audio.h\nCALLIOPE_BUILD_DIR := output\nCALLIOPE_DRIVER_ASM := output/driver.s\nCALLIOPE_SOURCE_WRAPPERS := 1\nCALLIOPE_BANK := audio/manifest.json\nOUT := output\ninclude '+ROOT.as_posix()+'/make/calliope.mk\nprobe: calliope-config\n')
        command=[str(sgdk_tool(SDK,'make')),'-f',str(make),'probe']
        def run(*options):
            result=subprocess.run(command+list(options),cwd=self.root,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            return result
        run()
        output=self.root/'output'
        before={p:p.stat().st_mtime_ns for p in output.rglob('*')if p.is_file()}
        self.assertNotIn('configure.py',run().stdout)
        self.assertEqual(before,{p:p.stat().st_mtime_ns for p in before})
        for name in ('driver.s','audio.h','adapter.c','configure.d'):
            (output/name).unlink()
            self.assertIn('configure.py',run().stdout)
            self.assertTrue((output/name).exists())
            self.assertNotIn('configure.py',run().stdout)
        source=self.root/'audio/click.wav'
        time.sleep(1.1);source.touch()
        bank=(output/'bank.bin').stat().st_mtime_ns
        self.assertIn('configure.py',run().stdout)
        self.assertEqual(bank,(output/'bank.bin').stat().st_mtime_ns)
        self.assertNotIn('configure.py',run().stdout)
        time.sleep(1.1)
        nested.write_text('#ifndef CALLIOPE_HYBRID\n#define CALLIOPE_HYBRID 0\n#endif\n')
        run();self.assertFalse(json.loads((output/'calliope_config.json').read_text())['hybrid'])
        run('CALLIOPE_HYBRID=1')
        self.assertTrue(json.loads((output/'calliope_config.json').read_text())['hybrid'])
        run();self.assertFalse(json.loads((output/'calliope_config.json').read_text())['hybrid'])
        original=(output/'bank.bin').read_bytes()
        run('CALLIOPE_REGION=pal')
        self.assertEqual(json.loads((output/'calliope_config.json').read_text())['region'],'pal')
        run();self.assertEqual((output/'bank.bin').read_bytes(),original)
if __name__=='__main__':unittest.main()
