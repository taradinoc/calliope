"""Prepare four version-bound, bank-injectable player ROMs (release tool)."""
from toolchain import sgdk_path
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def symbol(text,name):
    match=re.search(r'^([0-9a-fA-F]+)\s+\w\s+'+name+r'$',text,re.M)
    if not match: raise RuntimeError('missing player symbol '+name)
    return int(match[1],16)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sgdk',type=Path,default=None)
    p.add_argument('--output',type=Path,default=ROOT/'editor/assets')
    a=p.parse_args();a.sgdk=sgdk_path(a.sgdk);a.output.mkdir(parents=True,exist_ok=True)
    for mode in ('z80','hybrid'):
        for region in ('ntsc','pal'):
            stage=ROOT/'out/editor-player'/f'{mode}-{region}'
            if stage.resolve().parent != (ROOT/'out/editor-player').resolve():raise ValueError('Invalid player staging directory')
            if stage.exists():shutil.rmtree(stage)
            for directory in ('src','inc','res'): (stage/directory).mkdir(parents=True,exist_ok=True)
            (stage/'bank.json').write_text('{"version":1,"songs":[],"sfx":[]}\n')
            (stage/'res/audio.res').write_text('// Bank generated directly by Calliope.\n')
            main=(ROOT/'editor/player/src/main.c').read_text().replace('ADAPTER_SOURCE',(ROOT/'src/calliope.c').as_posix())
            (stage/'src/main.c').write_text(main)
            (stage/'Makefile').write_text(f'GDK ?= {a.sgdk.as_posix()}\nCALLIOPE_HOME := {ROOT.as_posix()}\nCALLIOPE_SOURCE_WRAPPERS := 0\nCALLIOPE_BANK := bank.json\ninclude $(GDK)/makefile.gen\ninclude {ROOT.as_posix()}/make/calliope.mk\n')
            # The adapter lives in main.c for version-bound hybrid telemetry.
            (stage/'src/bus.s').write_text('.include "'+(ROOT/'src/hybrid_bus.s').as_posix()+'"\n')
            command=[sys.executable,str(ROOT/'tools/build_editor_player_stage.py'),str(stage),'--sgdk',str(a.sgdk),'--region',region,'--hybrid' if mode=='hybrid' else '--pure-z80']
            subprocess.run(command,check=True)
            symbols=(stage/'out/symbol.txt').read_text();raw=(stage/'out/rom.bin').read_bytes()
            bank=symbol(symbols,'clp_bank');mailbox=symbol(symbols,'clpEditorMailbox');length=symbol(symbols,'clpEditorBankLength')
            if len(raw)>0x100000 or length+4>len(raw): raise RuntimeError('template overlaps bank slot')
            metadata={'protocol':1,'mode':mode,'region':region,'pcm_timer':1020 if mode=='hybrid' else 1016,
                      'bank_address':0x100000,'mailbox_address':mailbox&0xffffff,'bank_length_address':length,
                      'sha256':hashlib.sha256(raw).hexdigest(),'bank_revision':12}
            stem=a.output/f'{mode}-{region}'
            stem.with_suffix('.bin').write_bytes(raw)
            stem.with_suffix('.json').write_text(json.dumps(metadata,indent=2)+'\n')
            print('Prepared',stem)

if __name__=='__main__':main()
