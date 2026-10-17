"""Generate small original Furnace projects for tests and auditioning.
These are authored fixtures, not exports or copies of Bonk music.
"""
from pathlib import Path
import struct
import zlib

def make(version=248,kind=0x83,notes=(108,112,115,120),event=True,extra_tracks=(),track_channels=None,initial_effects=None,hz=60,speeds=(6,)):
    data=bytearray(b'-Furnace module-'+struct.pack('<HHI',version,0,0)+bytes(8))
    def block(tag,body):
        offset=len(data); data.extend(tag+struct.pack('<I',len(body))+body); return offset
    def feature(tag,body): return tag+struct.pack('<H',len(body))+body
    channels=6 if kind==0x83 else 4
    # Four operators, algorithm 7, zero feedback; no modulation carriers.
    fm=bytes((0xf4,0x70,0,0,0))+bytes((0x31,36,31,0,0,0x08,0,0))*4
    ins=block(b'INS2',struct.pack('<HH',version,1 if kind==0x83 else 0)+feature(b'NA',b'Fixture\0')+(feature(b'FM',fm) if kind==0x83 else b'')+b'EN')
    patterns=[]; channel_names=['']*channels
    initial_effects=initial_effects or {}
    for channel,track in enumerate([(notes,'melody')]+list(extra_tracks)):
        channel=track_channels[channel] if track_channels is not None else channel
        track_notes,name=track
        channel_names[channel]=name
        if len(track_notes)!=len(notes):raise ValueError('fixture tracks must have equal lengths')
        stream=bytearray()
        for i,note in enumerate(track_notes):
            if i==0 and channel in initial_effects:stream.extend((0x1b,note,0,*initial_effects[channel]))
            elif i==0 and event and channel==0: stream.extend((0x1b,note,0,0xee,1))
            elif i==0: stream.extend((3,note,0))
            else: stream.extend((1,note))
            stream.extend((0x80+2,))
        stream.extend((1,180,255))
        if version==156:
            body=bytearray(struct.pack('<4H',channel,0,0,0))
            for row in range(len(notes)*5+1):
                note=octave=0;instrument=volume=effect=value=-1
                if row%5==0:
                    i=row//5
                    if i==len(notes):note=100
                    else:
                        n=track_notes[i]-60;note=n%12 or 12;octave=n//12-(n%12==0)
                        if i==0:
                            instrument=0
                            if channel in initial_effects:effect,value=initial_effects[channel]
                            elif event and channel==0:effect,value=0xee,1
                body.extend(struct.pack('<6h',note,octave,instrument,volume,effect,value))
            patterns.append(block(b'PATR',body+name.encode()+b'\0'))
        else:
            patterns.append(block(b'PATN',bytes((0,))+(struct.pack('<H',channel) if version>=240 else bytes((channel,)))+struct.pack('<H',0)+name.encode()+b'\0'+stream))
    rows=len(notes)*5+1
    names=b''.join(name.encode()+b'\0' for name in channel_names)
    metadata=bytes(channels)+bytes((1,))*channels+bytes(channels*2)+names+bytes(channels)
    if version==248:
        sub=block(b'SNG2',struct.pack('<fBBHHBBHHB',hz,1,1,rows,1,4,16,1,1,len(speeds))+struct.pack('<16H',*(list(speeds)+[0]*(16-len(speeds))))+b'Main\0\0'+metadata+bytes(channels*4))
        info=bytearray(b'Calliope fixture\0Author\0'+b'\0'*6)
        info.extend(struct.pack('<fBfHHHHfffIB',440,1,1,channels,1,kind,channels,1,0,0,0,1))
        for code,ptrs in ((1,[sub]),(4,[ins]),(7,patterns)):
            info.extend(struct.pack('<BI',code,len(ptrs))+b''.join(struct.pack('<I',p) for p in ptrs))
        info.append(0)
        offset=block(b'INF2',info)
    else:
        directories=[block(b'ADIR',struct.pack('<I',0)) for _ in range(3)]
        body=bytearray(struct.pack('<BBBBfHHBBHHHI',0,speeds[0],speeds[-1],1,hz,rows,1,4,16,1,0,0,len(patterns)))
        body.extend(bytes((kind,))+bytes(31)+bytes(64+128)+b'Calliope fixture\0Author\0'+struct.pack('<f',440))
        compat=bytearray(20); compat[1]=2
        body.extend(compat+struct.pack('<I',ins)+b''.join(struct.pack('<I',pat) for pat in patterns)+metadata+b'\0'+struct.pack('<f',1))
        body.extend(bytes(28)+struct.pack('<HH',1,1)+b'Main\0\0'+bytes(4)+bytes(6))
        body.extend(struct.pack('<fffIB',1,0,0,0,1)+bytes(8)+bytes((len(speeds),))+bytes(speeds)+bytes(16-len(speeds))+bytes(1)+struct.pack('<III',*directories))
        offset=block(b'INFO',body)
    struct.pack_into('<I',data,20,offset)
    return zlib.compress(data)

def main():
    import json,math,wave
    root=Path(__file__).resolve().parents[1]; target=root/'examples'; target.mkdir(exist_ok=True)
    for version in (232,248): (target/f'melody{version}.fur').write_bytes(make(version))
    (target/'arrangements232.fur').write_bytes(make(232,extra_tracks=(((108,111,115,120),'sad'),((84,84,91,91),'bass'))))
    (target/'noise232.fur').write_bytes(make(232,3,notes=(108,109,110),event=False,track_channels=(3,),initial_effects={3:(0x20,1)}))
    (target/'coupled232.fur').write_bytes(make(232,3,event=False,extra_tracks=(((108,112,115,120),'noise'),),track_channels=(2,3),initial_effects={3:(0x20,0x11)}))
    (target/'multi_psg232.fur').write_bytes(make(232,3,event=False,
        extra_tracks=(((112,115,119,124),'harmony'),((84,84,91,91),'bass'),((108,109,110,108),'noise')),
        initial_effects={3:(0x20,1)}))
    (target/'multi_fm232.fur').write_bytes(make(232,event=False,
        extra_tracks=(((112,115,119,124),'harmony'),((115,119,122,127),'fifth'),
                      ((84,84,91,91),'bass'),((120,124,127,132),'high'))))
    with wave.open(str(target/'click.wav'),'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b''.join(struct.pack('<h',round(14000*math.sin(i*.13)*math.exp(-i/240))) for i in range(2205)))
    manifest={'version':1,'songs':[{'id':1,'name':'melody','source':'melody232.fur','voices':[{'channel':'melody','slot':'fm0'}],'events':{'1':{'id':7,'parameter':42}}}],'sfx':[{'id':2,'name':'click','source':'click.wav','priority':1}]}
    if not (target/'bank.json').exists(): (target/'bank.json').write_text(json.dumps(manifest,indent=2)+'\n',newline='\r\n')
if __name__=='__main__': main()
