"""Play the untouched GHZ asset's isolated legacy DAC track with rapid PCM SFX."""
import os
import argparse,json
from pathlib import Path
from unittest.mock import patch
import numpy as np
from libretro_host import Host
from calliope.furnace import parse
from calliope.compiler import compile_bank
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--core',default=os.environ.get('CALLIOPE_CORE','genesis_plus_gx'));p.add_argument('--pal',action='store_true');args=p.parse_args()
    region='pal' if args.pal else 'ntsc';out=ROOT/'out'/('legacy-pcm-'+region);out.mkdir(exist_ok=True)
    source=ROOT/'examples/sonic_greenhillzone_annotated.fur';module=parse(source)
    module.patterns={key:value for key,value in module.patterns.items() if key[1]==5}
    manifest=out/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[{'id':2,'name':'drums','external_commands':'passthrough','pcm_gain':0.9,'source':str(source),'voices':[{'channel':5,'slot':'pcm'}],'loop_order':0}],'sfx':[{'id':5,'name':'chu','source':str(ROOT/'examples/chu.wav')}]}))
    with patch('calliope.compiler.parse',return_value=module):data,report,_=compile_bank(manifest,region)
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1');raw[base:base+len(data)]=data
    rom=out/'legacy.bin';rom.write_bytes(raw)
    host=Host(args.core,rom,out,{b'genesis_plus_gx_region_detect':region.encode()})
    audio=[];host.audio_sink=audio.append
    needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
    try:
        host.run(100);host.run(1,(4,));host.run(300)
        energy=float(np.std(np.frombuffer(b''.join(audio),dtype='<i2')));assert energy>100,energy
        for frame in range(3000):host.run(1,(7,) if frame%2==0 else ())
        state=host.save();at=state.find(needle)-0x38;assert at>=0;ram=state[at:at+8192]
        assert not ram[0x1d05] and not ram[0x1d1a],(ram[0x1d05],ram[0x1d1a])
        assert ram[0x1738:0x1740]==bytes([165]*8) and ram[0x1f78:0x1f80]==bytes([165]*8)
        result={'region':region,'sample_variants':report['sample_variants'],'drum_rms':energy,'rapid_sfx_frames':3000,'fault':0,'underrun':0}
        (out/'report.json').write_text(json.dumps(result,indent=2)+'\n');print(result)
    finally:host.close()
if __name__=='__main__':main()
