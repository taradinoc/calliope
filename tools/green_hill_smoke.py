"""Run the complete authored Green Hill Zone route with concurrent effects."""
import argparse,hashlib,json,re,wave
from pathlib import Path
import numpy as np
from libretro_host import Host
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--hybrid',action='store_true');p.add_argument('--pal',action='store_true');p.add_argument('--frames',type=int,default=12000);p.add_argument('--core',default='genesis_plus_gx');p.add_argument('--stress',action='store_true');p.add_argument('--dma',action='store_true');p.add_argument('--multi-psg-sfx',action='store_true',help='include periodic four-channel PSG effects');p.add_argument('--multi-fm-sfx',action='store_true',help='include periodic five-channel FM effects');args=p.parse_args()
    region='pal' if args.pal else 'ntsc';out=ROOT/'out'/('green-hill-'+Path(args.core).stem+'-'+region+('-stress' if args.stress else '')+('-multi-psg' if args.multi_psg_sfx else '')+('-multi-fm' if args.multi_fm_sfx else ''));out.mkdir(exist_ok=True)
    host=Host(args.core,ROOT/'audition/out/rom.bin',out,{b'genesis_plus_gx_region_detect':region.encode(),b'blastem_region':b'E' if args.pal else b'U'})
    audio=[];host.audio_sink=audio.append
    if args.multi_psg_sfx or args.multi_fm_sfx:host.core.retro_set_controller_port_device(0,513)
    symbols=(ROOT/'audition/out/symbol.txt').read_text()
    def word(name):
        ram=host.memory();at=int(re.search(r'^([0-9a-f]+) \w '+name+r'$',symbols,re.M)[1],16)&65535
        return (ram[at^1]<<8)|ram[(at+1)^1]
    try:
        host.run(100)
        for frame in range(args.frames):
            buttons=[]
            if args.stress:
                if frame%2==0:buttons.append(7)
                if frame%120==0:buttons.append(8)
                if frame%180==0:buttons.append(6)
            if args.multi_psg_sfx and frame%240==60:buttons=[2,5]
            if args.multi_fm_sfx and frame%240==90:buttons=[2,1]
            if args.dma:buttons.append(3)
            host.run(1,tuple(buttons))
            if frame%300==0:assert word('gsTestFault')==0 and word('gsTestPcmUnderrun')==0,(frame,word('gsTestFault'),word('gsTestPcmUnderrun'))
        samples=np.frombuffer(b''.join(audio),dtype='<i2').reshape(-1,2)
        result={'rom_sha256':hashlib.sha256((ROOT/'audition/out/rom.bin').read_bytes()).hexdigest(),'hybrid':args.hybrid,'region':region,'frames':args.frames,'stress':args.stress,'dma':args.dma,'multi_psg_sfx':args.multi_psg_sfx,'multi_fm_sfx':args.multi_fm_sfx,'fault':word('gsTestFault'),'underrun':word('gsTestPcmUnderrun'),'completed':word('gsTestCompleted'),'events':word('gsTestEvents'),'last_event':word('gsTestLastId'),'last_source_channel':word('gsTestLastParameter'),'tail_rms':float(np.std(samples[-44100:]))}
        assert not result['fault'] and not result['underrun'] and not result['completed'],result
        assert result['events']>10 and result['tail_rms']>100,result
        if 'genesis_plus_gx' in Path(args.core).stem:
            needle=(ROOT/'out/driver.bin').read_bytes()[0x38:0x50];state=host.save();at=state.find(needle)-0x38;assert at>=0;ram=state[at:at+8192]
            assert ram[(0xff8 if args.hybrid else 0x1738):(0x1000 if args.hybrid else 0x1740)]==bytes([165]*8) and ram[0x1f78:0x1f80]==bytes([165]*8)
            result['ticks']=int.from_bytes(ram[0x1d0a:0x1d0e],'little')
            assert result['ticks']>=args.frames*(1.19 if args.pal else .99),result
            result['guards']=True
        with wave.open(str(out/'capture.wav'),'wb') as w:
            w.setnchannels(2);w.setsampwidth(2);w.setframerate(round(host.sample_rate));w.writeframes(samples.tobytes())
        host.screenshot().save(out/'screen.png');(out/'report.json').write_text(json.dumps(result,indent=2)+'\n');print(result)
    finally:host.close()
if __name__=='__main__':main()
