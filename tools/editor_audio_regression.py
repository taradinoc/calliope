"""Check pitched audio independently of PCM, including the packaged worker."""
import argparse
import base64
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--executable',type=Path);parser.add_argument('--bank',type=Path,default=ROOT/'examples/bank.json');parser.add_argument('--song',default='green_hill_zone');parser.add_argument('--mode',default='z80');parser.add_argument('--mute-pcm',action='store_true');parser.add_argument('--whole-bank',action='store_true');a=parser.parse_args()
    document=json.loads(a.bank.read_text());song=next(i for i in document['songs'] if i['name']==a.song)
    if not a.whole_bank:document=dict(version=1,songs=[song])
    executable=a.executable.resolve() if a.executable else None
    command=[str(executable),'--worker'] if executable else [sys.executable,str(ROOT/'tools/calliope_editor.py'),'--worker']
    assets=executable.parent/'assets' if executable else ROOT/'editor/assets'
    request=dict(action='preview',document=document,base_dir=str(a.bank.resolve().parent),region='ntsc',mode=a.mode,revision=1,assets=str(assets),cache_dir=str(ROOT/'out/editor-audio-cache'))
    env=dict(os.environ);env.pop('CALLIOPE_EDITOR_EXECUTABLE',None)
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',env=env)
    lines=queue.Queue();errors=[]
    def read():
        try:
            for line in process.stdout:lines.put(line)
        finally:lines.put(None)
    threading.Thread(target=read,daemon=True).start();threading.Thread(target=lambda:errors.append(process.stderr.read()),daemon=True).start()
    def send(value):process.stdin.write(json.dumps(value)+'\n');process.stdin.flush()
    send(request);peak=0;frames=0;started=time.perf_counter();ready=None
    try:
        while frames<120:
            line=lines.get(timeout=120)
            if line is None:raise RuntimeError('Worker ended: '+''.join(errors))
            value=json.loads(line)
            if value['type']=='error':raise RuntimeError(value['message'])
            if value['type']=='ready':
                ready=time.perf_counter()-started;send({'command':[1,song['id']]})
                if a.mute_pcm:send({'command':[11,512]})
            if value['type']=='audio':
                frames+=1
                if frames>12:
                    data=base64.b64decode(value['data']);samples=struct.unpack('<'+str(len(data)//2)+'h',data)
                    peak=max(peak,max(map(abs,samples),default=0))
        print(json.dumps(dict(song=a.song,mode=a.mode,mute_pcm=a.mute_pcm,peak=peak,ready_seconds=ready,packaged=bool(executable))),flush=True)
        assert peak>100,'Pitched audio is silent'
    finally:
        if process.poll() is None:
            try:send({'quit':True})
            except (OSError,BrokenPipeError):pass
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
if __name__=='__main__':main()
