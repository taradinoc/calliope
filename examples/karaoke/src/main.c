/* Calliope example: receive advance events, queue words, then apply due cues. */
#include <genesis.h>
#include "calliope.h"
#include "audio.h"
#include "clp_ids.h"
#include "lyrics.h"
#include "timing.h"
#include "screen.h"
#include "diagnostics.h"

/* EExx passthrough uses xx as the event ID. These meanings belong to this game. */
#define LINE_CUE 0xf0
#define WORD_CUE 0xf1
#define EXTERNAL_EVENT_TYPE 1
#define CUE_CAPACITY 32
#define SCROLL_LEAD_TICKS 15
#define SCROLL_SETTLE_TICKS 3
#define BALL_MIN_HEIGHT 12

typedef struct {
    u32 contactTime;
    u16 id, line, start, end, x;
} Cue;

typedef enum {
    ERROR_NONE, ERROR_AUDIO, ERROR_LYRICS, ERROR_QUEUE, ERROR_EVENT_LOSS
} KaraokeError;

static Cue cues[CUE_CAPACITY];
static u16 readCue, writeCue, generation, contacts;
static u16 currentLine, wordEnd, visualLine;
static u16 plannedLine, plannedEnd;
static bool started, plannedStarted, stopped, completed, ballVisible;
static KaraokeError error;

/* One music tick uses 5 * videoHz units. Each frame adds 5 * musicHz.
   This preserves fractional music tick rates without floating-point math.
   Every contactTime and clockTime below uses these same scaled units. */
static u16 unitsPerTick;
static u32 clockTime, flightStartTime;
static s16 flightStartX;

/* smoke.py reads this snapshot. It does not control playback. */
volatile KaraokeDiagnostics karaokeDebug;

static void fail(KaraokeError reason)
{
    static const char *const messages[] = {
        "", "Audio command or driver error", "Lyrics do not match the cues",
        "Advance cue queue is full", "Calliope events were lost"
    };
    if (error != ERROR_NONE) return;
    error = reason;
    stopped = TRUE;
    clp_stop();
    screenError(messages[reason]);
}

static bool delimiter(char c)
{
    return c == ' ' || c == '-' || c == '_' || c == '\0';
}

static void synchronizeClock(u32 notificationTick)
{
    u32 observed = notificationTick * unitsPerTick;
    /* event.tick identifies the notification's music tick, not the future cue.
       Keep the fractional estimate if it lies inside that tick's interval.
       This is a frame-based estimate, not a sample-accurate playback clock. */
    if (clockTime < observed || clockTime >= observed + unitsPerTick)
        clockTime = observed;
}

static void receiveCue(const ClpEvent *event)
{
    Cue *cue;
    const char *line;
    u16 start, end;
    if (event->id != LINE_CUE && event->id != WORD_CUE) return;
    synchronizeClock(event->tick);
    if ((u16)(writeCue - readCue) >= CUE_CAPACITY) {
        fail(ERROR_QUEUE);
        return;
    }
    /* Advance notifications describe future words. Keep this parsing cursor
       separate from currentLine and wordEnd, which describe audible words. */
    if (event->id == LINE_CUE) {
        if (plannedStarted) ++plannedLine;
        plannedStarted = TRUE;
        plannedEnd = 0;
    }
    if (!plannedStarted || plannedLine >= (u16)LYRIC_COUNT) {
        fail(ERROR_LYRICS);
        return;
    }
    line = lyrics[plannedLine];
    start = plannedEnd;
    while (line[start] && delimiter(line[start])) ++start;
    end = start;
    while (line[end] && !delimiter(line[end])) ++end;
    if (start == end) {
        fail(ERROR_LYRICS);
        return;
    }
    /* A long gap can empty the queue. Start the next flight when notice arrives. */
    if (readCue == writeCue && started) flightStartTime = clockTime;
    cue = &cues[writeCue++ % CUE_CAPACITY];
    cue->contactTime = (event->tick + KARAOKE_LEAD_TICKS) * unitsPerTick;
    cue->id = event->id;
    cue->line = plannedLine;
    cue->start = start;
    cue->end = end;
    cue->x = screenWordCenter(plannedLine, start, end);
    plannedEnd = end;
    if (!ballVisible) {
        flightStartX = cue->x;
        flightStartTime = cue->contactTime - BALL_MAX_HEIGHT * unitsPerTick;
        ballVisible = TRUE;
    }
}

static void pollEvents(void)
{
    ClpEvent events[4];
    u16 count, i;
    /* clp_poll consumes at most four records per call. Drain full batches.
       clp_poll discards stale generations after replay. */
    do {
        count = clp_poll(events, 4);
        for (i = 0; i < count; ++i) {
            if (!stopped && events[i].type == EXTERNAL_EVENT_TYPE)
                receiveCue(&events[i]);
        }
    } while (count == 4);
}

static bool applyDueCues(void)
{
    bool landed = FALSE;
    while (readCue != writeCue && clockTime >= cues[readCue % CUE_CAPACITY].contactTime) {
        const Cue *cue = &cues[readCue++ % CUE_CAPACITY];
        if (cue->id == LINE_CUE) {
            ++karaokeDebug.lineCues;
            currentLine = cue->line;
            started = TRUE;
        } else ++karaokeDebug.wordCues;
        screenHighlightWord(cue->line, cue->start, cue->end);
        wordEnd = cue->end;
        flightStartTime = cue->contactTime;
        flightStartX = cue->x;
        ++contacts;
        karaokeDebug.lastCueTime = cue->contactTime;
        landed = TRUE;
    }
    return landed;
}

