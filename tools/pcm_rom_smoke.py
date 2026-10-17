"""Check ROM PCM bytes across bank boundaries, forward loops and video DMA."""
import argparse,ctypes as C,hashlib,json
from pathlib import Path
from unittest.mock import patch
from calliope.furnace import parse,Cell
from calliope.compiler import compile_bank
from calliope.pcm import RATES
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--dma',action='store_true');p.add_argument('--pal',action='store_true');a=p.parse_args()
    region='pal' if a.pal else 'ntsc'
    config=json.loads((ROOT/'out/build-report.json').read_text())
    assert config['hybrid'] and not config['host_pcm'] and config['region']==region
    out=ROOT/'out'/('pcm-rom-'+region+('-dma' if a.dma else ''));out.mkdir(exist_ok=True)
    source=ROOT/'examples/sonic_greenhillzone_annotated.fur';m=parse(source);s=m.subsongs[0]
    s.rows=128;s.speeds=[4];s.hz=60;s.orders=[[0] for _ in m.channels]
    m.patterns={(0,5,0):{0:Cell(note=108,effects=[(0,0x17,1)]),127:Cell(effects=[(0,0xff,0)])}}
    # Nonzero signed bytes distinguish every source sample from FIFO silence.
    m.samples=[{'name':'bank loop','frames':70001,'rate':round(RATES[region]),'center_rate':round(RATES[region]),'depth':8,'data':bytes(16+i%80 for i in range(70001)),'loop':[32765,65543],'direction':0}]
    manifest=out/'bank.json';manifest.write_text(json.dumps({'version':1,'songs':[{'id':5,'name':'pcm_banks','source':str(source),'voices':[{'channel':5,'slot':'pcm'}]}]}))
    with patch('calliope.compiler.parse',return_value=m):bank,report,_=compile_bank(manifest,region)
    sample=report['music_samples'][0];data=bank[sample['offset']:sample['offset']+sample['bytes']];start,end=sample['loop']
    assert sample['bytes']>65536 and len(data)>end>start
    assert 128 not in data
    raw=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=raw.index(b'BGM1');raw[base:base+len(bank)]=bank
    rom=out/'test.bin';rom.write_bytes(raw)
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':region.encode()})
    actual=bytearray()
    def record(t,v,p,c):
        if v!=128:actual.append(v)
    cb=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record);h.core.clp_trace_set.argtypes=[type(cb)];h.core.clp_trace_set(cb)
    try:
        h.run(700 if a.pal else 840,(3,) if a.dma else ())
        # A looping descriptor plays to loop-end first, then repeats loop-start/end.
        expected=bytearray(data[:end])
        while len(expected)<len(actual):expected.extend(data[start:end])
        assert len(actual)>end+(end-start),('insufficient loop coverage',len(actual),start,end)
        assert actual==expected[:len(actual)],next((i,actual[i],expected[i]) for i in range(len(actual)) if actual[i]!=expected[i])
        needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50];state=h.save();at=state.find(needle)-0x38;assert at>=0;ram=state[at:at+8192]
        assert not ram[0x1d05] and not ram[0x1d1a],(ram[0x1d05],ram[0x1d1a])
        assert ram[0xff8:0x1000]==bytes([165]*8) and ram[0x1f78:0x1f80]==bytes([165]*8)
        assert ram[0x1d16]!=0 and ram[0x1d0e]==0,'finite PCM song did not complete'
        result={'rom_sha256':hashlib.sha256(raw).hexdigest(),'region':region,'dma':a.dma,'sample_offset':sample['offset'],'sample_bytes':len(data),'loop':[start,end],'verified_dac_samples':len(actual),'fault':0,'underrun':0,'guards':True,'completed':True}
        (out/'report.json').write_text(json.dumps(result,indent=2)+'\n');print(result)
    finally:h.close()
if __name__=='__main__':main()
