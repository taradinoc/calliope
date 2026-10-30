#ifndef CALLIOPE_H
#define CALLIOPE_H
#include "calliope_config.h"
#include <genesis.h>
typedef struct
{
   u8 type, flags;
   u16 id, parameter, generation;
   u32 tick;
} ClpEvent;
typedef struct
{
   u8 ready, playing, fault, pending, pcmUnderrun;
   u16 generation, completedGeneration, eventsLost;
   bool completed;
} ClpStatus;
bool clp_init(const u8 *bank, u32 length);
/* Queue a song; return its internally assigned generation, or zero on failure.
   Successful starts advance 1..65535, wrapping to 1. Init resets the counter. */
u16 clp_start(u16 song);
/* Call once per frame. The hybrid prototype prepares future chip commands. */
void clp_update(void);
typedef enum
{
   CLP_IMMEDIATE = 1,
   CLP_NEXT_BAR = 8,
   CLP_NEXT_MARKER = 16
} ClpBoundary;
/* BAR follows the Furnace major highlight; explicit markers remain supported. */
/* Pending part controls use latest-request-wins semantics. */
bool clp_setParts(u16 mask, ClpBoundary boundary, u16 marker);
bool clp_selectArrangement(u8 arrangement, ClpBoundary boundary, u16 marker);
/* 8.8 multiplier, applied to authored tempo; SFX timing is independent.
   Effective tick rate rounds to 0.2 Hz. Latest tempo request wins, independently
   of part requests. Immediate applies while paused; deferred waits for resume.
   Stop cancels pending tempo. New song starts reset to normal. Hybrid applies
   at its preparation frontier. False means invalid or not queued. */
#define CLP_TEMPO_MIN 128
#define CLP_TEMPO_NORMAL 256
#define CLP_TEMPO_MAX 512
bool clp_setTempoScale(u16 scale, ClpBoundary boundary, u16 marker);
/* Membership changes reapply current group mutes when the request is consumed. */
bool clp_setPartGroups(u8 part, u16 groups);
bool clp_muteGroups(u16 mask);
bool clp_stop(void);
bool clp_stopMusic(void);
/* Bits 0..4 FM, 5..7 PSG tones, 8 noise, 9 music PCM. SFX are unaffected. */
bool clp_mutePhysical(u16 mask);
/* Full scale: FM 127, PSG 15. Changes held music without restarting envelopes. */
bool clp_setMusicVolume(u8 fm, u8 psg);
/* PCM only. Default 0: all SFX can interrupt music. SFX win ties with music;
   music replaces a playing SFX only when its priority is strictly greater.
   Used when PCM requests are processed; persists across songs, resets on init.
   Returns false before initialization. Buffered audio is already committed. */
bool clp_setMusicPcmPriority(u8 priority);
bool clp_pauseMusic(void);
bool clp_resumeMusic(void);
bool clp_stopSfx(u16 token);
/* True while this SFX token owns a sequencer or PCM source. Pending commands
   conservatively report busy so a queued start cannot look finished. Hybrid
   queries its preparation frontier; subsequent starts are ordered after that
   prepared audio, including the final SFX page. False before initialization. */
bool clp_sfxActive(u16 token);
/* Return means queued. Arbitration may reject equal/lower-priority requests. */
bool clp_playSfx(u16 effect, u8 priority, u16 token);
/* Logical source 0 is music, 1 is PCM SFX. Music uses the configured PCM
   priority; SFX use the argument. Rejected notes and interrupted notes drop. */
bool clp_playPcm(u16 effect, u8 source, u8 priority, u16 token);
/* Consume at most four records; stale generations are discarded within that budget. */
u16 clp_poll(ClpEvent *events, u16 capacity);
ClpStatus clp_status(void);
/* Music ticks since consumed start; pauses freeze, loops continue, u32 wraps.
   Tick duration follows authored tempo. Hybrid reports the last consumed page header.
   Before initialization: zero. Queued start/stop do not take effect immediately. */
u32 clp_getElapsed(void);
#endif
