/* SGDK display details. See main.c for the Calliope event flow. */
#include <genesis.h>
#include "screen.h"
#include "diagnostics.h"
#include "lyrics.h"

#define SCREEN_COLUMNS 40
#define PLANE_ROWS 64
#define BALL_TILE TILE_USER_INDEX
#define ACTIVE_PAL PAL0
#define PLAYED_PAL PAL1
#define UPCOMING_PAL PAL2
#define WORD_PAL PAL3

static u16 centerRow;
static u16 rowLines[PLANE_ROWS / 2];
static u16 activeLine;
static bool overlay;
static u16 overlayBars, overlayProgress;
static bool overlaySplash, overlayIntro;
static const u32 ballTile[8] = {
    0x00011000, 0x00122100, 0x01222110, 0x11221111,
    0x11111111, 0x01111110, 0x00111100, 0x00011000
};

/* Cue spans index the source string, which includes hidden underscores.
   Convert those offsets to visible columns for drawing and ball contacts. */
static u16 visibleColumn(u16 line, u16 sourceEnd)
{
    u16 i, column = 0;
    for (i = 0; i < sourceEnd; ++i)
        if (lyrics[line][i] != '_') ++column;
    return column;
}

static u16 lineColumn(u16 line)
{
    return (SCREEN_COLUMNS - visibleColumn(line, strlen(lyrics[line]))) / 2;
}

static void paint(u16 line, u16 start, u16 end, u16 palette)
{
    char text[39];
    u16 i, length = 0;
    for (i = start; i < end; ++i)
        if (lyrics[line][i] != '_') text[length++] = lyrics[line][i];
    text[length] = 0;
    /* SGDK creates a temporary tile buffer. COPY keeps it valid until VBlank. */
    VDP_drawTextEx(BG_A, text, TILE_ATTR(palette, FALSE, FALSE, FALSE),
                  lineColumn(line) + visibleColumn(line, start),
                  (centerRow + line * 2) % PLANE_ROWS, DMA_QUEUE_COPY);
    karaokeDebug.textWrites += length;
}

static void paintLine(u16 line, u16 palette)
{
    paint(line, 0, strlen(lyrics[line]), palette);
}

void screenInit(void)
{
    u16 palette[16] = {0};
    u16 i;
    VDP_setScreenWidth320();
    VDP_setPlaneSize(64, PLANE_ROWS, TRUE);
    centerRow = VDP_getScreenHeight() / 16;
    VDP_clearPlane(WINDOW, TRUE);
    VDP_setWindowOff();
    for (i = 1; i < 16; ++i) palette[i] = 0xeee;
    PAL_setPalette(ACTIVE_PAL, palette, CPU);
    for (i = 1; i < 16; ++i) palette[i] = 0x606;
    PAL_setPalette(PLAYED_PAL, palette, CPU);
    for (i = 1; i < 16; ++i) palette[i] = 0x888;
    PAL_setPalette(UPCOMING_PAL, palette, CPU);
    for (i = 1; i < 16; ++i) palette[i] = 0xe0e;
    palette[2] = 0xeee; /* White highlight on the ball. Font uses index 15. */
    PAL_setPalette(WORD_PAL, palette, CPU);
    VDP_loadTileData(ballTile, BALL_TILE, 1, CPU);
}

void screenError(const char *message)
{
    VDP_setWindowOff();
    /* BG_B stays fixed while the lyrics on BG_A scroll. */
    VDP_drawTextEx(BG_B, message, TILE_ATTR(ACTIVE_PAL, TRUE, FALSE, FALSE),
                  1, 0, DMA_QUEUE_COPY);
}

void screenReset(void)
{
    u16 i;
    overlay = FALSE;
    karaokeDebug.textWrites = 0;
    screenError("                                      ");
    VDP_clearPlane(BG_A, TRUE);
    for (i = 0; i < PLANE_ROWS / 2; ++i) rowLines[i] = 0;
    activeLine = 0;
    screenScroll(0);
    screenInterlude(TRUE, 0, TRUE, 0);
    screenBall(160, 160, 0, 0, FALSE, FALSE);
}

u16 screenWordCenter(u16 line, u16 start, u16 end)
{
    return lineColumn(line) * 8 +
        (visibleColumn(line, start) + visibleColumn(line, end)) * 4;
}

void screenHighlightWord(u16 line, u16 start, u16 end)
{
    paint(line, start, end, WORD_PAL);
}

void screenActivateLine(u16 previous, u16 next)
{
    activeLine = next;
    paintLine(previous, PLAYED_PAL);
    paintLine(next, ACTIVE_PAL);
}

