import json
import math
from pathlib import Path
import struct
import subprocess
import os
import sys
import tempfile
import unittest
import wave
import zlib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from calliope.furnace import parse,FormatError,Reader
from calliope.compiler import compile_bank,infer_sfx,Packer,load_manifest,ids_header,fm_patch
from calliope.pcm import decode,convert,RATES
from calliope.reference import Player
from calliope.fm import frequency as fm_frequency
from make_fixtures import make
from clpconvert import compile_cached
class Fixture(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def module(self,version=248,kind=0x83):
        p=self.root/'song.fur'; p.write_bytes(make(version,kind)); return p
    def manifest(self,**extra):
        p=self.module(); song={'name':'test','id':4,'source':p.name,'voices':[{'channel':'melody','slot':'fm0'}],'events':{'1':{'id':123,'parameter':456}}}; song.update(extra)
        m=self.root/'bank.json'; m.write_text(json.dumps({'version':1,'songs':[song]})); return m
    def wav(self,width=2,channels=1):
        p=self.root/'sample.wav'
        with wave.open(str(p),'wb') as w:
            w.setnchannels(channels); w.setsampwidth(width); w.setframerate(22050)
            raw=bytearray()
            for i in range(256):
                x=math.sin(i*.2)*.4
                for _ in range(channels): raw.extend(bytes((round(128+x*127),)) if width==1 else round(x*((1<<(8*width-1))-1)).to_bytes(width,'little',signed=True))
            w.writeframes(raw)
        return p
    def test_song_fm_steal_order(self):
        data,_,_=compile_bank(self.manifest())
        player=Player(data);player.start(4)
        self.assertEqual(player.fm_steal_order,[0,1,2,3,4])
        order=['fm4','fm2','fm0','fm3','fm1']
        data,_,_=compile_bank(self.manifest(fm_steal_order=order))
        player=Player(data);player.start(4)
        self.assertEqual(player.fm_steal_order,[4,2,0,3,1])
        for bad in [[],['fm0']*5,['fm0','fm1','fm2','fm3','tone0'],[0,1,2,3,4],None]:
            with self.assertRaisesRegex(FormatError,'fm_steal_order'):
                compile_bank(self.manifest(fm_steal_order=bad))
        manifest=self.manifest(fm_steal_order=order)
        m=json.loads(manifest.read_text())
        second=dict(m['songs'][0],id=5,name='other');second.pop('fm_steal_order')
        m['songs'].append(second);manifest.write_text(json.dumps(m))
        data,_,_=compile_bank(manifest);player=Player(data)
        player.start(4);self.assertEqual(player.fm_steal_order,[4,2,0,3,1])
        player.start(5);self.assertEqual(player.fm_steal_order,[0,1,2,3,4])
        m['sfx']=[dict(second,id=6,name='effect',fm_steal_order=order)]
        manifest.write_text(json.dumps(m))
        with self.assertRaisesRegex(FormatError,'fm_steal_order'):compile_bank(manifest)

    def test_song_psg_steal_order(self):
        data,_,_=compile_bank(self.manifest())
        player=Player(data);player.start(4)
        self.assertEqual(player.psg_steal_order,[0,1,2])
        order=['tone2','tone0','tone1']
        data,_,_=compile_bank(self.manifest(psg_steal_order=order,fm_steal_order=['fm4','fm3','fm2','fm1','fm0']))
        player=Player(data);player.start(4)
        self.assertEqual(player.psg_steal_order,[2,0,1])
        self.assertEqual(player.fm_steal_order,[4,3,2,1,0])
        for bad in [[],['tone0']*3,['tone0','tone1','noise'],['tone0','tone1','fm2'],[0,1,2],None]:
            with self.assertRaisesRegex(FormatError,'psg_steal_order'):
                compile_bank(self.manifest(psg_steal_order=bad))
        manifest=self.manifest(psg_steal_order=order)
        m=json.loads(manifest.read_text());second=dict(m['songs'][0],id=5,name='other');second.pop('psg_steal_order')
        m['songs'].append(second);manifest.write_text(json.dumps(m))
        data,_,_=compile_bank(manifest);player=Player(data)
        player.start(4);self.assertEqual(player.psg_steal_order,[2,0,1])
        player.start(5);self.assertEqual(player.psg_steal_order,[0,1,2])
        m['sfx']=[dict(second,id=6,name='effect',psg_steal_order=order)]
        manifest.write_text(json.dumps(m))
        with self.assertRaisesRegex(FormatError,'psg_steal_order'):compile_bank(manifest)

    def test_psg_music_envelope(self):
        source=ROOT/'examples/psg_ring.fur';path=self.root/'envelope.json'
        path.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'env','source':str(source),'voices':[{'channel':6,'slot':'tone0'}]}]}))
        data,_,_=compile_bank(path);player=Player(data);player.start(1)
        for _ in range(40):player.step()
        volumes=[(e[0],v&15) for e in player.trace if e[1]==0x30 for v in e[2] if v&0x90==0x90]
        self.assertEqual(volumes,[(1,0),(5,0),(9,0),(26,2),(27,4),(28,5),(29,7),(30,9),(31,10),(32,12),(33,14),(34,15)])
    def test_invalid_external_command_mode(self):
        with self.assertRaisesRegex(FormatError,'external_commands'):compile_bank(self.manifest(external_commands='groups'))
    def test_external_command_passthrough(self):
        path=self.manifest(external_commands='passthrough',events={})
        data,_,_=compile_bank(path);player=Player(data);player.start(4);player.step()
        self.assertEqual(player.events,[(1,1,1,0)])
    def test_psg_sfx_release_envelope(self):
        from calliope.psg import compile_tone
        from calliope.macros import sequences
        m=parse(ROOT/'examples/psg_ring.fur')
        kind,channels=infer_sfx(m); self.assertEqual(kind,'psg')
        stream=compile_tone(m,0,channels[0],'ntsc','ring')
        # The authored release at row 6 follows three notes, four ticks apart.
        tick=1; notes=[]; volumes=[]; p=0
        while stream[p]!=255:
            op=stream[p];p+=1
            if op==0: tick+=stream[p];p+=1;continue
            self.assertEqual(op,0x30); n=stream[p];p+=1; values=stream[p:p+n];p+=n
            if values[0]&0x10: volumes.append((tick,values[0]&15))
            else: notes.append(tick)
        self.assertEqual(notes,[1,5,9])
        self.assertEqual(volumes,[(1,0),(5,0),(9,0),(26,2),(27,4),(28,5),(29,7),(30,9),(31,10),(32,12),(33,14),(34,15)])
        # The release reaches silence before the terminal row finishes.
        ins={'kind':0,'features':{'MA':bytes((8,0,1,1,255,255,0,1,0,1,15,255))}}
        with self.assertRaisesRegex(FormatError,'unsupported'): sequences(ins,(0,))
    def test_noise_and_coupled_assets(self):
        from calliope.psg import compile_noise
        for name,kind,channels in [('noise232','noise',[3]),('coupled232','coupled',[2,3])]:
            module=parse(ROOT/'examples'/f'{name}.fur')
            self.assertEqual(infer_sfx(module),(kind,channels))
            stream=compile_noise(module,0,channels,'ntsc',name)
            self.assertEqual(stream[-1],255)
            self.assertIn(bytes((0x30,1,0xe6 if kind=='noise' else 0xe7)),stream)
        data,report,_=compile_bank(ROOT/'examples/bank.json')
        self.assertEqual({e['id']:e['kind'] for e in report['effects']}[7],'coupled')
        module=parse(ROOT/'examples/coupled232.fur')
        module.patterns.pop((0,2,0))
        with self.assertRaisesRegex(FormatError,'tone-derived'):infer_sfx(module)
    def test_timing_rates_and_grooves(self):
        from calliope.timing import Timing,rate5
        for version in (232,248):
            p=self.manifest();(self.root/'song.fur').write_bytes(make(version,hz=120,speeds=(3,5)))
            data,_,_=compile_bank(p);player=Player(data);player.start(4)
            for _ in range(31):player.clock()
            self.assertEqual(player.tick,62)
            self.assertEqual([e[0] for e in player.trace if e[1]==0x11],[1,20,41,60])
        with self.assertRaisesRegex(FormatError,'0.2'):rate5(60.1)
        p=self.manifest(loop_order=0);(self.root/'song.fur').write_bytes(make(232,speeds=(3,5)))
        with self.assertRaisesRegex(FormatError,'groove phase'):compile_bank(p)
        module=parse(self.root/'song.fur');module.flags['grooves']=[[2,4]]
        module.patterns[(0,0,0)][0].effects=[(0,0x09,0),(1,0xf0,125)]
        timing=Timing(module,0);duration,ops=timing.row(0,0,[0])
        self.assertEqual((duration,ops),(2,bytes((0x34,250,0))))
        self.assertEqual(timing.row(0,1,[0])[0],4)
    def test_conductor_speed_effects(self):
        p=self.manifest();(self.root/'song.fur').write_bytes(make(232,initial_effects={0:(0x0f,3)}))
        data,_,_=compile_bank(p);player=Player(data);player.start(4)
        for _ in range(50):player.step()
        self.assertEqual([e[0] for e in player.trace if e[1]==0x11],[1,16,31,46])
        (self.root/'song.fur').write_bytes(make(232,initial_effects={0:(0xc0,30)}))
        data,_,_=compile_bank(p);player=Player(data);player.start(4);player.clock()
        for _ in range(60):player.clock()
        self.assertEqual(player.tick,31)
    def test_sfx_initial_groove_and_loop_bounds(self):
        for kind in (0x83,3):
            source=self.root/'groove.fur';source.write_bytes(make(248,kind,notes=(108,112,115),event=False,speeds=(3,5)))
            item={'id':1,'name':'groove','source':source.name,'loop':{'start':[0,0],'end':[0,3]}}
            path=self.root/'groove.json';path.write_text(json.dumps({'version':1,'sfx':[item]}))
            data,report,_=compile_bank(path);pos=report['effects'][0]['offset'];ticks=0
            while data[pos]!=255:
                op=data[pos];pos+=1
                if op==0:ticks+=data[pos];pos+=1
                elif op==0x10:pos+=2
                elif op in (0x11,0x36):pos+=3
                elif op==0x37:pos+=6
                elif op==0x13:pos+=1
                elif op==0x30:pos+=data[pos]+1
                else:self.fail(f'unexpected opcode {op}')
            self.assertEqual(ticks,11)
    def test_explicit_sfx_loops(self):
        for name in ('fm_ring','psg_ring','noise232','coupled232'):
            source=ROOT/'examples'/f'{name}.fur'
            item={'id':1,'name':'looped','source':str(source),'loop':{'start':[0,0],'end':[0,3]}}
            path=self.root/'loop.json';path.write_text(json.dumps({'version':1,'sfx':[item]}))
            data,report,_=compile_bank(path);effect=report['effects'][0]
            self.assertEqual(effect['loop'],effect['offset'])
            desc=int.from_bytes(data[13:16],'little')
            self.assertEqual(int.from_bytes(data[desc+8:desc+11],'little'),effect['loop'])
            module=parse(source);channels=infer_sfx(module)[1]
            bad=next(row for row in range(module.subsongs[0].rows) if any(module.cell(0,0,row,ch).note is None or module.cell(0,0,row,ch).note>=180 or module.cell(0,0,row,ch).instrument is None for ch in channels))
            item['loop']={'start':[0,bad],'end':[1,0]};path.write_text(json.dumps({'version':1,'sfx':[item]}))
            with self.assertRaisesRegex(FormatError,'fresh note'):compile_bank(path)
            item['loop']={'start':[0,0],'end':[0,0]};path.write_text(json.dumps({'version':1,'sfx':[item]}))
            with self.assertRaisesRegex(FormatError,'positive'):compile_bank(path)
    def test_noise_music(self):
        data,_,_=compile_bank(ROOT/'examples/bank.json');p=Player(data);p.start(4)
        for _ in range(65):p.step()
        notes=[e for e in p.trace if e[1]==0x33]
        self.assertEqual([e[2]&0xf7 for e in notes],[0xe6,0xe5,0xe4])
        self.assertEqual([e[0] for e in notes],[1,31,61])
        old=bytearray(data);old[4]=2
        with self.assertRaisesRegex(FormatError,'unsupported bank'):Player(old)
    def test_noise_modes_and_lfsr_reset(self):
        from calliope.psg import compile_noise
        module=parse(ROOT/'examples/noise232.fur')
        module.patterns[(0,3,0)][5].note=108
        module.patterns[(0,3,0)][10].note=108
        stream=compile_noise(module,0,[3],'ntsc','fixed')
        self.assertEqual(stream.count(bytes((0x30,1,0xe6))),1)
        module.patterns[(0,3,0)][5].effects=[(0,0x20,1)]
        stream=compile_noise(module,0,[3],'ntsc','fixed')
        self.assertEqual(stream.count(bytes((0x30,1,0xe6))),2)
        module.patterns[(0,3,0)][5].note=None
        with self.assertRaisesRegex(FormatError,'require a note'):compile_noise(module,0,[3],'ntsc','fixed')
    def test_new_sfx_assets(self):
        data,report,_=compile_bank(ROOT/'examples/bank.json')
        byid={e['id']:e for e in report['effects']}
        self.assertEqual(byid[4]['kind'],'psg');self.assertEqual(byid[4]['mask'],224)
        self.assertEqual(byid[5]['kind'],'pcm');self.assertGreater(byid[5]['length'],1000)
        m=json.loads((ROOT/'examples/bank.json').read_text())
        for group in ('songs','sfx'):
            for item in m[group]: item['source']=str(ROOT/'examples'/item['source'])
        next(e for e in m['sfx'] if e['id']==4)['resource_mask']=1
        path=self.root/'invalid-mask.json';path.write_text(json.dumps(m))
        with self.assertRaisesRegex(FormatError,'tone bits'): compile_bank(path)
    def test_pcm_dac_bytes_and_revision(self):
        from calliope.compiler import packed_pcm
        self.assertEqual(packed_pcm(bytes((128,0,127))),bytes((0,128,255)))
        data,report,_=compile_bank(ROOT/'examples/bank.json')
        effect=next(e for e in report['effects'] if e['id']==5)
        signed,_=convert(ROOT/'examples/chu.wav','ntsc',1,None)
        self.assertEqual(data[effect['offset']:effect['offset']+effect['length']],packed_pcm(signed))
        self.assertEqual(data[4],12)
        old=bytearray(data);old[4]=11
        with self.assertRaisesRegex(FormatError,'unsupported bank'): Player(old)
    def test_logical_arrangement_boundaries(self):
        data,_,_=compile_bank(ROOT/'examples/bank.json');player=Player(data);player.start(3)
        player.step();self.assertEqual([v['enabled'] for v in player.parts],[True,False,True])
        self.assertEqual(player.parts[1]['note'],fm_frequency(108))  # Silent alternative still advances.
        with self.assertRaisesRegex(FormatError,'conflicting'):player.set_parts(7)
        self.assertIsNone(player.pending_parts)
        player.select_arrangement(1,1)
        self.assertEqual([v['enabled'] for v in player.parts],[False,True,True])
        self.assertIsNone(player.voices[0]['note']);self.assertEqual(player.voices[1]['note'],fm_frequency(84))
        for _ in range(30):player.step()
        self.assertEqual(player.voices[0]['note'],fm_frequency(111))  # sad E-flat at row 5, not happy E.
        player.mute_groups(1);player.step();self.assertFalse(player.parts[1]['enabled']);self.assertTrue(player.parts[2]['enabled'])
        player.groups=0;player.select_arrangement(0,16,2)
        while player.tick<61:player.step()
        self.assertEqual([v['enabled'] for v in player.parts],[True,False,True])
        self.assertEqual(len(player.events),1)  # Cue is independent of part muting.
    def test_arrangement_validation(self):
        from calliope.parts import layout
        item={'voices':[{'name':'one'},{'name':'two'}],'arrangements':{'bad':['one','two']}}
        with self.assertRaisesRegex(FormatError,'conflicting'):layout(item,[0,0])
        item['arrangements']={'one':['one'],'two':['two']};item['groups']={'both':['one','two']}
        data,masks,index=layout(item,[0,0]);self.assertEqual(masks,[1,2]);self.assertEqual(data[1:3],bytes((1,0)))
        item['groups']={'bad':['missing']}
        with self.assertRaisesRegex(FormatError,'unknown'):layout(item,[0,0])
    def test_part_ids_preserve_voice_indices(self):
        header=ids_header({'songs':[{'name':'test','id':1,'voices':[{}, {'name':'last'}]}]})
        self.assertIn('#define CLP_PART_TEST_LAST 2',header)
        with self.assertRaisesRegex(FormatError,'nonempty'):
            ids_header({'songs':[{'name':'test','id':1,'voices':[{'name':''}]}]})
    def test_sixteen_parts_and_overlapping_groups(self):
        from calliope.parts import layout
        names=[f'voice{i}' for i in range(16)]
        item={'voices':[{'name':n} for n in names],
              'groups':{'all':names,'last':[names[-1]]},
              'arrangements':{'first':[names[0]],'last':[names[-1]]},'initial_arrangement':'last'}
        data,masks,index=layout(item,[0]*16)
        self.assertEqual((masks,index),([1,32768],1))
        self.assertEqual(data[60:64],bytes((0,3,0,1)))
        self.assertEqual(sum(data[i*4+3]<<i for i in range(16)),32768)
        item['arrangements']={}
        with self.assertRaisesRegex(FormatError,'1..16'):layout(item,[0]*16)
    def test_latest_boundary_request_wins(self):
        data,_,_=compile_bank(ROOT/'examples/bank.json');p=Player(data);p.start(3);p.step()
        p.select_arrangement(1,16,2);p.set_parts(4,1)
        for _ in range(6):p.step()
        self.assertEqual([v['enabled'] for v in p.parts],[False,False,True])
        while p.tick<62:p.step()
        self.assertFalse(p.parts[1]['enabled'])
    def test_arrangement_fixture_loader(self):
        exe=Path(os.environ.get('CALLIOPE_FURNACE','__furnace_not_configured__'))
        if not exe.exists():self.skipTest('Furnace executable unavailable')
        output=self.root/'parts.txt'
        subprocess.run([str(exe),'-noreport','-txtout',str(output),str(ROOT/'examples/arrangements232.fur')],capture_output=True,timeout=30)
        self.assertTrue(output.exists(),'Furnace rejected arrangement fixture')
        self.assertEqual(len(parse(ROOT/'examples/arrangements232.fur').patterns),3)
    def test_noise_fixture_loader(self):
        exe=Path(os.environ.get('CALLIOPE_FURNACE','__furnace_not_configured__'))
        if not exe.exists():self.skipTest('Furnace executable unavailable')
        for name in ('noise232','coupled232'):
            output=self.root/(name+'.txt')
            subprocess.run([str(exe),'-noreport','-txtout',str(output),str(ROOT/'examples'/(name+'.fur'))],capture_output=True,timeout=30)
            self.assertTrue(output.exists(),f'Furnace rejected {name}')
            self.assertIn('Calliope fixture',output.read_text())
    def test_named_musical_boundaries(self):
        data,_,_=compile_bank(ROOT/'examples/bank.json')
        for boundary,marker,tick in ((8,0,97),(16,2,61)):
            p=Player(data);p.start(3);p.step();p.select_arrangement(1,boundary,marker)
            while p.tick<tick-1:p.step()
            self.assertTrue(p.parts[0]['enabled']);p.step();self.assertTrue(p.parts[1]['enabled'])
        p=Player(data);p.start(3);p.set_parts(0)
        for _ in range(140):p.step()
        self.assertEqual(len(p.events),2);self.assertTrue(all(not v['enabled'] for v in p.parts))
    def test_versions(self):
        for v in (232,248):
            with self.subTest(v=v):
                m=parse(self.module(v)); self.assertEqual(m.version,v); self.assertEqual(m.subsongs[0].names[0],'melody'); self.assertEqual(m.cell(0,0,5,0).note,112)
    def test_furnace_232_loader(self):
        exe=Path(os.environ.get('CALLIOPE_FURNACE','__furnace_not_configured__'))
        if not exe.exists(): self.skipTest('Set FURNACE_EXE for independent loader validation')
        export=self.root/'furnace.txt'
        subprocess.run([str(exe),'-noreport','-txtout',str(export),str(self.module(232))],capture_output=True,text=True,timeout=30)
        # Windows GUI builds may bypass redirected stdout and return zero on error.
        self.assertTrue(export.exists(),'Furnace did not export the fixture')
        self.assertIn('Calliope fixture',export.read_text())
    def test_uncompressed(self):
        p=self.module(); p.write_bytes(zlib.decompress(p.read_bytes())); self.assertEqual(parse(p).version,248)
    def test_bad_version(self):
        p=self.module(); b=bytearray(zlib.decompress(p.read_bytes())); b[16:18]=struct.pack('<H',999); p.write_bytes(b)
        with self.assertRaisesRegex(FormatError,'format 999'): parse(p)
    def test_truncated(self):
        p=self.module(); p.write_bytes(p.read_bytes()[:-5])
        with self.assertRaises(FormatError): parse(p)
    def test_bad_offset(self):
        p=self.module(); b=bytearray(zlib.decompress(p.read_bytes())); b[20:24]=bytes.fromhex('ffffff7f'); p.write_bytes(b)
        with self.assertRaises(FormatError): parse(p)
    def test_reader_bounds(self):
        with self.assertRaises(FormatError): Reader(b'a').u32()
        with self.assertRaises(FormatError): Reader(b'a').string()
    def test_infer_fm(self): self.assertEqual(infer_sfx(parse(self.module())),('fm',[0]))
    def test_infer_psg(self): self.assertEqual(infer_sfx(parse(self.module(kind=3))),('psg',[0]))
    def test_compile_reproducible(self):
        p=self.manifest(); a=compile_bank(p); b=compile_bank(p); self.assertEqual(a,b); self.assertEqual(a[0][:6],b'BGM1\14\0')
    def test_effect_index_sparse_and_full(self):
        source=self.wav()
        ids=[65534]+[i*257 for i in range(253,-1,-1)]
        manifest=self.root/'bank.json'
        manifest.write_text(json.dumps({'version':1,'sfx':[
            {'name':f'effect_{i}','id':id,'source':source.name} for i,id in enumerate(ids)]}))
        data,_,_=compile_bank(manifest)
        read24=lambda offset:int.from_bytes(data[offset:offset+3],'little')
        directory=read24(22);descriptors=read24(13)
        self.assertEqual(data[7],255)
        for ordinal,id in enumerate(ids,1):
            page=read24(directory+(id>>8)*3)
            self.assertEqual(data[page+(id&255)],ordinal)
            descriptor=descriptors+(ordinal-1)*16
            self.assertEqual(int.from_bytes(data[descriptor:descriptor+2],'little'),id)
            self.assertEqual(page//32768,(page+255)//32768)
        self.assertEqual(read24(directory+254*3),0)
        page=read24(directory)
        self.assertEqual(data[page+1],0)
        page=read24(directory+255*3)
        self.assertEqual(data[page+255],0)
        self.assertEqual(directory//32768,(directory+767)//32768)
        empty=self.root/'empty.json';empty.write_text('{"version":1}')
        empty_data,_,_=compile_bank(empty)
        self.assertEqual(empty_data[22:25],bytes(3))
    def test_song_order_cache_alignment(self):
        data,_,_=compile_bank(ROOT/'examples/bank.json')
        songs=int.from_bytes(data[10:13],'little')
        for i in range(data[6]):
            song=songs+i*24
            table=int.from_bytes(data[song+4:song+7],'little')
            for order in range(data[song+2]+1):
                record=int.from_bytes(data[table+order*3:table+order*3+3],'little')
                for channel in range((data[song+11]&31)+1):
                    stream=int.from_bytes(data[record+channel*3:record+channel*3+3],'little')
                    self.assertEqual(stream%16,0,(i,order,channel,stream))
    def test_reference_sequence_and_cue(self):
        data,_,_=compile_bank(self.manifest()); player=Player(data); player.start(4,9)
        for _ in range(150): player.step()
        self.assertFalse(player.playing); self.assertEqual(player.events,[(1,9,123,456)])
        self.assertEqual([x for x in player.trace if x[1]==0x11],[(t,17,0,fm_frequency(n+60)) for t,n in ((1,48),(31,52),(61,55),(91,60))])
    def test_loop_and_overflow(self):
        data,_,_=compile_bank(self.manifest(loop_order=0)); player=Player(data); player.start(4)
        for _ in range(2500): player.step()
        self.assertTrue(player.playing); self.assertEqual(len(player.events),16); self.assertGreater(player.lost,0)
    def test_unknown_song(self):
        data,_,_=compile_bank(self.manifest()); player=Player(data)
        with self.assertRaises(FormatError): player.start(9)
    def test_invalid_groups(self):
        with self.assertRaisesRegex(FormatError,'mapping'): compile_bank(self.manifest(groups=['drums']))
    def test_unmapped_channel(self):
        with self.assertRaisesRegex(FormatError,'unmapped'): compile_bank(self.manifest(voices=[{'channel':1,'slot':'fm0'}]))
    def test_duplicate_slot(self):
        with self.assertRaisesRegex(FormatError,'unique nonempty names'): compile_bank(self.manifest(voices=[{'channel':0,'slot':'fm0'},{'channel':1,'slot':'fm0'}]))
    def test_duplicate_ids(self):
        p=self.manifest(); m=json.loads(p.read_text()); m['songs'].append(dict(m['songs'][0],name='other')); p.write_text(json.dumps(m))
        with self.assertRaisesRegex(FormatError,'duplicate'): load_manifest(p)
    def test_id_header(self): self.assertIn('#define CLP_SONGS_TEST 4',ids_header(load_manifest(self.manifest())))
    def test_packer_bank_boundary(self):
        p=Packer(); p.add(bytes(32700)); off=p.add(b'xyz'*40); self.assertEqual(off%32768,0)
    def test_packer_dedup(self):
        p=Packer(); self.assertEqual(p.add(b'abc'),p.add(b'abc'))
    def test_wav_widths(self):
        for width in (1,2,3,4):
            with self.subTest(width=width):
                pcm,rate,info=decode(self.wav(width)); self.assertEqual(len(pcm),256); self.assertLess(max(pcm),.41)
    def test_stereo(self):
        data,info=convert(self.wav(channels=2)); self.assertTrue(info['downmixed']); self.assertGreater(len(data),0)
    def test_pcm_regions(self):
        p=self.wav(); a,ai=convert(p,'ntsc'); b,bi=convert(p,'pal'); self.assertNotEqual(ai['rate'],bi['rate'])
    def test_pcm_range(self):
        data,info=convert(self.wav()); self.assertTrue(all(-128<=x<=127 for x in struct.unpack(f'{len(data)}b',data)))
    def test_pcm_full_scale(self):
        from calliope.pcm import resample
        for level,expected in ((-1,-128), (0,0), (.5,64), (1,127)):
            data,_=resample([level]*32,RATES['ntsc'],{})
            self.assertEqual(set(struct.unpack(f'{len(data)}b',data)),{expected})
    def test_pcm_loop_validation(self):
        p=self.wav()
        with self.assertRaisesRegex(FormatError,'loop'): convert(p,loop=[256,1])
        _,info=convert(p,loop=[0,256]); self.assertEqual(info['loop'][1],info['bytes'])
    def test_pcm_gain(self):
        data,info=convert(self.wav(),gain=10)
        self.assertGreater(info['clipped_samples'],0)
        self.assertEqual(max(struct.unpack(f'{len(data)}b',data)),127)
        self.assertEqual(min(struct.unpack(f'{len(data)}b',data)),-128)
    def test_conversion_cache_content_and_region(self):
        m=self.manifest(); cache=self.root/'cache'
        first=compile_cached(m,'ntsc',cache)
        self.assertFalse(first[3]); self.assertEqual(first[:3],compile_cached(m,'ntsc',cache)[:3])
        self.assertTrue(compile_cached(m,'ntsc',cache)[3])
        self.assertFalse(compile_cached(m,'pal',cache)[3])
        text=m.read_text(); m.write_text(text+' ')
        self.assertFalse(compile_cached(m,'ntsc',cache)[3])
        # Different compressed bytes with identical semantics must invalidate too.
        fur=self.root/'song.fur'; fur.write_bytes(zlib.decompress(fur.read_bytes()))
        self.assertFalse(compile_cached(m,'ntsc',cache)[3])
    def test_pcm_timer_cache_and_bank_identity(self):
        m=self.manifest();cache=self.root/'clock cache';banks=[]
        for timer in (1016,1020):
            output=self.root/f'clock-{timer}.bin'
            env=dict(os.environ,CALLIOPE_PCM_TIMER=str(timer))
            result=subprocess.run([sys.executable,str(ROOT/'tools/clpconvert.py'),'compile',str(m),'--cache-dir',str(cache),'--output',str(output)],env=env,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            data=output.read_bytes();self.assertEqual(int.from_bytes(data[19:21],'little'),timer);self.assertEqual(data[21],0);banks.append(data)
        self.assertNotEqual(banks[0],banks[1]);self.assertEqual(len(list(cache.glob('*.json'))),2)
    def test_atomic_publish_reader_contention(self):
        from unittest.mock import patch
        from clpconvert import atomic
        path=self.root/'shared.json';path.write_bytes(b'old')
        original=Path.read_bytes;replace=os.replace;reads=0;writes=0
        def read(p):
            nonlocal reads
            if p==path:
                reads+=1
                if reads<=2:raise PermissionError('concurrent Windows reader')
            return original(p)
        def publish(source,target):
            nonlocal writes
            writes+=1
            if writes==1:raise PermissionError('reader denies replacement')
            return replace(source,target)
        with patch.object(Path,'read_bytes',read),patch('clpconvert.os.replace',publish):atomic(path,b'new')
        self.assertEqual(path.read_bytes(),b'new');self.assertEqual(writes,2)
    def test_conversion_cache_corruption(self):
        m=self.manifest(); cache=self.root/'cache'
        original=compile_cached(m,'ntsc',cache)
        entry=next(cache.glob('*.json')); content=json.loads(entry.read_text())
        content['payload']['bank']='AAAA'; entry.write_text(json.dumps(content))
        recovered=compile_cached(m,'ntsc',cache)
        self.assertFalse(recovered[3]); self.assertEqual(original[:3],recovered[:3])
    def test_parallel_cli_and_paths_with_spaces(self):
        m=self.manifest(); cache=self.root/'shared cache'; output=self.root/'output with spaces'
        output.mkdir()
        commands=[[sys.executable,str(ROOT/'tools/clpconvert.py'),'compile',str(m),
                   '--cache-dir',str(cache),'--output',str(output/f'{i}.bin')] for i in range(4)]
        processes=[subprocess.Popen(c,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for c in commands]
        results=[(p,p.communicate(timeout=30)) for p in processes]
        for p,(stdout,stderr) in results:
            self.assertEqual(p.returncode,0,stderr.decode())
        self.assertEqual(len(list(cache.glob('*.json'))),1)
        self.assertEqual(len({p.read_bytes() for p in output.glob('*.bin')}),1)
    def test_failed_conversion_preserves_outputs(self):
        m=self.manifest(); output=self.root/'converted.bin'; output.write_bytes(b'previous successful result')
        (self.root/'song.fur').write_bytes(b'invalid')
        p=subprocess.run([sys.executable,str(ROOT/'tools/clpconvert.py'),'compile',str(m),
                          '--output',str(output),'--cache-dir',str(self.root/'cache')],capture_output=True,timeout=30)
        self.assertNotEqual(p.returncode,0); self.assertEqual(output.read_bytes(),b'previous successful result')
        self.assertFalse(list((self.root/'cache').glob('*.json')))
    def test_prerequisite_id_mismatch(self):
        m=self.manifest(); ids=self.root/'ids.h'; ids.write_text('stale header')
        p=subprocess.run([sys.executable,str(ROOT/'tools/clpconvert.py'),'compile',str(m),
                          '--output',str(self.root/'converted.bin'),'--ids-header',str(ids)],capture_output=True,timeout=30)
        self.assertNotEqual(p.returncode,0); self.assertIn(b'prerequisite header',p.stderr)
        self.assertFalse((self.root/'converted.bin').exists())
    def test_supplied_fm_effect(self):
        source=ROOT/'examples/fm_ring.fur'; m=parse(source)
        self.assertEqual(infer_sfx(m),('fm',[0]))
        manifest=self.root/'bank.json'; manifest.write_text(json.dumps({'version':1,'sfx':[{'id':1,'name':'ring','source':str(source)}]}))
        data,report,_=compile_bank(manifest)
        offset=int.from_bytes(data[13:16],'little')
        self.assertEqual(data[offset+2],2); self.assertEqual(data[offset+7],31)
        self.assertEqual(report['songs'],0); self.assertEqual(report['patches'],1)
    def test_fm_operator_enable_masks(self):
        ins=parse(ROOT/'examples/fm_ring.fur').instruments[0]
        original=ins['features']['FM']; full=fm_patch(ins)
        # Independent expected YM order, matching Furnace genesis.cpp commitState.
        keymasks=(0,1,4,5,2,3,6,7,8,9,12,13,10,11,14,15)
        for mask in range(16):
            with self.subTest(mask=mask):
                ins['features']['FM']=bytes((mask<<4|4,))+original[1:]
                compiled=fm_patch(ins)
                self.assertEqual(compiled[30],keymasks[mask])
                self.assertEqual(compiled[:4],full[:4])
                self.assertEqual(compiled[8:30],full[8:30])
                self.assertEqual(compiled[4:8],bytes(full[4+i] if mask&(1<<i) else 127 for i in range(4)))
                self.assertEqual(compiled[31],full[31]&mask)
                quiet=fm_patch(ins,64)
                for i in range(4):
                    if not mask&(1<<i):self.assertEqual(quiet[4+i],127)
        for flags in (0xf2,0xf0,0xf5):
            ins['features']['FM']=bytes((flags,))+original[1:]
            with self.assertRaisesRegex(FormatError,'unsupported FM instrument mode'):fm_patch(ins)
        ins['features']['FM']=original[:4]+b'\x01'+original[5:]
        with self.assertRaisesRegex(FormatError,'unsupported FM instrument mode'):fm_patch(ins)

    def test_fm_volume_carriers_and_ams(self):
        m=parse(ROOT/'examples/elevator.fur'); full=fm_patch(m.instruments[4]); quiet=fm_patch(m.instruments[4],116)
        self.assertEqual(full[29]&0x30,0x10,'AMS must move from Furnace bit 3 to YM bit 4')
        self.assertEqual(quiet[4:6],full[4:6],'algorithm 4 modulators retain their TL')
        self.assertEqual(quiet[6:8],bytes(min(127,v+11) for v in full[6:8]))
    def test_embedded_samples_and_mixed_track(self):
        source=ROOT/'examples/elevator.fur'; m=parse(source)
        self.assertEqual([x['frames'] for x in m.samples],[17595,14517,2424])
        self.assertTrue(all(len(x['data'])==x['frames']*2 for x in m.samples))
        manifest=self.root/'bank.json'
        voices=[{'channel':ch,'slot':slot} for ch,slot in [(0,'fm0'),(1,'fm1'),(5,'pcm'),(6,'tone0'),(7,'tone1')]]
        manifest.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'mixed','source':str(source),'voices':voices,'loop_order':0}]}))
        data,report,_=compile_bank(manifest); player=Player(data);player.start(1)
        for _ in range(2049):player.step()
        self.assertTrue(player.playing);self.assertEqual(player.order,0)
        self.assertEqual(sum(e[1]==0x31 for e in player.trace),125)
        self.assertEqual(sum(e[1]==0x11 for e in player.trace),202)
        self.assertTrue(any(e[1]==0x30 and len(e[2]) in (2,3) for e in player.trace))
        self.assertGreaterEqual(report['sample_variants'],3)
    def test_psg_pitch_matches_furnace(self):
        source=self.root/'tone.fur';source.write_bytes(make(232,3,notes=(108,),event=False))
        manifest=self.root/'bank.json'
        manifest.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'tone','source':str(source),'voices':[{'channel':0,'slot':'tone0'}]}]}))
        data,_,_=compile_bank(manifest);player=Player(data);player.start(1);player.step()
        command=next(e[2] for e in player.trace if e[1]==0x30)
        period=(command[0]&15)|(command[1]<<4)
        self.assertEqual(period,107)
        exe=Path(os.environ.get('CALLIOPE_FURNACE','__furnace_not_configured__'))
        if not exe.exists():return
        output=self.root/'tone.wav'
        subprocess.run([str(exe),'-noreport','-loops','1','-output',str(output),str(source)],capture_output=True,timeout=30)
        self.assertTrue(output.exists(),'Furnace did not produce the pitch oracle')
        with wave.open(str(output),'rb') as w:
            rate=w.getframerate();channels=w.getnchannels();width=w.getsampwidth()
            self.assertEqual(width,2)
            frames=struct.unpack('<'+'h'*(w.getnframes()*channels),w.readframes(w.getnframes()))
        signal=[sum(frames[i:i+channels]) for i in range(int(.05*rate)*channels,int(.25*rate)*channels,channels)]
        crossings=sum(a<=0<b for a,b in zip(signal,signal[1:]))
        observed=crossings/(len(signal)/rate)
        expected=(53693175/15)/(32*period)
        self.assertAlmostEqual(observed,expected,delta=10)
if __name__=='__main__': unittest.main()
