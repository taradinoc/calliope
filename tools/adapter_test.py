"""Compile the real 68000 adapter against a host mailbox/SGDK stub using MSVC."""
from toolchain import msvc_path
from pathlib import Path
import argparse,os,sys
import subprocess
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--vcvars',type=Path);p.add_argument('--hybrid',action='store_true');g=p.add_mutually_exclusive_group();g.add_argument('--host-pcm',action='store_true');g.add_argument('--z80-pcm',action='store_true');a=p.parse_args()
    if (a.z80_pcm or a.host_pcm) and not a.hybrid:p.error('PCM selection requires --hybrid')
    if a.hybrid:
        subprocess.run([sys.executable,str(ROOT/'tools/clpconvert.py'),'compile',str(ROOT/'examples/bank.json'),'--output',str(ROOT/'out/hybrid-test-bank.bin')],env=dict(os.environ,CALLIOPE_PCM_TIMER='1020'),check=True)
    source='hybrid_test.c' if a.hybrid else 'adapter_test.c'
    args=' "'+str(ROOT/'out/hybrid-test-bank.bin')+'"' if a.hybrid else ''
    out=ROOT/'out/adapter-test';out.mkdir(parents=True,exist_ok=True)
    vc=msvc_path(a.vcvars)
    if not vc.exists():raise RuntimeError('MSVC x86 build environment unavailable')
    define=('/DCALLIOPE_HOST_PCM='+str(int(a.host_pcm))+' ') if a.hybrid else ''
    batch=f'@echo off\ncall "{vc}"\nif errorlevel 1 exit /b 1\ncl /nologo /std:c11 /W4 /WX {define}/I"{ROOT}/tests/adapter" /I"{ROOT}/include" "{ROOT}/tests/adapter/{source}" /Fe:adapter_test.exe\nif errorlevel 1 exit /b 1\nadapter_test.exe{args}\n'
    (out/'run.bat').write_text(batch,newline='\r\n')
    subprocess.run(['cmd','/c',str(out/'run.bat')],cwd=out,check=True)
if __name__=='__main__':main()
