"""Track decoder backlog through the assembled driver, optionally against a ROM."""
import argparse, ctypes as C, hashlib, json
from pathlib import Path
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--rom',type=Path,default=ROOT/'audition/out/rom.bin');p.add_argument('--baseline',type=Path);p.add_argument('--pal',action='store_true');p.add_argument('--frames',type=int,default=6000);p.add_argument('--report',type=Path,default=ROOT/'out/music-hitch-report.json');a=p.parse_args()
if a.frames<1:p.error('--frames must be positive')
def measure(rom):
    region='pal' if a.pal else 'ntsc'
    h=Host(ROOT/'out/trace-core/build/calliope_trace.dll',rom,ROOT/'out',{b'genesis_plus_gx_region_detect':region.encode()})
    h.core.clp_zram.restype=C.POINTER(C.c_ubyte)
    try:
        h.run(100);z=bytes(h.core.clp_zram()[:8192]);previous=int.from_bytes(z[0x1d0a:0x1d0e],'little')
        stalled=busy=longest=run=maximum=queue=0;failure=None
        for frame in range(a.frames):
            h.run(1);z=bytes(h.core.clp_zram()[:8192]);tick=int.from_bytes(z[0x1d0a:0x1d0e],'little')
            stalled+=tick==previous
            if tick==previous and z[0x1e80]:busy+=1;run+=1
            else:run=0
            longest=max(longest,run);maximum=max(maximum,z[0x1e80]);queue=max(queue,z[0x1eec]);previous=tick
            if z[0x1d05] or z[0x1d1a]:reason='driver fault/PCM underrun'
            elif z[0x1eec]>128:reason='register queue exceeds capacity'
            elif z[0x1738:0x1740]!=bytes([165]*8) or z[0x1f78:0x1f80]!=bytes([165]*8):reason='stack guard changed'
            else:continue
            failure={'frame_after_warmup':frame,'absolute_frame':frame+100,'reason':reason,
                     'song_tick':tick,'order':z[0x1e8b],
                     'dac_empty_caller':hex(int.from_bytes(z[0x1d1c:0x1d1e],'little'))}
            break
        result=dict(frames=frame+1,requested_frames=a.frames,stalled_frames=stalled,busy_stalled_frames=busy,longest_busy_stall=longest,max_pending=maximum,max_register_queue=queue,fault=z[0x1d05],underrun=z[0x1d1a])
        if failure:result['failure']=failure
        return result
    finally:h.close()
result={'current':measure(a.rom),'rom_sha256':hashlib.sha256(a.rom.read_bytes()).hexdigest()}
if a.baseline:
    result['baseline']=measure(a.baseline)
    result['baseline_rom_sha256']=hashlib.sha256(a.baseline.read_bytes()).hexdigest()
# Preserve rejection evidence before the acceptance assertions.
print(json.dumps(result,indent=2));a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(result,indent=2)+'\n')
for measured in ('current','baseline'):
    if measured in result:assert not result[measured].get('failure'),result
if a.baseline:
    assert result['current']['busy_stalled_frames']<result['baseline']['busy_stalled_frames'],result
    assert result['current']['longest_busy_stall']<=result['baseline']['longest_busy_stall'],result
if a.frames==6000 and not a.pal:
    assert result['current']['stalled_frames']<=150 and result['current']['max_pending']<=3 and result['current']['longest_busy_stall']<=3,result
