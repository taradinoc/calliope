"""Compare new music traces with the committed BGM1-11 compiler/reference.

The baseline is the Mercurial parent, so run before committing this migration.
PCM addresses are normalized by sample bytes and loop bounds, not ROM position.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import types
from calliope.compiler import compile_bank
from calliope.reference import Player

ROOT=Path(__file__).resolve().parents[1]

def historical(name):
    source=subprocess.check_output(['hg','cat','tools/calliope/'+name+'.py'],cwd=ROOT)
    module=types.ModuleType('calliope.baseline_'+name);module.__package__='calliope'
    exec(compile(source,'baseline_'+name+'.py','exec'),module.__dict__)
    return module

def normalize(trace,bank):
    groups={}
    for event in trace:
        op=event[1]
        slot=(5+((event[2][0]>>5)&3)) if op in (0x30,0x35) else 8 if op==0x33 else 9 if op in (0x31,0x32) else event[2] if op in (0x10,0x11,0x13,0x36,0x37) else 10
        # The fixture's tone2 is reserved for derived noise; preserve their
        # mutual ordering. Different independent voices can change write order.
        if slot==7:slot=8
        if event[1]==0x31:
            p=event[2];d=bank[p:p+16]
            address=int.from_bytes(d[4:7],'little');size=int.from_bytes(d[7:10],'little')
            event=event[:2]+(hashlib.sha256(bank[address:address+size]).hexdigest(),d[10:16].hex())
        groups.setdefault((event[0],slot),[]).append(event)
    return [(key,events) for key,events in sorted(groups.items())]

def main():
    baseline=historical('compiler');old_player=historical('reference').Player
    results=[]
    for region in ('ntsc','pal'):
        old,old_report,_=baseline.compile_bank(ROOT/'examples/bank.json',region)
        new,new_report,_=compile_bank(ROOT/'examples/bank.json',region)
        (ROOT/'out'/('channel-baseline-'+region+'.bin')).write_bytes(old)
        songs=json.loads((ROOT/'examples/bank.json').read_text())['songs']
        for song in songs:
            before=old_player(old);after=Player(new)
            before.start(song['id']);after.start(song['id'])
            for _ in range(18000):before.clock();after.clock()
            a=normalize(before.trace,old);b=normalize(after.trace,new)
            if a!=b:
                mismatch=next((i for i,(x,y) in enumerate(zip(a,b)) if x!=y),min(len(a),len(b)))
                raise AssertionError((region,song['name'],mismatch,a[mismatch:mismatch+3],b[mismatch:mismatch+3]))
            assert before.events==after.events and before.lost==after.lost
            assert before.tick==after.tick and before.playing==after.playing
        results.append({'region':region,'old_bank_bytes':len(old),'new_bank_bytes':len(new),
                        'saved_bytes':len(old)-len(new),'music_clocks_per_song':18000,
                        'ordered_per_channel_music_traces_equal':True,'cues_and_tick_counts_equal':True,
                        'baseline_sha256':old_report['sha256'],'new_sha256':new_report['sha256']})
    path=ROOT/'out/channel-comparison.json';path.write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))

if __name__=='__main__':main()
