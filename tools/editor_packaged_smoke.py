"""Test a frozen editor's worker protocol without an installed Python runtime."""
import base64
import argparse
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import sys
import threading
import time
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def clean_environment(executable):
    env=dict(os.environ)
    for key in tuple(env):
        if key.startswith(('PYTHON','CALLIOPE_')) or key=='GDK':env.pop(key,None)
    if sys.platform=='win32':env['PATH']=str(executable.parent)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32')
    return env

def verify_banks(executable):
    from calliope.editor.services import compile_document
    document=json.loads((ROOT/'examples/bank.json').read_text())
    with tempfile.TemporaryDirectory(prefix='calliope-package-parity-') as directory:
        for mode in ('z80','hybrid'):
            for region in ('ntsc','pal'):
                expected,_,header=compile_document(document,ROOT/'examples',region,mode)
                output=Path(directory)/f'{mode}-{region}.bin'
                request=dict(action='export',document=document,base_dir=str(ROOT/'examples'),region=region,mode=mode,revision=0,output=str(output))
                result=subprocess.run([str(executable),'--worker'],input=json.dumps(request)+'\n',text=True,encoding='utf-8',capture_output=True,timeout=120,env=clean_environment(executable),cwd=directory)
                assert result.returncode==0,(result.stderr,result.stdout)
                assert output.exists(),result.stdout
                assert output.read_bytes()==expected,f'Packaged converter differs: {mode}/{region}'
                assert output.with_suffix('.h').read_text()==header
                print(f'Bank-byte parity passed: {mode}/{region}',flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable',type=Path)
    parser.add_argument('--mode',choices=('z80','hybrid'))
    parser.add_argument('--region',choices=('ntsc','pal'),default='ntsc')
    args=parser.parse_args();executable=args.executable.resolve()
    if not args.mode:
        verify_banks(executable)
        for mode in ('z80','hybrid'):
            for region in ('ntsc','pal'):
                subprocess.run([sys.executable,__file__,str(executable),'--mode',mode,'--region',region],check=True)
        return
    request=dict(action='preview',document=json.loads((ROOT/'examples/bank.json').read_text()),base_dir=str(ROOT/'examples'),region=args.region,mode=args.mode,revision=1,assets=str(executable.parent/'assets'))
    env=clean_environment(executable)
    process=subprocess.Popen([str(executable),'--worker'],cwd=executable.parent,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    lines=queue.Queue();errors=[]
    def reader():
        try:
            for line in process.stdout:lines.put(line)
        finally:lines.put(None)
    threading.Thread(target=reader,daemon=True).start()
    threading.Thread(target=lambda:errors.append(process.stderr.read()),daemon=True).start()
    def send(value):process.stdin.write(json.dumps(value)+'\n');process.stdin.flush()
    send(request);peak=0;frames=0;ready=False;started=time.monotonic()
    try:
        while time.monotonic()-started<45:
            line=lines.get(timeout=45)
            if line is None:raise RuntimeError('Packaged worker ended early: '+''.join(errors))
            value=json.loads(line)
            if value['type']=='error':raise RuntimeError(value['message'])
            if value['type']=='ready':ready=True;send({'command':[1,5]})
            if value['type']=='audio':
                data=base64.b64decode(value['data']);samples=struct.unpack('<'+str(len(data)//2)+'h',data)
                peak=max(peak,max((abs(n) for n in samples),default=0));frames+=1
                if frames==60:send({'command':[6,3,2,99]})
                if frames>=120:break
        assert ready and frames>=120 and peak>100,(ready,frames,peak)
        print(json.dumps({'mode':args.mode,'region':args.region,'ready':ready,'frames':frames,'audio_peak':peak,'stderr':''.join(errors)}))
    finally:
        if process.poll() is None:
            send({'quit':True})
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        process.stdin.close()

if __name__=='__main__':main()
