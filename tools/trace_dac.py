"""Record DAC master-clock intervals and their assembled Z80 call sites."""
import argparse
import hashlib
import ctypes as C
import json
from pathlib import Path
import re
import numpy as np
from libretro_host import Host
from calliope.pcm import RATES,DAC_CLOCKS
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--timer',type=int,default=1016);p.add_argument('--strict',action='store_true',help='fail unless rate error <=0.1% and every interval <=2 nominal periods');p.add_argument('--retrigger',action='store_true');p.add_argument('--no-dma',action='store_true');p.add_argument('--rom',type=Path,default=ROOT/'out/pcm-stress-genesis_plus_gx-ntsc-loop/stress.bin');a=p.parse_args()
    out=ROOT/'out/dac-trace';out.mkdir(exist_ok=True)
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',a.rom,out,{b'genesis_plus_gx_region_detect':b'ntsc'})
    records=[];frame=0
    longest={'cycles':0}
    if hasattr(h.core,'clp_history'):
        h.core.clp_history.restype=C.POINTER(C.c_uint)
        h.core.clp_history_index.restype=C.c_uint
        if hasattr(h.core,"clp_history_cycles"):h.core.clp_history_cycles.restype=C.POINTER(C.c_uint)
    def record(cycles,value,pc,caller):
        now=frame*3420*262+cycles
        if records and frame>=100 and hasattr(h.core,'clp_history'):
            gap=now-records[-1][0]
            if gap>longest['cycles']:
                end=h.core.clp_history_index();history=h.core.clp_history()
                longest.update(cycles=gap,frame=frame,pc=pc,caller=caller,
                               history=[history[i&2047] for i in range(max(0,end-256),end)])
                if hasattr(h.core,"clp_history_cycles"):
                    clocks=h.core.clp_history_cycles()
                    longest["instruction_master_cycles"]=[clocks[i&2047] for i in range(max(0,end-256),end)]
        records.append((now,value,pc,caller))
    callback=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record)
    h.core.clp_trace_set.argtypes=[type(callback)];h.core.clp_trace_set(callback)
    try:
        for frame in range(300):
            buttons=((4,) if frame==100 else ((7,) if frame>101 and frame%4==0 else ())) if a.retrigger else (() if a.no_dma else (3,))
            h.run(1,buttons)
        (out/'longest.json').write_text(json.dumps(longest,indent=2)+'\n')
        data=np.array(records,dtype=np.int64);np.save(out/'writes.npy',data)
        # Discard startup and inspect the sustained music/DMA window.
        data=data[(data[:,0]>=100*3420*262)&(data[:,0]<280*3420*262)]
        delta=np.diff(data[:,0]);callers=data[1:,3]
        sites=[]
        for caller in np.unique(callers):
            d=delta[callers==caller];sites.append({'caller':hex(int(caller)),'count':len(d),'mean_z80_cycles':float(d.mean()/15),'max_z80_cycles':float(d.max()/15)})
        sites.sort(key=lambda x:x['mean_z80_cycles'],reverse=True)
        report={'rom_sha256':hashlib.sha256(a.rom.read_bytes()).hexdigest(),'retrigger':a.retrigger,'dma':not a.no_dma and not a.retrigger,'writes':len(data),'effective_rate':float(53693175/np.mean(delta)),'interval_percentiles_z80':(np.percentile(delta,[0,50,90,99,100])/15).tolist(),'sites':sites}
        expected=53693175/7/(144*(1024-a.timer))
        report['expected_rate']=expected
        report['rate_error_percent']=100*(report['effective_rate']/expected-1)
        report['clock_gate']=abs(report['rate_error_percent'])<=0.1 and float(delta.max())<=2*7*144*(1024-a.timer)
        if hasattr(h.core,'clp_profile'):
            h.core.clp_profile.restype=C.POINTER(C.c_ulonglong)
            (out/'instruction-cycles.json').write_text(json.dumps(list(h.core.clp_profile()[:8192]))+'\n')
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
        if a.strict and not report['clock_gate']:raise AssertionError('DAC clock acceptance failed; see report.json')
    finally:h.close()
if __name__=='__main__':main()
