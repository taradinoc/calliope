"""Play Elevator for a complete order-list loop and retain an audio capture."""
import argparse
import json
from pathlib import Path
import re
import wave
import numpy as np
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--pal',action='store_true');p.add_argument('--core',default='genesis_plus_gx');a=p.parse_args()
    out=ROOT/'out'/('elevator-'+Path(a.core).stem+('-pal' if a.pal else '-ntsc'));out.mkdir(exist_ok=True)
    rom=ROOT/'audition/out/rom.bin';symbols=(rom.parent/'symbol.txt').read_text()
    options={b'genesis_plus_gx_region_detect':b'pal' if a.pal else b'ntsc',b'blastem_region':b'E' if a.pal else b'U'}
    h=Host(a.core,rom,out,options)
    chunks=[];h.audio_sink=chunks.append
    try:
        h.run(100);h.run(2,(4,));h.run(2250 if not a.pal else 1900)
        ram=h.memory()
        def word(name):
            address=int(re.search(r'^([0-9a-f]+) \w '+name+r'$',symbols,re.M)[1],16)&65535
            return (ram[address^1]<<8)|ram[(address+1)^1]
        audio=np.frombuffer(b''.join(chunks),dtype='<i2').reshape(-1,2)
        report={name:word(name) for name in ('gsTestFault','gsTestPcmUnderrun','gsTestCompleted')}
        report['peak']=int(np.abs(audio.astype('i4')).max());report['tail_rms']=float(np.sqrt(np.mean(audio[-44100:].astype('f8')**2)))
        with wave.open(str(out/'capture.wav'),'wb') as w:
            w.setnchannels(2);w.setsampwidth(2);w.setframerate(round(h.sample_rate));w.writeframes(audio.tobytes())
        h.screenshot().save(out/'screen.png')
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
        assert report['gsTestFault']==0 and report['gsTestCompleted']==0,'song stopped/faulted'
        assert report['tail_rms']>100,'silent output'
        assert report['gsTestPcmUnderrun']==0,'PCM underrun during mixed track'
    finally:h.close()
if __name__=='__main__':main()
