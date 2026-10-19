/* Editor-only player. Mailbox v1 uses explicit big-endian fields, not C structs.
 * Including the real adapter here permits read-only hybrid telemetry without
 * exporting implementation details in Calliope's public game API. */
#include <genesis.h>
#include "calliope.h"
/* SGDK may generate a same-named audio.h for the empty resource file. */
extern const u8 clp_bank[];
#include "ADAPTER_SOURCE"

volatile u8 clpEditorMailbox[256];
volatile const u32 clpEditorBankLength = 32;
static u16 read16(u16 at) { return ((u16)clpEditorMailbox[at] << 8) | clpEditorMailbox[at + 1]; }
static void write16(u16 at, u16 n)
{
    clpEditorMailbox[at] = n >> 8;
    clpEditorMailbox[at + 1] = n;
}
static void write32(u16 at, u32 n)
{
    write16(at, n >> 16);
    write16(at + 2, n);
}

static void telemetry(void)
{
    ClpStatus s = clp_status();
    clpEditorMailbox[40] = s.ready;
    clpEditorMailbox[41] = s.playing;
    clpEditorMailbox[42] = s.fault;
    clpEditorMailbox[43] = s.pcmUnderrun;
    clpEditorMailbox[44] = s.completed;
    write16(46, s.eventsLost);
#if CALLIOPE_HYBRID
    u16 i;
    for (i = 0; i < 9; i++)
        clpEditorMailbox[48 + i] = owners[i];
    clpEditorMailbox[57] = pcmOwner;
    write16(58, pcmToken);
    for (i = 0; i < 4; i++)
    {
        u16 at = 64 + i * 8;
        ClhSfx *e = &effects[i];
        clpEditorMailbox[at] = e->active;
        clpEditorMailbox[at + 1] = e->priority;
        write16(at + 2, e->token);
        write16(at + 4, e->mask);
    }
    write32(96, songTick);
#endif
}

int main(bool hardReset)
{
    u16 last = 0, frame = 0;
    bool ok;
    ClpEvent events[4];
    (void)hardReset;
    memset((void *)clpEditorMailbox, 0, sizeof(clpEditorMailbox));
    clpEditorMailbox[0] = 'C';
    clpEditorMailbox[1] = 'L';
    clpEditorMailbox[2] = 'P';
    clpEditorMailbox[3] = 'E';
    /* Keep the rescomp placeholder linked; the release host appends the bank
       above all template sections, at a fixed 32 KiB-aligned address. */
    write16(4, 1);
    clpEditorMailbox[26] = (clp_bank[0] == 'B' &&
        clp_init((const u8 *)0x100000, clpEditorBankLength)) ? 1 : 2;
    while (TRUE)
    {
        u16 sequence = read16(8), op = read16(12), a = read16(14), b = read16(16), c = read16(18);
        if (sequence != last)
        {
            ok = FALSE;
            switch (op)
            {
            case 1:
                ok = clp_start(a) != 0;
                break;
            case 2:
                ok = clp_pauseMusic();
                break;
            case 3:
                ok = clp_resumeMusic();
                break;
            case 4:
                ok = clp_stopMusic();
                break;
            case 5:
                ok = clp_stop();
                break;
            case 6:
                ok = clp_playSfx(a, b, c);
                break;
            case 7:
                ok = clp_playPcm(a, 1, b, c);
                break;
            case 8:
                ok = clp_stopSfx(a);
                break;
            case 9:
                ok = clp_selectArrangement(a, (ClpBoundary)b, c);
                break;
            case 10:
                ok = clp_muteGroups(a);
                break;
            case 11:
                ok = clp_mutePhysical(a);
                break;
            case 12:
                ok = clp_setMusicVolume(a, b);
                break;
            case 13:
                ok = clp_setMusicPcmPriority(a);
                break;
            case 16:
                ok = clp_setTempoScale(a, (ClpBoundary)b, c);
                break;
            case 14:
                ok = clp_setParts(a, (ClpBoundary)b, c);
                break;
            }
            clpEditorMailbox[22] = ok;
            write16(10, sequence);
            last = sequence;
        }
        clp_update();
        {
            u16 n = clp_poll(events, 4), i;
            for (i = 0; i < n; i++)
            {
                write16(104, events[i].id);
                write16(106, events[i].parameter);
                write32(108, events[i].tick);
                write16(112, read16(112) + 1);
            }
        }
        /* Telemetry is opt-in so normal audition does not add Z80 bus traffic. */
        if (clpEditorMailbox[24] && !(frame % 6))
            telemetry();
        else
        {
            ClpStatus s = clp_status();
            clpEditorMailbox[41] = s.playing;
            clpEditorMailbox[42] = s.fault;
            clpEditorMailbox[43] = s.pcmUnderrun;
        }
        write32(28, ++frame);
        /* Editor CPU profiler excludes the player's VBlank wait, including
           interrupts serviced during that wait. No game/driver ABI changes. */
        clpEditorMailbox[25] = 1;
        SYS_doVBlankProcess();
        clpEditorMailbox[25] = 0;
    }
    return 0;
}
