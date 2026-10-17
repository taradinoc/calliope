"""Exercise the assembled PCM driver with long, deliberately unaligned ROM data.
Only generated audition copies are patched; source assets are never overwritten.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import numpy as np
from libretro_host import Host
from calliope.pcm import RATES
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(); p.add_argument('--core',default='genesis_plus_gx'); p.add_argument('--pal',action='store_true'); p.add_argument('--loop',action='store_true'); p.add_argument('--no-dma',action='store_true'); p.add_argument('--frames',type=int,default=800); a=p.parse_args()
    out=ROOT/'out'/('pcm-stress-'+Path(a.core).stem+('-pal' if a.pal else '-ntsc')+('-loop' if a.loop else '-finite')+('-no-dma' if a.no_dma else '')); out.mkdir(parents=True,exist_ok=True)
    symbols=(ROOT/'audition/out/symbol.txt').read_text()
    def symbol(name): return int(re.search(r'^([0-9a-f]+) \w '+name+r'$',symbols,re.M)[1],16)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes()); base=symbol('clp_bank')
    desc=base+int.from_bytes(raw[base+13:base+16],'little')
    song=base+int.from_bytes(raw[base+10:base+13],'little')
    raw[song+3]=0
    # Keep FM sequencing/patch-write load, but isolate the DAC calibration tone.
    driver=symbol('clp_driver')
    raw[driver+0x1f54]=127 # retain YM writes while attenuating every carrier
    raw[driver+0x1f55]=15
    if not a.loop: raw[driver+0x1f17]=2 # suppress later drums for the finite-tail check
    address=((len(raw)+32767)//32768)*32768+0x7fe0; length=65597
    samples=(np.rint(128+31*np.sin(np.arange(length)*2*np.pi/64))).astype('u1').tobytes()
    extent=((address+length+131071)//131072)*131072
    raw.extend(bytes(max(0,extent-len(raw))))
    raw[address:address+length]=samples
    raw[desc+4:desc+7]=(address-base).to_bytes(3,'little')
    raw[desc+7:desc+10]=length.to_bytes(3,'little')
    raw[desc+10:desc+13]=(31 if a.loop else 0).to_bytes(3,'little')
    raw[desc+13:desc+16]=(length-17 if a.loop else 0).to_bytes(3,'little')
    raw[0x1a4:0x1a8]=(len(raw)-1).to_bytes(4,'big')
    raw[0x18e:0x190]=(sum(int.from_bytes(raw[i:i+2],'big') for i in range(0x200,len(raw),2))&65535).to_bytes(2,'big')
    rom=out/'stress.bin'; rom.write_bytes(raw)
    options={b'genesis_plus_gx_region_detect':b'pal' if a.pal else b'ntsc',b'blastem_region':b'E' if a.pal else b'U'}
    h=Host(a.core,rom,out,options); chunks=[]; h.audio_sink=chunks.append
    try:
        h.run(a.frames,() if a.no_dma else (3,)); ram=h.memory()
        def word(name):
            at=symbol(name)&65535
            return int.from_bytes(bytes((ram[at^1],ram[(at+1)^1])),'big')
        report={name:word(name) for name in ('gsTestFault','gsTestPcmUnderrun','gsTestCompleted','gsTestEvents')}
        audio=np.frombuffer(b''.join(chunks),dtype='<i2').reshape(-1,2).mean(axis=1)
        # The first seconds also contain FM. Measure the isolated DAC window.
        window=audio[round(3*h.sample_rate):round(4*h.sample_rate)]
        spectrum=np.abs(np.fft.rfft((window-window.mean())*np.hanning(len(window))))
        peak=int(spectrum[1:].argmax()+1)
        # Interpolate the Hann-window peak instead of rounding to whole Hz.
        left,center,right=np.log(np.maximum(spectrum[peak-1:peak+2],1e-30))
        offset=.5*(left-right)/(left-2*center+right)
        frequency=(peak+offset)*h.sample_rate/len(window)
        rate=RATES['pal' if a.pal else 'ntsc']
        report.update(frequency=frequency,expected_frequency=rate/64,tail_rms=float(np.std(audio[-round(h.sample_rate):])),rom_sha256=hashlib.sha256(raw).hexdigest(),loop=a.loop,frames=a.frames,dma=not a.no_dma)
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n',newline='\r\n'); print(json.dumps(report,indent=2))
        assert report['gsTestFault']==0 and report['gsTestPcmUnderrun']==0,'driver fault/underrun'
        assert abs(frequency-rate/64)<rate/64*.03,'DAC sample-rate drift'
        assert report['gsTestCompleted']==0 and report['gsTestEvents']>=2,'looped music did not progress'
        if a.loop:
            assert report['tail_rms']>100,'PCM loop must remain audible'
        else:
            # FM can remain audible after PCM ends. Inspect the DAC FIFO itself.
            state=h.save();needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
            at=state.find(needle)-0x38;assert at>=0,'Z80 RAM signature missing'
            zram=state[at:at+8192]
            assert not zram[0x1eac] and not zram[0x1ec0],'finite PCM did not release ownership'
            assert zram[0x1c00:0x1d00]==bytes([128])*256,'finite PCM FIFO did not become silent'
    finally: h.close()
if __name__=='__main__': main()