static void updateScroll(const Cue *next, bool landed)
{
    if (next && next->id == LINE_CUE && next->line > currentLine) {
        u32 begin = next->contactTime - SCROLL_LEAD_TICKS * unitsPerTick;
        u32 end = next->contactTime - SCROLL_SETTLE_TICKS * unitsPerTick;
        u16 pixels;
        /* Leave the departing word still until the ball leaves it. */
        if (begin <= flightStartTime) begin = flightStartTime + unitsPerTick;
        if (end <= begin) end = begin + unitsPerTick;
        if (clockTime >= begin) {
            if (visualLine != next->line) {
                screenActivateLine(visualLine, next->line);
                visualLine = next->line;
            }
            pixels = clockTime >= end ? LYRIC_LINE_STEP :
                (clockTime - begin) * LYRIC_LINE_STEP / (end - begin);
            screenScroll((next->line - 1) * LYRIC_LINE_STEP + pixels);
        }
    }
    if (landed) screenScroll(currentLine * LYRIC_LINE_STEP);
}

static void updateBall(const Cue *next, bool landed)
{
    u16 progress = 0, height = 0;
    s16 destinationX = flightStartX;
    /* Hold the exact landing position for one video frame. */
    if (next && !landed) {
        u32 duration = next->contactTime - flightStartTime;
        u32 elapsed = clockTime > flightStartTime ? clockTime - flightStartTime : 0;
        if (duration) {
            progress = elapsed >= duration ? 256 : elapsed * 256 / duration;
            destinationX = next->x;
            height = duration / unitsPerTick;
            if (height < BALL_MIN_HEIGHT) height = BALL_MIN_HEIGHT;
            if (height > BALL_MAX_HEIGHT) height = BALL_MAX_HEIGHT;
        }
    }
    screenBall(flightStartX, destinationX, progress, height, !started, ballVisible);
}

/* Breaks are computed at build time, so an empty event queue does not have
   to predict how long the instrumental gap will last. Audio keeps running underneath. */
static bool updateInterlude(bool landed)
{
    u16 i;
    for (i = 0; i < KARAOKE_BREAK_COUNT; ++i) {
        const KaraokeBreak *gap = &karaokeBreaks[i];
        u32 begin = gap->start * unitsPerTick;
        u32 end = gap->end * unitsPerTick;
        if (!landed && clockTime >= begin && clockTime < end) {
            /* The extra step lets the meter fill before the interval ends. */
            screenInterlude(!ballVisible, gap->bars, gap->intro,
                            (clockTime - begin) * (BREAK_METER_COLUMNS + 1) / (end - begin));
            screenBall(0, 0, 0, 0, FALSE, FALSE);
            return TRUE;
        }
    }
    if (!ballVisible) {
        screenInterlude(TRUE, 0, TRUE, 0);
        return TRUE;
    }
    screenLyrics();
    return FALSE;
}

static void updateDisplay(void)
{
    bool landed = applyDueCues();
    const Cue *next = readCue == writeCue ? NULL : &cues[readCue % CUE_CAPACITY];
    if (updateInterlude(landed)) return;
    updateScroll(next, landed);
    updateBall(next, landed);
}

static void restart(void)
{
    readCue = writeCue = currentLine = wordEnd = visualLine = 0;
    plannedLine = plannedEnd = 0;
    started = plannedStarted = stopped = completed = ballVisible = FALSE;
    contacts = 0;
    /* Start the intro estimate now; event ticks correct it once cues arrive. */
    clockTime = flightStartTime = 0;
    flightStartX = 160;
    error = ERROR_NONE;
    memset((void *)&karaokeDebug, 0, sizeof(karaokeDebug));
    screenReset();
    /* clp_init initializes the driver once. clp_start queues this playback. */
    generation = clp_start(CLP_SONGS_KARAOKE);
    if (!generation) fail(ERROR_AUDIO);
}

static void inspectStatus(void)
{
    ClpStatus status = clp_status();
    completed = status.completed && status.completedGeneration == generation;
    karaokeDebug.eventsLost = status.eventsLost;
    if (status.fault) fail(ERROR_AUDIO);
    /* Missing a cue shifts every later word. Stop instead of showing wrong lyrics. */
    if (status.eventsLost) fail(ERROR_EVENT_LOSS);
}

static void publishDiagnostics(void)
{
    karaokeDebug.line = currentLine;
    karaokeDebug.wordEnd = wordEnd;
    karaokeDebug.started = started;
    karaokeDebug.fault = error;
    karaokeDebug.completed = completed;
    karaokeDebug.queued = writeCue - readCue;
    karaokeDebug.clock = clockTime;
    /* Publish contacts last, after the corresponding display state. */
    karaokeDebug.contacts = contacts;
}

int main(bool hardReset)
{
    u16 previous = 0;
    unitsPerTick = IS_PAL_SYSTEM ? 250 : 300;
    screenInit();
    if (!clp_init(clp_bank, sizeof(clp_bank))) {
        screenError("Calliope initialization failed");
        while (TRUE) SYS_doVBlankProcess();
    }
    restart();
    while (TRUE) {
        u16 buttons = JOY_readJoypad(JOY_1);
        /* Hybrid Calliope needs one update per video frame, even without cues. */
        clp_update();
        if (!stopped && !completed) clockTime += KARAOKE_RATE5;
        pollEvents();
        if ((buttons & BUTTON_A) && !(previous & BUTTON_A)) restart();
        if ((buttons & BUTTON_B) && !(previous & BUTTON_B)) {
            if (!clp_stop()) fail(ERROR_AUDIO);
            stopped = TRUE;
        }
        previous = buttons;
        if (!stopped) updateDisplay();
        inspectStatus();
        publishDiagnostics();
        SYS_doVBlankProcess();
    }
    return 0;
}
