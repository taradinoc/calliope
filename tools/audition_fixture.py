"""Create focused test ROMs without altering the audition resource bank."""
def hide_song(raw, song_id):
    bank=raw.index(b'BGM1');table=bank+int.from_bytes(raw[bank+10:bank+13],'little')
    for at in range(table,table+raw[bank+6]*24,24):
        if int.from_bytes(raw[at:at+2],'little')==song_id:
            raw[at:at+2]=b'\xff\xff'
    return raw
