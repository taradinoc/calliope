"""Build a standalone editor using pyside6-deploy on the target OS/architecture."""
import argparse
import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
local=ROOT/'out/editor-deps'
if local.is_dir():sys.path.insert(0,str(local))

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--dry-run',action='store_true');parser.add_argument('--name',default='Calliope Bank Editor');a=parser.parse_args()
    import PySide6
    from calliope.editor.preview import core_path
    core_path(ROOT/'editor/assets')  # Fail before packaging an incomplete release.
    for profile in ('z80-ntsc','z80-pal','hybrid-ntsc','hybrid-pal'):
        if not (ROOT/'editor/assets'/f'{profile}.bin').exists():raise SystemExit('Build player templates first')
    stage=ROOT/'out/editor-package-source'
    # Stale modules or assets must never survive into a new distributable.
    for directory in (stage, ROOT/'out/editor-release'):
        if directory.resolve().parent != (ROOT/'out').resolve():
            raise ValueError('Package staging must remain inside the project out directory')
        if directory.exists():shutil.rmtree(directory)
    stage.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/'tools/calliope_editor.py',stage/'calliope_editor.py')
    shutil.copyfile(ROOT/'tools/clpconvert.py',stage/'clpconvert.py')
    shutil.copytree(ROOT/'tools/calliope',stage/'calliope',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.orig'))
    shutil.copytree(ROOT/'editor/assets',stage/'assets',dirs_exist_ok=True)
    shutil.copyfile(ROOT/'editor/THIRD_PARTY.md',stage/'assets/THIRD_PARTY.md')
    for info in Path(PySide6.__file__).parent.parent.glob('*.dist-info'):
        if info.name.lower().startswith(('pyside6','shiboken6')) and (info/'licenses').is_dir():
            shutil.copytree(info/'licenses',stage/'assets/qt-licenses'/info.name,dirs_exist_ok=True)
    deploy=Path(PySide6.__file__).parent/'scripts'
    config=configparser.ConfigParser();config.read(deploy/'deploy_lib/default.spec')
    config['app'].update(title=a.name,project_dir=str(stage),input_file=str(stage/'calliope_editor.py'),exec_directory=str(ROOT/'out/editor-release'))
    config['qt'].update(modules='Core,Gui,Widgets,Multimedia,Network',plugins='multimedia')
    # Keep the bank converter as ordinary Python modules in the bundled CPython
    # runtime. Native translation changed the generated FM command streams in
    # the previous release; bank-byte parity is a release gate below.
    converter_modules=[p.stem for p in (ROOT/'tools/calliope').glob('*.py') if p.stem!='__init__']
    args=['--include-package=calliope.editor','--include-module=clpconvert',
          '--nofollow-import-to='+','.join('calliope.'+name for name in converter_modules),
          '--include-data-dir='+str(stage/'assets').replace('\\','/')+'=assets','--assume-yes-for-downloads','--noinclude-qt-translations',
          '--no-deployment-flag=excluded-module-usage']
    args+=['--include-module='+name for name in ('math','wave','zlib','dataclasses','bisect')]
    if sys.platform=='win32':args+=['--windows-console-mode=attach','--msvc=latest']
    config['nuitka'].update(mode='standalone',extra_args=' '.join('"'+arg+'"' if ' ' in arg else arg for arg in args))
    spec=stage/'pysidedeploy.spec'
    with spec.open('w') as f:config.write(f)
    env=dict(os.environ);env['PYTHONPATH']=os.pathsep.join([str(local),str(stage),env.get('PYTHONPATH','')])
    # Keep compiler caches local, including MSVC discovery locks, so packaging
    # does not depend on another project's cache state or user-profile access.
    env.setdefault('NUITKA_CACHE_DIR',str(ROOT/'out/nuitka-cache'))
    command=[sys.executable,str(deploy/'deploy.py'),str(stage/'calliope_editor.py'),'-c',str(spec),'--force','--no-install','--keep-deployment-files']
    if a.dry_run:command+=['--dry-run']
    subprocess.run(command,cwd=stage,env=env,check=True)
    if not a.dry_run:
        destination=ROOT/'out/editor-release'/(a.name+('.app' if sys.platform=='darwin' else '.dist'))
        if sys.platform=='darwin':destination=destination/'Contents/MacOS'
        if not destination.exists():raise SystemExit('Deployment tool did not create the expected standalone output')
        # Nuitka intentionally excludes binary libraries and .bin files from data
        # directory inclusion. Install the verified native core and ROMs explicitly.
        shutil.copytree(stage/'assets',destination/'assets',dirs_exist_ok=True)
        (destination/'calliope').mkdir(exist_ok=True)
        for name in converter_modules:shutil.copyfile(stage/'calliope'/(name+'.py'),destination/'calliope'/(name+'.py'))
        shutil.copyfile(ROOT/'docs/editor-standalone.md',destination/'README.md')
        for name in ('LICENSE','THIRD_PARTY.md','VERSION'):
            shutil.copyfile(ROOT/name,destination/name)
        executable=destination/('calliope_editor.exe' if sys.platform=='win32' else 'calliope_editor' if sys.platform=='darwin' else 'calliope_editor.bin')
        subprocess.run([sys.executable,str(ROOT/'tools/editor_packaged_smoke.py'),str(executable)],env=env,check=True)
        subprocess.run([sys.executable,str(ROOT/'tools/editor_audio_regression.py'),'--executable',str(executable),'--mute-pcm'],env=env,check=True)
        print('Standalone editor with player assets:',destination)

if __name__=='__main__':main()
