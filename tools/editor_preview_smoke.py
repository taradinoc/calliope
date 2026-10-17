"""Actual bundled player/core checks. Each profile gets its own process."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys
from calliope.editor.services import compile_document
from calliope.editor.preview import make_rom,Player,core_path

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=('z80','hybrid'));parser.add_argument('--region',choices=('ntsc','pal'));a=parser.parse_args()
    if not a.mode:
        for mode in ('z80','hybrid'):
            for region in ('ntsc','pal'):subprocess.run([sys.executable,__file__,'--mode',mode,'--region',region],check=True)
        return
    m=json.loads((ROOT/'examples/bank.json').read_text())
    # Force ring effects onto a single music channel to make arbitration visible.
    for item in m['sfx']:
        if item['name']=='fm_ring':item['resource_mask']=1
    data,_,_=compile_document(m,ROOT/'examples',a.region,a.mode)
    raw,meta=make_rom(data,ROOT/'editor/assets',a.region,a.mode)
    player=Player(core_path(ROOT/'editor/assets'),raw,meta);player.write(24,b'\x01')
    def frames(n):return b''.join(player.frame() for _ in range(n))
    def command(*args):
        player.command(*args);frames(12);return player.status()
    try:
        command(1,5);audio=frames(120)
        samples=struct.unpack('<'+str(len(audio)//2)+'h',audio)
        assert max(abs(v) for v in samples)>100,'silent music'
        state=command(6,3,2,100);assert state['owners'][0]>0,state
        state=command(6,3,2,101);assert any(e['active'] and e['token']==100 for e in state['effects']),state
        assert not any(e['active'] and e['token']==101 for e in state['effects']),state
        state=command(6,3,3,102);assert any(e['active'] and e['token']==102 for e in state['effects']),state
        state=command(8,102);assert not any(e['active'] and e['token']==102 for e in state['effects']),state
        assert state['playing'] and not state['owners'][0],state
        # Atomic multi-channel ownership, cancellation, persistent PSG loop.
        state=command(6,10,4,103);assert all(state['owners'][i] for i in range(5)),state
        command(8,103)
        state=command(6,8,4,104);assert state['owners'][7] and state['owners'][8],state
        state=command(8,104);assert not state['owners'][7] and not state['owners'][8],state
        assert not command(16,127,1,0)['queued'], 'invalid tempo was accepted'
        advances=[]
        for scale in (128,512):
            state=command(16,scale,1,0);assert state['queued'],state
            before=state['tick'];frames(120);advances.append(player.status()['tick']-before)
        assert abs(advances[1]-4*advances[0])<=10,(a.mode,a.region,advances)
        assert command(16,256,1,0)['queued']
        command(7,5,2,105)
        command(2);frames(30);command(3);frames(30)
        state=command(5);assert not state['playing'] and not state['fault'],state
        assert not any(e['active'] for e in state['effects']),state
        print(json.dumps({'mode':a.mode,'region':a.region,'sample_rate':player.sample_rate,'fps':player.fps,'peak':max(abs(v) for v in samples),'checks':'music, stealing, equal-priority rejection, preemption, cancellation, multi-FM, coupled PSG, PCM, tempo scaling, pause/resume, stop','status':state}))
    finally:player.close()

if __name__=='__main__':main()
