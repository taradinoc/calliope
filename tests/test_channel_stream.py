import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from calliope.channel_stream import split
from calliope.compiler import compile_bank
from calliope.furnace import Cell,FormatError,parse
from calliope.reference import Player
from calliope.timing import CommandStream
from sequencer_study import tokens,timed_trace


class ChannelStreams(unittest.TestCase):
    def test_synchronized_partition_preserves_each_channel_time_and_conductor(self):
        raw=bytes((0x38,0,0x15,8,0,0,0x14,0,0x10,0,2,0x14,1,0x31,20,0,0,
                   0x38,1,0x14,0,0x11,0,0x34,0x12,0,6,0x14,0,0x36,0,0x35,0x12,0,6))
        conductor,fm,pcm=split(raw,2,0xfd,True)
        self.assertEqual(timed_trace(tokens(conductor)),[(0,bytes((0x15,8,0,0))),(12,bytes((0xfd,)))])
        self.assertEqual(timed_trace(tokens(fm)),[(0,bytes((0x10,2))),(0,bytes((0x11,0x34,0x12))),(6,bytes((0x36,0x35,0x12))),(12,bytes((0xfd,)))])
        self.assertEqual(timed_trace(tokens(pcm)),[(0,bytes((0x31,20,0,0))),(12,bytes((0xfd,)))])
        self.assertTrue(all(t[0] not in (0x14,0x38) for s in (conductor,fm,pcm) for t in tokens(s)))

    def test_events_follow_preparation_without_changing_tick_or_source_order(self):
        events=bytes((0x20,0xcc,0,1,0,0x20,0xa2,0,1,0))
        raw=bytes((0x14,0,0x10,0,2))+events+bytes((0,3,0x14,1,0x13,1,0,3))
        conductor,fm,last=split(raw,2,0xfd,True)
        self.assertEqual(timed_trace(tokens(conductor)),[(6,b'\xfd')])
        self.assertEqual(timed_trace(tokens(last))[:2],[(0,events[:5]),(0,events[5:])])
        self.assertEqual(timed_trace(tokens(last))[-2:],[(3,b'\x13'),(6,b'\xfd')])

    def test_events_can_follow_last_chip_part_when_pcm_is_last_in_manifest(self):
        event=bytes((0x20,0xcc,0,1,0))
        raw=bytes((0x14,1,0x31,20,0,0))+event+bytes((0,3))
        conductor,fm,pcm=split(raw,2,0xfd,True,event_part=0)
        self.assertEqual(timed_trace(tokens(fm))[:1],[(0,event)])
        self.assertEqual(timed_trace(tokens(pcm))[:1],[(0,bytes((0x31,20,0,0)))])
        self.assertEqual(timed_trace(tokens(conductor)),[(3,b'\xfd')])

    def test_unsynchronized_patch_stays_in_channel_and_empty_channel_advances(self):
        conductor,fm,empty=split(bytes((0x14,0,0x10,0,2,0x11,0,0x34,0x12,0,9)),2,0xff)
        self.assertEqual(tokens(conductor),(bytes((0x88,)),bytes((0xff,))))
        self.assertEqual(tokens(empty),(bytes((0x88,)),bytes((0xfd,))))
        self.assertEqual(tokens(fm)[0],bytes((0x10,2)))
        with self.assertRaises(FormatError):split(bytes((0x11,0,1,2)),1,0xfd)

    def test_short_and_long_waits_keep_exact_duration_and_terminal_space(self):
        for ticks in (1,125,126,255,256,769):
            stream=CommandStream(compact=True);stream.wait(ticks)
            encoded=stream.finish()+bytes((0xfd,))
            parsed=tokens(encoded)
            self.assertEqual(timed_trace(parsed),[(ticks,bytes((0xfd,)))])
            self.assertEqual(len(parsed[0]),1 if ticks<=125 else 2)
            self.assertTrue(all(t[0]<0xfd for t in parsed[:-1]))

    def test_order_records_share_channel_patterns_and_play_full_duration(self):
        source=ROOT/'examples/fm_ring.fur';module=parse(source);song=module.subsongs[0]
        song.rows=16;song.speeds=[6];song.orders=[[0]*4 for _ in module.channels]
        module.instruments[0]['features'].pop('MA',None)
        module.patterns={(0,0,0):{0:Cell(note=108,instrument=0),8:Cell(note=180)}}
        item={'id':1,'name':'shared','source':str(source),'voices':[{'channel':0,'slot':'fm0'}]}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bank.json';path.write_text(json.dumps({'version':1,'songs':[item]}))
            with patch('calliope.compiler.parse',return_value=module):data,_,_=compile_bank(path)
        descriptor=int.from_bytes(data[10:13],'little');table=int.from_bytes(data[descriptor+4:descriptor+7],'little')
        pointers=[]
        for i in range(4):
            record=int.from_bytes(data[table+i*3:table+i*3+3],'little')
            pointers.append(int.from_bytes(data[record+3:record+6],'little'))
        self.assertEqual(pointers[1:], [pointers[1]]*3)
        for count in (0,17,128):
            malformed=bytearray(data);malformed[descriptor+11]=count
            with self.assertRaises(FormatError):Player(malformed).start(1)
        p=Player(data);p.start(1)
        for _ in range(385):p.step()
        self.assertEqual([e[0] for e in p.trace if e[1]==0x11],[1,97,193,289])
        self.assertFalse(p.playing);self.assertEqual(p.tick,385)


if __name__=='__main__':unittest.main()
