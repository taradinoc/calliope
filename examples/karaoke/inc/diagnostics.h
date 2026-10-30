#ifndef KARAOKE_DIAGNOSTICS_H
#define KARAOKE_DIAGNOSTICS_H

#include <genesis.h>

/* Emulator observations only. Playback decisions do not use this structure.
   Keep the field order synchronized with field_offsets in smoke.py. */
typedef struct {
    u16 line, wordEnd, started;
    u16 lineCues, wordCues, fault, eventsLost, completed;
    u16 scroll, ballX, ballY, contacts, queued, textWrites;
    u32 clock, lastCueTime;
    /* 0 = lyrics, 1 = end card, 2 = menu prompt, 3 = song menu. */
    u16 stage, selectedSong;
} KaraokeDiagnostics;
extern volatile KaraokeDiagnostics karaokeDebug;

#endif
