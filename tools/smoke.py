"""Verify actual cartridge audio, cue delivery and completion in libretro."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import wave
import numpy as np
from libretro_host import Host
from audition_fixture import hide_song
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser(); p.add_argument('--core',default='genesis_plus_gx'); p.add_argument('--pal',action='store_true'); a=p.parse_args()
    out=ROOT/'out'/('smoke-'+Path(a.core).stem+('-pal' if a.pal else '-ntsc')); out.mkdir(parents=True,exist_ok=True)
    rom=ROOT/'audition/out/rom.bin'; symbols=(rom.parent/'symbol.txt').read_text()
    fixture=out/'completion.bin';fixture.write_bytes(hide_song(bytearray(rom.read_bytes()),5));rom=fixture
    options={b'genesis_plus_gx_region_detect':b'pal' if a.pal else b'ntsc',b'blastem_region':b'E' if a.pal else b'U'}
    host=Host(a.core,rom,out,options)
    chunks=[]; host.audio_sink=chunks.append
    try:
        host.run(240)
        ram=host.memory()
        def word(name):
            m=re.search(r'^([0-9a-f]+) \w '+name+r'$',symbols,re.M); assert m,name
            addr=int(m[1],16)&65535
            # Both installed little-endian cores expose word-swapped 68000 RAM.
            xor=1
            return int.from_bytes(bytes((ram[addr^xor],ram[(addr+1)^xor])),'big')
        values={n:word(n) for n in ('gsTestEvents','gsTestCompleted','gsTestFault','gsTestLastId','gsTestLastParameter','gsTestPcmUnderrun')}
        audio=np.frombuffer(b''.join(chunks),dtype='<i2').reshape(-1,2)
        values.update(peak=int(np.abs(audio.astype(np.int32)).max()),video_hz=host.video_hz,rom_sha256=hashlib.sha256(rom.read_bytes()).hexdigest())
        with wave.open(str(out/'capture.wav'),'wb') as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(round(host.sample_rate)); w.writeframes(audio.tobytes())
        host.screenshot().save(out/'screen.png')
        (out/'report.json').write_text(json.dumps(values,indent=2)+'\n',newline='\r\n')
        print(json.dumps(values,indent=2))
        assert values['gsTestEvents']==1 and values['gsTestLastId']==7 and values['gsTestLastParameter']==42,'cue delivery'
        assert values['gsTestCompleted']==1,'completion'
        assert values['gsTestFault']==0,'driver fault'
        assert values['gsTestPcmUnderrun']==0,'PCM underrun'
        assert values['peak']>100,'silent capture'
    finally: host.close()
if __name__=='__main__': main()
