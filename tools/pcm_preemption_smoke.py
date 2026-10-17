"""Verify exclusive PCM playback, full amplitude, and silence through DAC writes."""
import argparse
import ctypes as C
import json
import hashlib
from pathlib import Path
from calliope.compiler import compile_bank
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--hybrid',action='store_true');args=parser.parse_args()
    out=ROOT/'out'/('pcm-preemption-hybrid' if args.hybrid else 'pcm-preemption');out.mkdir(exist_ok=True)
    bank,report,_=compile_bank(ROOT/'examples/bank.json')
    config=json.loads((ROOT/'out/build-report.json').read_text());assert config['hybrid']==args.hybrid
    depth_limit=(896 if config['host_pcm'] else 1023) if args.hybrid else 255
    original=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=original.index(b'BGM1')
    original[base:base+len(bank)]=bank
    results=[]
    for music,sfx in ((100,100),(-100,-100),(100,-100),(-128,127)):
        raw=bytearray(original)
        for sample in report['music_samples']:
            address=sample['offset']
            length=sample['bytes']
            raw[base+address:base+address+length]=bytes([(music+128)&255])*length
        for effect in report['effects']:
            if effect['kind']=='pcm':
                at=base+effect['offset'];raw[at:at+effect['length']]=bytes([(sfx+128)&255])*effect['length']
        rom=out/'mix.bin';rom.write_bytes(raw)
        host=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':b'ntsc'})
        values=set(); depths=[]
        host.core.clp_dac_fifo.restype=C.c_uint
        if args.hybrid:host.core.clp_pcm_positions.restype=C.c_uint
        def record(clocks,value,pc,caller):
            values.add(value)
            if args.hybrid:
                fifo=host.core.clp_pcm_positions();depths.append(((fifo>>16)-fifo)&1023)
            else:
                fifo=host.core.clp_dac_fifo();depths.append(((fifo>>8)-(fifo&255))&255)
        callback=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record)
        host.core.clp_trace_set.argtypes=[type(callback)];host.core.clp_trace_set(callback)
        try:
            host.run(100)
            values.clear();depths.clear() # omit the audition startup test that plays SFX on both voices
            for frame in range(400):host.run(1,(7,3) if frame%2==0 else (3,))
            expected=sfx+128
            allowed={128,music+128,sfx+128}
            assert expected in values,(music,sfx,expected,values)
            assert music+128 in values,(music,'single voice amplitude',values)
            assert max(depths)<=depth_limit,('PCM lookahead exceeded',max(depths))
            assert values<=allowed,(music,sfx,'unexpected DAC values',values)
            needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50]
            state=host.save();at=state.find(needle)-0x38;assert at>=0;ram=state[at:at+8192]
            assert ram[0x1d05]==0 and ram[0x1d1a]==0,('fault/underrun',ram[0x1d05],ram[0x1d1a])
            assert (ram[0xff8:0x1000] if args.hybrid else ram[0x1738:0x1740])==bytes([165]*8) and ram[0x1f78:0x1f80]==bytes([165]*8)
            results.append({'rom_sha256':hashlib.sha256(raw).hexdigest(),'host_pcm':config['host_pcm'],'music':music,'sfx':sfx,'expected_sfx':expected,'dac_values':sorted(values),'max_queued_samples':max(depths),'fault':0,'underrun':0})
        finally:host.close()
    (out/'report.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
if __name__=='__main__':main()