void screenScroll(u16 pixels)
{
    u16 line;
    u16 top = pixels / 8;
    u16 bottom = (pixels + VDP_getScreenHeight() - 1) / 8;
    /* Reuse rows after they leave the viewport. Keep existing rows intact so
       word highlights survive fractional scrolling between lyric lines. */
    for (line = top > centerRow ? (top - centerRow + 1) / 2 : 0;
         centerRow + line * 2 <= bottom; ++line) {
        u16 worldRow = centerRow + line * 2;
        u16 slot = line % (PLANE_ROWS / 2);
        if (worldRow < top || worldRow > bottom || rowLines[slot] == line + 1) continue;
        VDP_drawTextEx(BG_A, "                                        ",
                      TILE_ATTR(UPCOMING_PAL, FALSE, FALSE, FALSE),
                      0, worldRow % PLANE_ROWS, DMA_QUEUE_COPY);
        /* Rows beyond the final lyric must replace the previous lap with
           blank tiles too, or old lyrics reappear below the last line. */
        if (line < (u16)LYRIC_COUNT)
            paintLine(line, line < activeLine ? PLAYED_PAL :
                            line == activeLine ? ACTIVE_PAL : UPCOMING_PAL);
        rowLines[slot] = line + 1;
    }
    VDP_setVerticalScrollVSync(BG_A, pixels);
    karaokeDebug.scroll = pixels;
}

void screenBall(s16 fromX, s16 toX, u16 progress, u16 height,
                bool firstFlight, bool visible)
{
    /* Progress is 0..256. The arc is 4*p*(1-p), with zero height at each end.
       The first flight descends from above instead of bouncing from a word. */
    s16 x = fromX + ((s32)toX - fromX) * progress / 256;
    s16 y = centerRow * 8 - 8;
    if (firstFlight) y -= (u32)height * (256 - progress) * (256 - progress) / 65536;
    else y -= (u32)height * 4 * progress * (256 - progress) / 65536;
    VDP_setSpriteFull(0, visible ? x - 4 : -16, visible ? y : -16,
                     SPRITE_SIZE(1, 1),
                     TILE_ATTR_FULL(WORD_PAL, TRUE, FALSE, FALSE, BALL_TILE), 0);
    VDP_updateSprites(1, DMA_QUEUE);
    karaokeDebug.ballX = x;
    karaokeDebug.ballY = y;
}

/* WINDOW replaces BG_A on screen while preserving its scroll and highlights.
   COPY also keeps these stack-built strings valid until the VBlank DMA. */
static void centered(const char *text, u16 row)
{
    VDP_drawTextEx(WINDOW, text, TILE_ATTR(ACTIVE_PAL, FALSE, FALSE, FALSE),
                  (SCREEN_COLUMNS - strlen(text)) / 2, row, DMA_QUEUE_COPY);
    karaokeDebug.textWrites += strlen(text);
}

void screenInterlude(bool splash, u16 bars, bool intro, u16 progress)
{
    char label[39], number[8], meter[BREAK_METER_COLUMNS + 1];
    u16 i;
    if (progress > BREAK_METER_COLUMNS) progress = BREAK_METER_COLUMNS;
    /* Redraw labels on a mode change; otherwise only changed dashes need DMA. */
    if (!overlay || overlaySplash != splash || overlayBars != bars || overlayIntro != intro) {
        centered("                                      ", centerRow - 4);
        centered("                                      ", centerRow - 1);
        centered("                                      ", centerRow + 2);
        centered("                                      ", centerRow + 5);
        centered("                                      ", centerRow + 7);
        VDP_setWindowVPos(TRUE, 0);
        if (splash) {
            centered(KARAOKE_TITLE, centerRow - 4);
            centered("in the style of", centerRow - 1);
            centered(KARAOKE_ARTIST, centerRow + 2);
        }
        if (bars) {
            uintToStr(bars, number, 1);
            strcpy(label, number);
            strcat(label, intro ? " BAR INTRO" : " BAR BREAK");
            centered(label, centerRow + 5);
        }
        overlayProgress = 65535;
    }
    if (bars && progress != overlayProgress) {
        for (i = 0; i < BREAK_METER_COLUMNS; ++i) meter[i] = i < progress ? '-' : ' ';
        meter[BREAK_METER_COLUMNS] = 0;
        centered(meter, centerRow + 7);
    }
    overlay = TRUE;
    overlaySplash = splash;
    overlayBars = bars;
    overlayIntro = intro;
    overlayProgress = progress;
}

void screenLyrics(void)
{
    if (!overlay) return;
    VDP_setWindowOff();
    overlay = FALSE;
    /* Count a window switch as a visual change for smoke.py's pixel checks. */
    ++karaokeDebug.textWrites;
}
