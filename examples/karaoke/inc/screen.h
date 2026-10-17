#ifndef KARAOKE_SCREEN_H
#define KARAOKE_SCREEN_H

#include <genesis.h>

#define LYRIC_LINE_STEP 16
#define BALL_MAX_HEIGHT 40
#define BREAK_METER_COLUMNS 32

/* Initialize SGDK graphics once, then reset the presentation for each replay. */
void screenInit(void);
void screenReset(void);

/* The window covers lyrics without erasing them. Progress is 0..meter width. */
void screenInterlude(bool splash, u16 bars, bool intro, u16 progress);
void screenLyrics(void);

/* Cue spans use source-string offsets, including hidden underscores. */
u16 screenWordCenter(u16 line, u16 start, u16 end);
void screenHighlightWord(u16 line, u16 start, u16 end);
void screenActivateLine(u16 previous, u16 next);
void screenScroll(u16 pixels);
void screenBall(s16 fromX, s16 toX, u16 progress, u16 height,
                bool firstFlight, bool visible);
void screenError(const char *message);

#endif
