"""Measure PCM/FM/PSG onsets through the assembled driver, including FIFO wrap."""
import argparse,json,hashlib,ctypes as C
from pathlib import Path
from unittest.mock import patch
from calliope.furnace import parse,Cell
from calliope.compiler import compile_bank
from calliope.pcm import RATES
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--hybrid',action='store_true');p.add_argument('--pal',action='store_true');p.add_argument('--dma',action='store_true');args=p.parse_args()
region='pal' if args.pal else 'ntsc';lines=313 if args.pal else 262;master=53203424 if args.pal else 53693175
out=ROOT/'out'/('pcm-alignment-'+region+('-dma' if args.dma else ''));out.mkdir(exist_ok=True)
source=ROOT/'examples/sonic_greenhillzone_annotated.fur';m=parse(source);s=m.subsongs[0];s.rows=8;s.speeds=[4];s.hz=60;s.orders=[[0] for _ in m.channels]
m.instruments[0]['features'].pop('MA',None)
m.patterns={(0,0,0):{0:Cell(note=108,instrument=0),2:Cell(note=180),4:Cell(note=109),6:Cell(note=180)},(0,6,0):{0:Cell(note=108,volume=15),2:Cell(note=180),4:Cell(note=109),6:Cell(note=180)},(0,5,0):{0:Cell(note=108,effects=[(0,0x17,1)]),2:Cell(note=180),4:Cell(note=109),6:Cell(note=180)}}
m.samples=[{'name':str(v),'frames':384,'rate':round(RATES[region]),'center_rate':round(RATES[region]),'depth':8,'data':bytes([v&255])*384,'loop':None,'direction':0} for v in (64,-64)]
content={'version':1,'songs':[{'id':5,'name':'align','source':str(source),'voices':[{'channel':0,'slot':'fm0'},{'channel':5,'slot':'pcm'},{'channel':6,'slot':'tone0'}]}]};manifest=out/'bank.json';manifest.write_text(json.dumps(content))
with patch('calliope.compiler.parse',return_value=m):data,report,_=compile_bank(manifest,region)
r=bytearray((ROOT/'audition/out/rom.bin').read_bytes());base=r.index(b'BGM1');r[base:base+len(data)]=data;rom=out/'test.bin';rom.write_bytes(r)
h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,out,{b'genesis_plus_gx_region_detect':region.encode()})
frame=0;dac=[];chips=[]
cb=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)
a=cb(lambda t,v,p,c:dac.append((frame*3420*lines+t,v)))
b=cb(lambda t,r,v,p:chips.append((frame*3420*lines+t,r,v)))
h.core.clp_trace_set.argtypes=[cb];h.core.clp_trace_set(a);h.core.clp_chip_trace_set.argtypes=[cb];h.core.clp_chip_trace_set(b)
try:
 for frame in range(180):h.run(1,(3,) if args.dma else ())
 onsets=[(t,v) for i,(t,v) in enumerate(dac) if v in (192,64) and (i==0 or dac[i-1][1]!=v)]
 gates=[(t,v) for t,r,v in chips if r==0x28 and v==0xf0]
 tone=[(t,v) for t,r,v in chips if r==0x200 and v&0xf0==0x80]
 assert len(onsets)==2 and len(gates)==2,(onsets,gates)
 deltas=[]
 for t,v in onsets:
  fm=(min(gates,key=lambda x:abs(x[0]-t))[0]-t)*1000/master
  psg=(min(tone,key=lambda x:abs(x[0]-t))[0]-t)*1000/master
  deltas.append({'pcm_value':v,'fm_after_pcm_ms':fm,'psg_after_pcm_ms':psg})
 needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50];state=h.save();at=state.find(needle)-0x38;assert at>=0;ram=state[at:at+8192]
 assert not ram[0x1d05] and not ram[0x1d1a],(ram[0x1d05],ram[0x1d1a])
 assert (ram[0xff8:0x1000] if args.hybrid else ram[0x1738:0x1740])==bytes([165]*8) and ram[0x1f78:0x1f80]==bytes([165]*8)
 assert (ram[0x1d16]!=0 if args.hybrid else ram[0x1eec]==0) and ram[0x1d0e]==0,'finite song must complete'
 result={'rom_sha256':hashlib.sha256(rom.read_bytes()).hexdigest(),'region':region,'hybrid':args.hybrid,'dma':args.dma,'onsets':deltas,'fault':0,'underrun':0,'guards':True,'alignment_gate':all(0<=d['fm_after_pcm_ms']<2.5 and 0<=d['psg_after_pcm_ms']<2.5 for d in deltas)}
 (out/'report.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
 assert result['alignment_gate'],'onset alignment failed; see report.json'
finally:h.close()
