"""Build and archive the pinned preview core on the current OS/architecture."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
REVISION='393f70cd64c7334564c19a424c383f53986c13f4'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=ROOT/'out/editor-core-source')
    p.add_argument('--output',type=Path,default=ROOT/'editor/assets')
    p.add_argument('--generator',default=None)
    a=p.parse_args();source=a.source.resolve();out=ROOT/'out'/('editor-core-build-'+(a.generator or 'default').replace(' ','-'));a.output.mkdir(parents=True,exist_ok=True)
    revision=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if revision!=REVISION or subprocess.check_output(['git','-C',str(source),'status','--porcelain']).strip():
        raise SystemExit('Core source must be clean and pinned at '+REVISION)
    args=['cmake','-S',str(ROOT/'editor/core'),'-B',str(out),'-DCORE_SOURCE='+source.as_posix(),'-DCMAKE_BUILD_TYPE=Release']
    if a.generator:args+=['-G',a.generator]
    subprocess.run(args,check=True);subprocess.run(['cmake','--build',str(out),'--config','Release','--parallel','4'],check=True)
    suffix='.dll' if sys.platform=='win32' else '.dylib' if sys.platform=='darwin' else '.so'
    built=next(out.rglob('genesis_plus_gx'+suffix));target=a.output/built.name;shutil.copyfile(built,target)
    shutil.copyfile(source/'LICENSE.txt',a.output/'GENESIS-PLUS-GX-LICENSE.txt')
    subprocess.run(['git','-C',str(source),'archive','--format=zip','--prefix=Genesis-Plus-GX/','--output='+str(a.output.resolve()/'genesis-plus-gx-source.zip'),REVISION],check=True)
    with zipfile.ZipFile(a.output/'genesis-plus-gx-source.zip','a',zipfile.ZIP_DEFLATED) as archive:
        for f in (ROOT/'editor/core').iterdir():archive.write(f,'calliope-core-build/'+f.name)
        archive.write(Path(__file__),'calliope-core-build/build_editor_core.py')
    (a.output/'core.json').write_text(json.dumps({'upstream':'https://github.com/libretro/Genesis-Plus-GX','revision':revision,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'modifications':'Read-only Z80 RAM export and observational 68000 cycle profiler; Calliope CMake build; see bundled source archive.'},indent=2)+'\n')

if __name__=='__main__':main()
