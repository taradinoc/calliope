"""Independent Furnace WAV check of legacy PSG E5 detune; no VGM export."""
from toolchain import furnace_path
import argparse,json,struct,subprocess,wave,zlib
from pathlib import Path
import numpy as np
from make_fixtures import make
from calliope.furnace import parse
from calliope.psg import tone_period
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--furnace',type=Path,default=None);args=p.parse_args();args.furnace=furnace_path(args.furnace)
    out=ROOT/'out/partial-psg-oracle';out.mkdir(exist_ok=True)
    raw=bytearray(zlib.decompress(make(232,3,notes=(108,),event=False,initial_effects={0:(0xe5,192)},speeds=(30,))))
    info=struct.unpack_from('<I',raw,20)[0];flags=raw.index(struct.pack('<f',440),info)+4;raw[flags+1]=1
    source=out/'tone.fur';source.write_bytes(zlib.compress(raw));audio=out/'tone.wav'
    with (out/'export.log').open('w') as log:subprocess.run([str(args.furnace),'-console','-loops','1','-output',str(audio),str(source)],stdout=log,stderr=subprocess.STDOUT,check=True)
    with wave.open(str(audio),'rb') as w:
        assert w.getsampwidth()==2;rate=w.getframerate();a=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').reshape(-1,w.getnchannels())
    x=a[int(rate*.1):int(rate*.7)].mean(axis=1);x-=x.mean()
    indices=np.flatnonzero((x[:-1]<0)&(x[1:]>=0));cross=indices-x[indices]/(x[indices+1]-x[indices]);measured=rate/np.mean(np.diff(cross))
    period=tone_period(108.5,parse(source),'ntsc',64);expected=53693175/15/(32*period)
    result={'period':period,'expected_hz':expected,'furnace_hz':measured,'error_percent':100*(measured/expected-1)}
    (out/'report.json').write_text(json.dumps(result,indent=2)+'\n');print(result)
    assert abs(result['error_percent'])<.05,result
if __name__=='__main__':main()
