"""Sample the audition's 68000 audio-service flag at actual DAC writes.
DAC occupancy is a sampling estimate. A rebuilt core also counts 68000
instruction/bus cycles while the audio-service flag is set.
"""
import argparse,ctypes as C,hashlib,json,re
from collections import Counter
from pathlib import Path
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--frames',type=int,default=3000);p.add_argument('--pal',action='store_true');p.add_argument('--stress',action='store_true');p.add_argument('--dma',action='store_true');a=p.parse_args()
    report=json.loads((ROOT/'out/build-report.json').read_text());assert report['hybrid'] and report['region']==('pal' if a.pal else 'ntsc')
    out=ROOT/'out/hybrid-profile';out.mkdir(exist_ok=True)
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',ROOT/'audition/out/rom.bin',out,{b'genesis_plus_gx_region_detect':report['region'].encode()})
    symbols=(ROOT/'audition/out/symbol.txt').read_text();syms={n:int(x,16)&65535 for x,n in re.findall(r'^([0-9a-f]+) \w (\w+)$',symbols,re.M)}
    hist=Counter();h.core.clp_main_pc.restype=C.c_uint
    ram=C.cast(h.core.retro_get_memory_data(2),C.POINTER(C.c_ubyte));h.core.clp_pcm_positions.restype=C.c_uint
    h.core.clp_zram.restype=C.POINTER(C.c_ubyte);zram=h.core.clp_zram()
    def value(n,size=2):return int.from_bytes(bytes(ram[(syms[n]+i)^1] for i in range(size)),'big')
    count=busy=0;minimum=1024;maximum=0;frame=0
    def record(cycles,sample,pc,caller):
        nonlocal count,busy,minimum,maximum
        positions=h.core.clp_pcm_positions();producer=zram[0x1e87]|(zram[0x1e88]<<8)
        depth=(producer-positions)&1023
        hist[h.core.clp_main_pc()]+=1
        count+=1;busy+=bool(value('gsTestAudioActive'));minimum=min(minimum,depth);maximum=max(maximum,depth)
    cb=C.CFUNCTYPE(None,C.c_uint,C.c_uint,C.c_uint,C.c_uint)(record);h.core.clp_trace_set.argtypes=[type(cb)]
    instruction_profile=hasattr(h.core,'clp_audio_profile_cycles')
    if instruction_profile:
        h.core.clp_audio_profile_reset.argtypes=[C.c_uint]
        h.core.clp_audio_profile_cycles.restype=C.POINTER(C.c_ulonglong)
    try:
        h.run(150);start=value('gsTestFrames',4);h.core.clp_trace_set(cb)
        if instruction_profile:h.core.clp_audio_profile_reset(syms['gsTestAudioActive'])
        for frame in range(a.frames):
            buttons=[3] if a.dma else []
            if a.stress:
                if frame%2==0:buttons.append(7)
                if frame%120==0:buttons.append(8)
                if frame%180==0:buttons.append(6)
            h.run(1,tuple(buttons))
        rendered=value('gsTestFrames',4)-start
        result=dict(host_pcm=report['host_pcm'],rom_sha256=hashlib.sha256((ROOT/'audition/out/rom.bin').read_bytes()).hexdigest(),region=report['region'],frames=a.frames,stress=a.stress,dma=a.dma,displayed_frames=rendered,displayed_fps=rendered/a.frames*h.video_hz,dac_writes=count,audio_service_occupancy_percent=100*busy/count,audio_service_estimated_ms_per_frame=1000*busy/count/h.video_hz,minimum_pcm_depth=minimum,maximum_pcm_depth=maximum,fault=value('gsTestFault'),underrun=value('gsTestPcmUnderrun'))
        if instruction_profile:
            cycles=h.core.clp_audio_profile_cycles()[:32768]
            total=sum(cycles);master=53203424 if a.pal else 53693175;lines=313 if a.pal else 262
            result['instruction_audio_frame_percent']=100*total/(a.frames*3420*lines)
            result['instruction_audio_ms_per_frame']=1000*total/(a.frames*master)
            functions=sorted((int(x,16),n) for x,n in re.findall(r'^([0-9a-f]+) [tT] (\S+)',symbols,re.M) if int(x,16)<65536)
            breakdown=[]
            for i,(at,name) in enumerate(functions):
                end=functions[i+1][0] if i+1<len(functions) else 65536
                cost=sum(cycles[at//2:end//2])
                if cost:breakdown.append(dict(name=name,master_cycles=cost,frame_percent=100*cost/(a.frames*3420*lines)))
            result['instruction_functions']=sorted(breakdown,key=lambda item:item['master_cycles'],reverse=True)
        (out/'main-pc.json').write_text(json.dumps(hist.most_common(200)))
        (out/('report-'+report['region']+('-stress' if a.stress else '')+'.json')).write_text(json.dumps(result,indent=2)+'\n');print(result)
        assert not result['fault'] and not result['underrun'],result
        if not a.pal:assert result['displayed_fps']>=59,result
    finally:h.close()
if __name__=='__main__':main()
