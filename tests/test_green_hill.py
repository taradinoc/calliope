import json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from calliope.compiler import compile_bank
from calliope.furnace import parse,Cell,FormatError
from calliope.psg import VolumeEnvelope,tone_period
from calliope.reference import Player

class GreenHill(unittest.TestCase):
    def manifest(self,root,tone=False):
        voices=[{'name':str(i),'channel':i,'slot':slot} for i,slot in enumerate(('fm0','fm1','fm2','fm3','fm4','pcm','tone0','tone1','tone2','noise')) if i!=8 or tone]
        path=Path(root)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[{'id':5,'name':'ghz','source':str(ROOT/'examples/sonic_greenhillzone_annotated.fur'),'voices':voices,'groups':{'all':[v['name'] for v in voices]},'pcm_gain':.9,'external_commands':'passthrough'}]}));return path
    def test_complete_song_regions_and_raw_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=self.manifest(tmp)
            for region in ('ntsc','pal'):
                data,report,_=compile_bank(path,region)
                self.assertEqual(report['sample_variants'],2)
                self.assertEqual(len(report['rows']),1824)
                self.assertTrue(all(r['bytes']<=192 for r in report['rows']))
                descriptor=int.from_bytes(data[10:13],'little')
                self.assertTrue(data[descriptor+7]&128,'tone3 must be reserved')
                player=Player(data);player.start(5)
                cues=[]
                for _ in range(12000):
                    player.step();cues.extend(player.events);player.events.clear()
                self.assertTrue(player.playing)
                self.assertTrue(any(e[2]==0xa1 for e in cues))
                self.assertTrue(any(e[2] in (0xca,0xcb,0xcc) and e[3]==5 for e in cues))
                self.assertTrue(any(e[1]==0x33 and e[2]&7==7 for e in player.trace))
                self.assertTrue(any(e[1]==0x30 and 0xc1 in e[2] for e in player.trace))
    def test_derived_noise_reserves_entire_tone3_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FormatError,'reserves tone2'):
                compile_bank(self.manifest(tmp,True))
    def test_sfx_loop_requires_vibrato_reset(self):
        from calliope.sfx import apply_loop
        module=parse(ROOT/'examples/fm_ring.fur');song=module.subsongs[0]
        song.rows=2;song.speeds=[1];song.orders=[[0] for _ in module.channels]
        module.flags['compat_legacy'][6]=0
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0),1:Cell(effects=[(0,4,0x48)])}}
        item={'loop':{'start':[0,0],'end':[0,2]}}
        with self.assertRaisesRegex(FormatError,'reset vibrato'):apply_loop(bytes((0,2,255)),item,module,0,[0])
        module.patterns[0,0,0][0].effects=[(0,4,0)]
        self.assertEqual(apply_loop(bytes((0,2,255)),item,module,0,[0])[1],0)

    def test_partial_psg_detune_vibrato_and_reset(self):
        module=parse(ROOT/'examples/sonic_greenhillzone_annotated.fur')
        env=VolumeEnvelope(True,False,partial=True)
        env.begin(Cell(note=108,effects=[(0,4,0x48)]),None)
        values=[]
        for _ in range(4):
            env.tick();values.append(tone_period(env.pitch_note,module,'ntsc',env.effective_fine))
        self.assertEqual(values,[105,104,104,103])
        env.begin(Cell(note=108,effects=[(0,4,0),(1,0xe5,192)]),None);env.tick()
        self.assertEqual(tone_period(env.pitch_note,module,'ntsc',env.effective_fine),103)
        env.begin(Cell(note=180),None);self.assertEqual(env.tick(),15)
        self.assertFalse(env.active)

if __name__=='__main__':unittest.main()
