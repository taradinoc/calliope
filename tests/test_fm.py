import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
patch_module=patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from calliope.fm import frequency,Pitch
from calliope.furnace import Cell,FormatError,parse
from calliope.compiler import compile_bank
from calliope.reference import Player

# Arpeggio: 0, +12 every two ticks. Pitch: 0, +64 every tick.
MACRO=bytes((8,0,1,2,0,255,0,64,0,2,0,12,4,2,0,255,0,64,0,1,0,64,255))

class FMPitch(unittest.TestCase):
    def test_operator_level_effects_use_register_order_and_persist(self):
        from calliope.fm import Level
        import copy
        ins=copy.deepcopy(parse(ROOT/'examples/fm_ring.fur').instruments[0])
        raw=bytearray(ins['features']['FM']);raw[0]=0xf4;raw[1]=0x70
        # All operators follow channel volume; use distinct authored TLs.
        for op in range(4):raw[5+op*8+4]=(raw[5+op*8+4]&31)|32
        ins['features']['FM']=bytes(raw)
        level=Level()
        level.begin(Cell(note=108,instrument=0,volume=100,
                         effects=[(i,0x12+i,10+i*10) for i in range(4)]),ins,True)
        self.assertEqual(level.tick()[:4],bytes((37,57,47,67)))
        level.begin(Cell(note=110,instrument=0),ins,True)
        self.assertEqual(level.operator_levels,[10,30,20,40])
        level.begin(Cell(volume=127,effects=[(0,0x13,0)]),ins,False)
        self.assertEqual(level.tick()[:4],bytes((10,30,0,40)))
        level.begin(Cell(note=112,instrument=1),ins,True)
        self.assertEqual(level.operator_levels,[None]*4)
        self.assertEqual(raw,bytearray(ins['features']['FM']))

    def test_operator_level_validation_and_disabled_operators(self):
        from calliope.fm import Level
        import copy
        ins=copy.deepcopy(parse(ROOT/'examples/fm_ring.fur').instruments[0])
        raw=bytearray(ins['features']['FM']);raw[0]=4
        ins['features']['FM']=bytes(raw)
        for effect in range(0x12,0x16):
            level=Level()
            level.begin(Cell(note=108,instrument=0,effects=[(0,effect,0)]),ins,True)
            self.assertEqual(level.tick()[:4],bytes((127,)*4))
            for value in (-1,128,None):
                with self.assertRaisesRegex(FormatError,'operator level'):
                    level.begin(Cell(effects=[(0,effect,value)]),ins,False)

    def test_operator_levels_compile_for_music_and_sfx_without_new_patches(self):
        module=parse(ROOT/'examples/fm_ring.fur')
        song=module.subsongs[0];song.rows=4;song.speeds=[1];song.orders=[[0] for _ in module.channels]
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0),
            1:Cell(effects=[(i,0x12+i,20+i) for i in range(4)]),
            2:Cell(note=110),3:Cell(note=180)}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json'
            for category in ('songs','sfx'):
                path.write_text(json.dumps({'version':1,category:[{'id':1,'name':'fm','source':str(ROOT/'examples/fm_ring.fur'),'voices':[{'channel':0,'slot':'fm0'}]}]}))
                with patch_module('calliope.compiler.parse',return_value=module):
                    data,report,_=compile_bank(path)
                self.assertEqual(report['patches'],1)
                if category=='songs':
                    player=Player(data);player.start(1)
                    for _ in range(5):player.step()
                    self.assertTrue(any(e[1]==0x37 for e in player.trace))
                else:
                    from test_fm_sfx import timeline
                    _,operations=timeline(data[report['effects'][0]['offset']:])
                    updates=[e for e in operations if e[1]==0x37]
                    self.assertEqual(updates[1][0],1)
                    self.assertEqual(updates[1][3][:4],bytes((20,22,21,23)))

    def test_operator_level_loop_requires_preserved_or_reset_state(self):
        module=parse(ROOT/'examples/fm_ring.fur')
        song=module.subsongs[0];song.rows=2;song.speeds=[1];song.orders=[[0] for _ in module.channels]
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0),
                                 1:Cell(effects=[(0,0x13,20)])}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json'
            path.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'fm','loop_order':0,'source':str(ROOT/'examples/fm_ring.fur'),'voices':[{'channel':0,'slot':'fm0'}]}]}))
            with patch_module('calliope.compiler.parse',return_value=module):
                with self.assertRaisesRegex(FormatError,'reset changed FM operator level'):
                    compile_bank(path)
                module.patterns[(0,0,0)][0].effects=[(0,0x13,10)]
                compile_bank(path)

    def test_green_hill_fm_flow_and_cancelled_slide_loop(self):
        source=ROOT/'examples/sonic_greenhillzone_annotated.fur'
        module=parse(source)
        module.patterns={key:value for key,value in module.patterns.items() if key[1]<5}
        with tempfile.TemporaryDirectory() as tmp:
            manifest=Path(tmp)/'bank.json'
            manifest.write_text(json.dumps({'version':1,'songs':[{'id':2,'name':'ghz_fm','source':str(source),'external_commands':'passthrough','voices':[{'channel':i,'slot':'fm'+str(i)} for i in range(5)]}]}))
            with patch_module('calliope.compiler.parse',return_value=module):
                data,report,_=compile_bank(manifest)
        self.assertEqual(len(report['rows']),1824)
        player=Player(data);player.start(2)
        for _ in range(12000):player.step()
        self.assertTrue(player.events)
        self.assertTrue(any(e[1]==0x36 for e in player.trace))

    def test_partial_pitch_table_and_slide(self):
        from calliope.fm import pitch_options
        module=parse(ROOT/'examples/sonic_greenhillzone_annotated.fur')
        options=pitch_options(module)
        self.assertTrue(options['partialPitch'])
        options['fbPortaPause']=False
        pitch=Pitch(False,'ntsc',440,options=options)
        pitch.begin(Cell(note=108,effects=[(0,0xe5,192)]),None)
        base=frequency(108,rounded=False)
        multiplier=round(1024*2**(64/1536))
        self.assertEqual(pitch.tick(),(base&0xf800)|(((base&2047)*multiplier)>>10))
        pitch.begin(Cell(note=110,effects=[(0,0xe5,128),(1,2,4)]),None)
        self.assertEqual(pitch.tick(),frequency(110,rounded=False)-4)
        self.assertEqual(pitch.tick(),frequency(110,rounded=False)-8)
        pitch.begin(Cell(effects=[(0,1,0)]),None)
        self.assertIsNone(pitch.tick())

    def test_partial_pitch_octave_pause(self):
        pitch=Pitch(False,'ntsc',440,options={'partialPitch':True,'fbPortaPause':True})
        pitch.begin(Cell(note=108,effects=[(0,2,4)]),None)
        base=pitch.base
        # C lies at the OPN boundary: first crossing waits a tick.
        self.assertEqual(pitch.tick(),base)
        self.assertIsNotNone(pitch.pause_freq)
        pending=pitch.pause_freq
        pitch.tick()
        self.assertEqual(pitch.base,pending-4)
        self.assertIsNone(pitch.pause_freq)

    def test_volume_macro_and_held_volume_use_one_patch(self):
        from calliope.fm import Level
        from calliope.compiler import fm_patch
        module=parse(ROOT/'examples/fm_ring.fur');ins=module.instruments[0]
        ins['features']['MA']=bytes((8,0,0,3,0,255,0,0,0,1,127,95,63,255))
        level=Level();level.begin(Cell(note=108,volume=100),ins,True)
        for volume in (100,68,36):
            patch=fm_patch(ins,volume)
            self.assertEqual(level.tick(),patch[4:8]+patch[31:32])
        level.begin(Cell(volume=64),ins,False)
        self.assertEqual(level.tick()[:4],fm_patch(ins,64)[4:8])
        level.begin(Cell(note=182),ins,False);self.assertTrue(level.released)
        song=module.subsongs[0];song.rows=4;song.speeds=[3];song.orders=[[0] for _ in module.channels]
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0,volume=127),1:Cell(volume=64),3:Cell(note=180)}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'fm','source':str(ROOT/'examples/fm_ring.fur'),'voices':[{'channel':0,'slot':'fm0'}]}]}))
            with patch_module('calliope.compiler.parse',return_value=module):data,report,_=compile_bank(path)
        self.assertEqual(report['patches'],1)
        player=Player(data);player.start(1)
        for _ in range(12):player.step()
        self.assertEqual(len([e for e in player.trace if e[1]==0x11]),1)
        self.assertGreater(len([e for e in player.trace if e[1]==0x37]),4)

    def test_volume_compatibility_and_scaling(self):
        from calliope.fm import Level,volume_options
        from calliope.compiler import fm_patch
        module=parse(ROOT/'examples/fm_ring.fur');ins=module.instruments[0]
        ins['features']['MA']=bytes((8,0,0,1,255,255,0,0,0,1,63,255))
        for modern,ceil in ((True,False),(False,False),(False,True)):
            level=Level(modern,ceil);level.begin(Cell(note=108,volume=100),ins,True)
            volume=max(0,100+63-127) if modern else (100*63+(126 if ceil else 0))//127
            expected=fm_patch(ins,volume,modern,ceil)
            self.assertEqual(level.tick(),expected[4:8]+expected[31:32])
        module.version=248;module.flags={'compat':[b'newVolumeScaling=0\nceilVolumeScaling=1\0'.hex()]}
        self.assertEqual(volume_options(module),(False,True))

    def test_furnace_rounding_and_block_boundaries(self):
        # Furnace rounds absolute f-number before applying block shifts.
        self.assertEqual([frequency(n) for n in (60,108,109,110,119,120)],
                         [0x284,0x2284,0x22aa,0x22d2,0x24bf,0x2a84])
        self.assertEqual(frequency(108,'pal'),0x2289)
        def actual(value):return (value&2047)*2**(value>>11)
        for n in (71.9,83.9,107.9,119.9):
            self.assertGreater(actual(frequency(n+.2)),actual(frequency(n)))
        self.assertEqual(frequency(-1000),0);self.assertEqual(frequency(1000),0x3fff)
    def test_pitch_macros_and_gate_release(self):
        pitch=Pitch(True,'ntsc',440);ins={'kind':1,'features':{'MA':MACRO}}
        pitch.begin(Cell(note=108,instrument=0),ins)
        self.assertEqual([pitch.tick() for _ in range(4)],
                         [frequency(108),frequency(108.5),frequency(120),frequency(120.5)])
        pitch.begin(Cell(note=181),ins);self.assertTrue(pitch.released)
        pitch.begin(Cell(note=180),ins);self.assertIsNone(pitch.tick())
    def test_fine_pitch_and_unsupported_compatibility(self):
        pitch=Pitch(True,'ntsc',440);pitch.begin(Cell(note=108,effects=[(0,0xe5,192)]),None)
        self.assertEqual(pitch.tick(),frequency(108.5))
        with self.assertRaisesRegex(FormatError,'full linear'):
            Pitch(False,'ntsc',440).begin(Cell(effects=[(0,0xe5,128)]),None)
        with self.assertRaisesRegex(FormatError,'full linear'):
            Pitch(False,'ntsc',440).begin(Cell(note=108),{'kind':1,'features':{'MA':MACRO}})
    def test_vibrato_persistence_stop_and_note_reset(self):
        pitch=Pitch(True,'ntsc',440)
        pitch.begin(Cell(note=108,effects=[(0,4,0x48)]),None)
        # Furnace's signed sine table and integer depth calculation.
        self.assertEqual([pitch.tick() for _ in range(4)],
                         [frequency(108+n/128) for n in (24,44,58,63)])
        pitch.begin(Cell(note=108),None);self.assertEqual(pitch.tick(),frequency(108+24/128))
        pitch.begin(Cell(effects=[(0,4,0)]),None);self.assertEqual(pitch.tick(),frequency(108))
        self.assertIsNone(pitch.tick())
        pitch=Pitch(True,'ntsc',440,True)
        pitch.begin(Cell(note=108,effects=[(0,4,0x48)]),None);pitch.tick()
        pitch.begin(Cell(note=108),None);self.assertEqual(pitch.tick(),frequency(108+44/128))

    def test_slides_persist_cancel_and_skip_first_tick(self):
        pitch=Pitch(True,'ntsc',440)
        pitch.begin(Cell(note=108,effects=[(0,1,16)]),None)
        self.assertEqual([pitch.tick(),pitch.tick()],[frequency(108.5),frequency(109)])
        pitch.begin(Cell(),None);self.assertEqual(pitch.tick(),frequency(109.5))
        pitch.begin(Cell(effects=[(0,2,8)]),None);self.assertEqual(pitch.tick(),frequency(109.25))
        pitch.begin(Cell(effects=[(0,1,0)]),None);self.assertIsNone(pitch.tick())
        pitch=Pitch(True,'ntsc',440,options={'noSlidesOnFirstTick':True})
        pitch.begin(Cell(note=108,effects=[(0,1,32)]),None)
        self.assertEqual([pitch.tick(),pitch.tick()],[frequency(108),frequency(109)])
        pitch.begin(Cell(),None);self.assertIsNone(pitch.tick())

    def test_portamento_does_not_restart_envelope_or_vibrato(self):
        pitch=Pitch(True,'ntsc',440)
        pitch.begin(Cell(note=108,effects=[(0,4,0x48)]),None);pitch.tick()
        pitch.begin(Cell(note=110,effects=[(0,3,32)]),None)
        self.assertFalse(pitch.fresh);self.assertEqual(pitch.vibrato_phase,4)
        self.assertEqual(pitch.tick(),frequency(109+44/128))
        pitch.tick();self.assertEqual(pitch.base,110*128);self.assertEqual(pitch.slide_kind,0)
        pitch.begin(Cell(note=112),None);self.assertTrue(pitch.fresh)
        pitch.begin(Cell(note=114,effects=[(0,3,16)]),None);pitch.tick()
        pitch.begin(Cell(note=180),None);pitch.tick()
        pitch.begin(Cell(note=108),None);self.assertTrue(pitch.fresh);self.assertEqual(pitch.slide_speed,0)

    def test_legato_preserves_macro_and_macro_release_preserves_gate(self):
        pitch=Pitch(True,'ntsc',440);ins={'kind':1,'features':{'MA':MACRO}}
        pitch.begin(Cell(note=108),ins);pitch.tick()
        cursors=pitch.cursors
        pitch.begin(Cell(note=112,effects=[(0,0xea,1)]),ins)
        self.assertFalse(pitch.fresh);self.assertIs(pitch.cursors,cursors)
        self.assertEqual(pitch.tick(),frequency(112.5))
        pitch.begin(Cell(note=182),ins);self.assertTrue(pitch.gate);self.assertTrue(pitch.released)
        pitch.begin(Cell(note=114,effects=[(0,0xea,0)]),ins);self.assertTrue(pitch.fresh)

    def test_expanded_slide_loop_requires_deterministic_restart(self):
        from calliope.fm import validate_loop_entry
        with self.assertRaisesRegex(FormatError,'slide loop'):
            validate_loop_entry(Cell(note=108),{1})
        with self.assertRaisesRegex(FormatError,'EA00'):
            validate_loop_entry(Cell(note=108,effects=[(0,1,16)]),{1,0xea})
        validate_loop_entry(Cell(note=108,effects=[(0,1,16),(1,0xea,0)]),{1,0xea})

    def test_sfx_vibrato_loop_requires_phase_reset(self):
        from calliope.sfx import apply_loop
        module=parse(ROOT/'examples/fm_ring.fur');song=module.subsongs[0]
        song.rows=2;song.speeds=[2];song.orders=[[0] for _ in module.channels]
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0,effects=[(0,4,0x48)])}}
        item={'loop':{'start':[0,0],'end':[0,2]}}
        module.flags['compat_legacy'][15]=1
        with self.assertRaisesRegex(FormatError,'phase reset'):apply_loop(bytes((0,4,255)),item,module,0,[0])
        module.flags['compat_legacy'][15]=0
        self.assertEqual(apply_loop(bytes((0,4,255)),item,module,0,[0]),(bytes((0,4,255)),0))

    def test_compiler_keeps_fresh_notes_separate_from_automation(self):
        module=parse(ROOT/'examples/fm_ring.fur');song=module.subsongs[0]
        song.rows=3;song.speeds=[3];song.orders=[[0] for _ in module.channels]
        module.flags['compat_legacy'][1]=2
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0,volume=127),2:Cell(note=180)}}
        module.instruments[0]['features']['MA']=MACRO
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'fm','source':str(ROOT/'examples/fm_ring.fur'),'voices':[{'channel':0,'slot':'fm0'}]}]}))
            with patch('calliope.compiler.parse',return_value=module):data,_,_=compile_bank(path)
        p=Player(data);p.start(1)
        for _ in range(10):p.step()
        self.assertEqual([e for e in p.trace if e[1]==0x11],[(1,0x11,0,frequency(108))])
        self.assertEqual([e[0] for e in p.trace if e[1]==0x36],[2,3,4,5,6])
        self.assertIsNone(p.voices[0]['note'])

if __name__=='__main__':unittest.main()
