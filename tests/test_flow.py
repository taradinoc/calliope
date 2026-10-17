import json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from calliope.flow import resolve
from calliope.furnace import Cell, Module, Subsong, FormatError, parse
from calliope.compiler import compile_bank
from calliope.reference import Player

def song(orders=3, rows=4):
    return Module(232,'flow',[(0,'fm',0),(0,'fm',1)],
                  [Subsong(60,rows,[list(range(orders))]*2,['a','b'],[1])],[],{},
                  {'compat_extended':[0]*28},440,'test')

def effect(m, order, row, channel, *effects):
    m.patterns.setdefault((0,channel,order),{})[row]=Cell(effects=[(i,e,v) for i,(e,v) in enumerate(effects)])

class OrderFlow(unittest.TestCase):
    def test_natural_end_and_explicit_loop(self):
        m=song();f=resolve(m,0,[0,1]);self.assertIsNone(f.loop)
        self.assertEqual(f.bundles,tuple(tuple((o,r) for r in range(4)) for o in range(3)))
        self.assertEqual(resolve(m,0,[0,1],1).loop,1)
    def test_jump_and_break_combine_in_either_channel_order(self):
        for first,second in (((0x0b,2),(0x0d,2)),((0x0d,2),(0x0b,2))):
            m=song();effect(m,0,1,0,first);effect(m,0,1,1,second)
            self.assertEqual(resolve(m,0,[0,1]).bundles,(((0,0),(0,1)),((2,2),(2,3))))
    def test_last_jump_wins_and_terminal_loop_splits_bundle(self):
        m=song();effect(m,0,3,0,(0x0b,2));effect(m,0,3,1,(0x0b,0),(0x0d,2))
        f=resolve(m,0,[0,1]);self.assertEqual(f.bundles,(((0,0),(0,1)),((0,2),(0,3))))
        self.assertEqual(f.loop,1)
    def test_stop_overrides_jump_and_unknown_rejected(self):
        m=song();effect(m,0,1,0,(0xff,0),(0x0b,0));self.assertIsNone(resolve(m,0,[0]).loop)
        m.flags['compat_extended'][23]=3
        with self.assertRaisesRegex(FormatError,'unknown'):resolve(m,0,[0])
    def test_legacy_flow_precedence(self):
        for treatment in (1,2):
            m=song();m.flags['compat_extended'][23]=treatment
            effect(m,0,0,0,(0x0b,2));effect(m,0,0,1,(0x0d,2))
            route=resolve(m,0,[0,1]).bundles
            self.assertEqual(route[1][0],(2,0) if treatment==1 else (1,2))
            effect(m,0,0,0,(0x0d,2));effect(m,0,0,1,(0x0b,2))
            self.assertEqual(resolve(m,0,[0,1]).bundles[1][0],(1,2))
            effect(m,0,0,0,(0x0b,1));effect(m,0,0,1,(0x0b,2))
            self.assertEqual(resolve(m,0,[0,1]).bundles[1][0],(1,0))

    def test_last_break_and_invalid_destinations(self):
        m=song(1);effect(m,0,0,0,(0x0d,1));self.assertEqual(resolve(m,0,[0]).bundles,(((0,0),),))
        m.flags['compat_extended'][3]=1;self.assertEqual(len(resolve(m,0,[0]).bundles[0]),4)
        effect(m,0,0,0,(0x0b,1))
        with self.assertRaisesRegex(FormatError,'outside song'):resolve(m,0,[0])
        effect(m,0,0,0,(0x0d,4));m.flags['compat_extended'][3]=0
        with self.assertRaisesRegex(FormatError,'outside pattern'):resolve(m,0,[0])
    def test_conflicting_or_unreachable_manifest_loop(self):
        m=song();effect(m,0,0,0,(0x0b,2));effect(m,2,3,0,(0x0b,2))
        with self.assertRaisesRegex(FormatError,'unreachable'):resolve(m,0,[0],1)
        with self.assertRaisesRegex(FormatError,'conflicts'):resolve(m,0,[0],0)
    def test_248_compatibility(self):
        m=song();m.version=248;m.flags={'compat':['jumpTreatment=0\nignoreJumpAtEnd=1\n\0'.encode().hex()]}
        effect(m,2,0,0,(0x0d,0));self.assertEqual(len(resolve(m,0,[0]).bundles[-1]),4)
    def test_compiled_cues_follow_source_route_and_repeat(self):
        m=parse(ROOT/'examples/fm_ring.fur');s=m.subsongs[0]
        s.rows=4;s.orders=[[0,1,2] for _ in m.channels];s.speeds=[1]
        m.flags['compat_extended'][23]=0
        m.patterns={}
        effect(m,0,1,0,(0x0b,2),(0xee,0xa1))
        effect(m,2,1,0,(0xee,0xb1));effect(m,2,3,0,(0x0b,2),(0x0d,1))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[{'id':1,'name':'route','source':str(ROOT/'examples/fm_ring.fur'),'voices':[{'channel':0,'slot':'fm0'}],'external_commands':'passthrough'}]}))
            with patch('calliope.compiler.parse',return_value=m):data,report,_=compile_bank(path)
        self.assertEqual([(r['order'],r['row']) for r in report['rows']],[(0,0),(0,1),(2,0),(2,1),(2,2),(2,3)])
        p=Player(data);p.start(1)
        for _ in range(11):p.step()
        self.assertEqual([(e[0],e[2]) for e in p.events],[(2,0xa1),(4,0xb1),(7,0xb1),(10,0xb1)])

if __name__=='__main__':unittest.main()
