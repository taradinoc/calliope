"""Editor service and document regressions without a GUI dependency."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from calliope.compiler import compile_bank
from calliope.editor.document import Document
from calliope.editor.services import cached_compile,compile_document,export_bank,inspect_source,validate_document
from calliope.editor.preview import make_rom

class EditorServices(unittest.TestCase):
    def test_preview_cache_and_progress(self):
        doc={'version':1,'songs':[{'id':1,'name':'melody','source':'melody232.fur','voices':[{'channel':'melody','slot':'fm0'}],'external_commands':'passthrough'}]}
        with tempfile.TemporaryDirectory() as cache:
            progress=[]
            first=cached_compile(doc,ROOT/'examples','ntsc','z80',cache,lambda *args:progress.append(args))
            self.assertFalse(first[3]);self.assertEqual(progress[0],('songs',0,1,'melody'))
            progress.clear();second=cached_compile(doc,ROOT/'examples','ntsc','z80',cache,lambda *args:progress.append(args))
            self.assertTrue(second[3]);self.assertEqual(first[:3],second[:3]);self.assertEqual(progress,[])
            self.assertFalse(cached_compile(doc,ROOT/'examples','pal','z80',cache)[3])
            doc['songs'][0]['id']=2
            self.assertFalse(cached_compile(doc,ROOT/'examples','ntsc','z80',cache)[3])
    def test_identical_output_and_no_resource_writes(self):
        path=ROOT/'examples/bank.json';m=json.loads(path.read_text())
        sources=[path]+[path.parent/i['source'] for cat in ('songs','sfx') for i in m[cat]]
        before={p:hashlib.sha256(p.read_bytes()).digest() for p in sources}
        original=copy.deepcopy(m)
        a,_,ah=compile_bank(path);b,report,bh=compile_document(m,path.parent)
        self.assertEqual(a,b);self.assertEqual(ah,bh);self.assertEqual(m,original)
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).digest() for p in sources})
        self.assertEqual(report['pcm_timer'],1016)

    def test_profile_timer_is_explicit_and_not_global(self):
        doc={'version':1,'sfx':[{'id':1,'name':'click','source':'click.wav'}]}
        for region in ('ntsc','pal'):
            z,_,_=compile_document(doc,ROOT/'examples',region,'z80')
            h,_,_=compile_document(doc,ROOT/'examples',region,'hybrid')
            z2,_,_=compile_document(doc,ROOT/'examples',region,'z80')
            self.assertEqual(z,z2);self.assertEqual(int.from_bytes(h[19:21],'little'),1020)
            self.assertGreater(len(h),len(z))

    def test_collect_independent_failures(self):
        doc={'version':1,'sfx':[{'id':n,'name':f'missing_{n}','source':f'missing{n}.fur'} for n in (1,2)]}
        diagnostics=validate_document(doc,ROOT/'examples')
        self.assertEqual(len(diagnostics),2)
        self.assertEqual({d['resource'] for d in diagnostics},{'missing_1','missing_2'})
        self.assertTrue(all(d['source'] and d['code']=='resource.incompatible' for d in diagnostics))

    def test_parse_is_not_compatibility_and_inspection_is_json(self):
        info=inspect_source(ROOT/'examples/fm_ring.fur')
        self.assertTrue(info['parsed']);self.assertTrue(info['patterns']);self.assertIn('Run validation',info['compatibility'])
        json.dumps(info)
        wav=inspect_source(ROOT/'examples/click.wav',mode='hybrid')
        self.assertGreater(wav['conversion']['rate'],13000)

    def test_export_protects_inputs_and_all_sidecars(self):
        doc={'version':1,'sfx':[{'id':1,'name':'click','source':'click.wav'}]}
        data,report,header=compile_document(doc,ROOT/'examples')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bank.bin'
            export_bank(path,data,report,header)
            self.assertEqual(path.read_bytes(),data)
            self.assertTrue(all(path.with_suffix(s).exists() for s in ('.json','.deps','.h')))
            with self.assertRaises(ValueError):export_bank(path.with_suffix('.json'),data,report,header)
            with self.assertRaises(ValueError):export_bank(path,data,report,header,path.with_suffix('.h'))
        with self.assertRaises(ValueError):export_bank(ROOT/'examples/click.wav',data,report,header)

    def test_rom_assembly_checks_profile_and_preserves_template(self):
        assets=ROOT/'editor/assets'
        if not (assets/'z80-ntsc.bin').exists():self.skipTest('player templates not built')
        data,_,_=compile_document({'version':1},ROOT)
        raw,meta=make_rom(data,assets,'ntsc','z80')
        self.assertEqual(raw[meta['bank_address']:meta['bank_address']+len(data)],data)
        self.assertEqual(int.from_bytes(raw[meta['bank_length_address']:meta['bank_length_address']+4],'big'),len(data))
        self.assertEqual(int.from_bytes(raw[0x1a4:0x1a8],'big'),len(raw)-1)
        with self.assertRaises(ValueError):make_rom(data,assets,'pal','z80')

class EditorDocument(unittest.TestCase):
    def test_existing_empty_optional_sections_are_repaired_without_dropping_memberships(self):
        value={'version':1,'songs':[{'id':1,'name':'song','source':'song.fur','arrangements':{},
               'initial_arrangement':'deleted','markers':[],'groups':{},'events':{},'unknown':{}}]}
        doc=Document(value)
        self.assertTrue(doc.dirty)
        song=doc.data['songs'][0]
        for key in ('arrangements','initial_arrangement','markers','groups','events'):self.assertNotIn(key,song)
        self.assertEqual(song['unknown'],{})
        value['songs'][0].update(arrangements={'silent':[]},initial_arrangement='silent',groups={'empty_group':[]})
        doc.replace(value)
        self.assertEqual(doc.data['songs'][0]['arrangements'],{'silent':[]})
        self.assertEqual(doc.data['songs'][0]['groups'],{'empty_group':[]})

    def test_history_unknown_fields_and_object_order(self):
        original={'version':1,'custom':{'a':1},'songs':[]}
        doc=Document(original);value=copy.deepcopy(original);value['custom']={'b':2,'a':1};doc.replace(value)
        doc.travel(-1);self.assertEqual(doc.data,original);self.assertFalse(doc.dirty)
        doc.travel(1);self.assertTrue(doc.dirty)
        value['custom']={'a':1,'b':2};doc.replace(value)
        self.assertEqual(list(doc.data['custom']),['a','b']);doc.travel(-1)
        self.assertEqual(list(doc.data['custom']),['b','a'])

    def test_save_as_rebases_history_and_never_writes_resource(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'音.fur').write_bytes(b'unchanged')
            original={'version':1,'songs':[{'id':1,'name':'song','source':'音.fur'}],'extension':{'keep':True}}
            doc=Document(original,root/'bank.json');modified=copy.deepcopy(original);modified['songs'][0]['name']='new';doc.replace(modified)
            target=root/'sub/project.json';doc.save(target)
            self.assertEqual(doc.data['songs'][0]['source'],'../音.fur');self.assertFalse(doc.dirty)
            doc.travel(-1);self.assertEqual((doc.base_dir/doc.data['songs'][0]['source']).resolve(),root/'音.fur')
            self.assertEqual((root/'音.fur').read_bytes(),b'unchanged')
            with self.assertRaises(ValueError):doc.save(root/'音.fur')

if __name__=='__main__':unittest.main()
