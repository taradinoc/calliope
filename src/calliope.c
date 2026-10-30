#include "calliope_config.h"
#if defined(CALLIOPE_HYBRID) && CALLIOPE_HYBRID
#include "hybrid.c"
#else
#include "calliope.h"
extern const u8 clp_driver[8192];
#ifndef CALLIOPE_MAILBOX
#define CALLIOPE_MAILBOX ((vu8 *)0xA01D00)
#endif
#define M CALLIOPE_MAILBOX
static const u8 *bankData;
static u32 bankSize, elapsedTick;
static u16 request;
static u32 partMeta;
static u8 partCount, arrangementCount;
static u16 selectedParts, mutedGroups, partGroups[16];
static u16 le16(const u8 *p) { return p[0] | ((u16)p[1] << 8); }
static u32 le24(const u8 *p) { return p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16); }
#include "effect_index.h"
static bool sendAt(u8 opcode, u8 flags, u16 generation, u32 argument)
{
    u8 w, r;
    u16 offset;
    bool taken;
    /* Volatile staging prevents LTO moving byte extraction into BUSREQ. */
    const volatile u8 bytes[8] = {opcode, flags, (u8)generation, (u8)(generation >> 8),
                                  (u8)argument, (u8)(argument >> 8), (u8)(argument >> 16), (u8)(argument >> 24)};
    SYS_disableInts();
    taken = Z80_getAndRequestBus(TRUE);
    w = M[1];
    r = M[2];
    if ((u8)(w - r) >= 16)
    {
        if (!taken)
            Z80_releaseBus();
        SYS_enableInts();
        return FALSE;
    }
    offset = 64 + ((w & 15) << 3);
    M[offset] = bytes[0];
    M[offset + 1] = bytes[1];
    M[offset + 2] = bytes[2];
    M[offset + 3] = bytes[3];
    M[offset + 4] = bytes[4];
    M[offset + 5] = bytes[5];
    M[offset + 6] = bytes[6];
    M[offset + 7] = bytes[7];
    M[1] = w + 1;
    if (!taken)
        Z80_releaseBus();
    SYS_enableInts();
    return TRUE;
}
static bool send(u8 opcode, u16 generation, u32 argument)
{
    return sendAt(opcode, 0, generation, argument);
}
bool clp_init(const u8 *bank, u32 length)
{
    u32 address = (u32)bank;
    u16 frames;
    if ((address & 32767) || length < 32 || address + length > 0x400000 ||
        bank[0] != 'B' || bank[1] != 'G' || bank[2] != 'M' || bank[3] != '1' || bank[4] != 12 || bank[5] != 0 || (le16(bank + 19) && (le16(bank + 19) != 1016 || bank[21] != (SYS_isPAL() ? 1 : 0))))
        return FALSE;
    if (!effectIndexValid(bank, length))
        return FALSE;
    bankData = bank;
    bankSize = length;
    request = 0;
    elapsedTick = 0;
    partMeta = 0;
    partCount = 0;
    arrangementCount = 0;
    Z80_loadCustomDriver(clp_driver, 8192);
    SYS_disableInts();
    Z80_requestBus(TRUE);
    M[16] = (u8)address;
    M[17] = (u8)(address >> 8);
    M[18] = (u8)(address >> 16);
    M[7] = SYS_isPAL() ? 1 : 0;
    Z80_releaseBus();
    SYS_enableInts();
    Z80_useBusProtection(0x1D06);
    Z80_setForceDelayDMA(TRUE);
    for (frames = 0; frames < 120; frames++)
    {
        if (clp_status().ready)
            return TRUE;
        SYS_doVBlankProcess();
    }
    return FALSE;
}
u16 clp_start(u16 song)
{
    u16 i, generation = (u16)(request + 1);
    u32 offset;
    if (!generation) generation = 1;
    if (!bankData)
        return 0;
    offset = le24(bankData + 10);
    if (offset + (u32)bankData[6] * 24 > bankSize)
        return 0;
    for (i = 0; i < bankData[6]; i++, offset += 24)
    {
        if (le16(bankData + offset) == song)
        {
            u32 meta = le24(bankData + offset + 8);
            u8 count = bankData[offset + 11] & 31, arrangements = bankData[offset + 12], initial = bankData[offset + 13];
            if (!count || count > 16 || arrangements > 16 || (count && (!arrangements || initial >= arrangements || meta + (u32)count * 4 + (u32)arrangements * 2 > bankSize)))
                return 0;
            if (!send(1, generation, offset))
                return 0;
            request = generation;
            partMeta = meta;
            partCount = count;
            arrangementCount = arrangements;
            mutedGroups = 0;
            for (i = 0; i < count; i++)
                partGroups[i] = le16(bankData + meta + i * 4 + 1);
            selectedParts = count ? le16(bankData + meta + count * 4 + initial * 2) : 0;
            return generation;
        }
    }
    return 0;
}
bool clp_setParts(u16 mask, ClpBoundary boundary, u16 marker)
{
    u16 used = 0, effective = 0, i;
    if (!partCount || ((u32)mask >> partCount) ||
        (boundary != CLP_IMMEDIATE && boundary != CLP_NEXT_BAR && boundary != CLP_NEXT_MARKER))
        return FALSE;
    for (i = 0; i < partCount; i++)
        if (mask & (1 << i))
        {
            const u8 *voice = bankData + partMeta + i * 4;
            u16 resource;
            if (voice[0] > 9)
                return FALSE;
            resource = 1 << voice[0];
            if (used & resource)
                return FALSE;
            used |= resource;
            if (!(partGroups[i] & mutedGroups))
                effective |= 1 << i;
        }
    if (!sendAt(11, (u8)boundary, request, effective | ((u32)marker << 16)))
        return FALSE;
    selectedParts = mask;
    return TRUE;
}
bool clp_setTempoScale(u16 scale, ClpBoundary boundary, u16 marker)
{
    if (!partCount || scale < CLP_TEMPO_MIN || scale > CLP_TEMPO_MAX ||
        (boundary != CLP_IMMEDIATE && boundary != CLP_NEXT_BAR && boundary != CLP_NEXT_MARKER))
        return FALSE;
    return sendAt(13, (u8)boundary, request, scale | ((u32)marker << 16));
}
bool clp_selectArrangement(u8 arrangement, ClpBoundary boundary, u16 marker)
{
    if (arrangement >= arrangementCount)
        return FALSE;
    return clp_setParts(le16(bankData + partMeta + partCount * 4 + arrangement * 2), boundary, marker);
}
bool clp_muteGroups(u16 mask)
{
    u16 previous = mutedGroups;
    mutedGroups = mask;
    if (clp_setParts(selectedParts, CLP_IMMEDIATE, 0))
        return TRUE;
    mutedGroups = previous;
    return FALSE;
}
bool clp_stop(void) { return send(3, request, 0); }
bool clp_mutePhysical(u16 mask) { return !(mask & ~0x03ff) && send(10, request, mask); }
bool clp_stopMusic(void) { return send(2, request, 0); }
bool clp_pauseMusic(void) { return send(7, request, 0); }
bool clp_resumeMusic(void) { return send(8, request, 0); }
bool clp_stopSfx(u16 token) { return send(6, request, token); }
bool clp_playPcm(u16 effect, u8 source, u8 priority, u16 token)
{
    u32 offset;
    if (source > 1)
        return FALSE;
    offset = effectOffset(effect);
    if (!offset || bankData[offset + 2] != 1)
        return FALSE;
    return send(source ? 4 : 5, token, offset | ((u32)priority << 24));
}
bool clp_playSfx(u16 effect, u8 priority, u16 token)
{
    u32 offset = effectOffset(effect);
    u8 type;
    if (!offset)
        return FALSE;
    type = bankData[offset + 2];
    if (type < 1 || type > 8)
        return FALSE;
    return send(type == 1 ? 4 : 9, token, offset | ((u32)priority << 24));
}
u16 clp_poll(ClpEvent *events, u16 capacity)
{
    u16 n = 0, copied = 0, i;
    bool taken;
    u8 r, w;
    volatile u8 raw[4][12];
    if (!events || !capacity)
        return 0;
    if (capacity > 4)
        capacity = 4;
    while (copied < capacity)
    {
        volatile u8 *destination = raw[copied];
        u16 off;
        SYS_disableInts();
        taken = Z80_getAndRequestBus(TRUE);
        r = M[4];
        w = M[3];
        if (r == w)
        {
            if (!taken)
                Z80_releaseBus();
            SYS_enableInts();
            break;
        }
        off = 192 + (r & 15) * 12;
        destination[0] = M[off];
        destination[1] = M[off + 1];
        destination[2] = M[off + 2];
        destination[3] = M[off + 3];
        destination[4] = M[off + 4];
        destination[5] = M[off + 5];
        destination[6] = M[off + 6];
        destination[7] = M[off + 7];
        destination[8] = M[off + 8];
        destination[9] = M[off + 9];
        destination[10] = M[off + 10];
        destination[11] = M[off + 11];
        M[4] = r + 1;
        if (!taken)
            Z80_releaseBus();
        SYS_enableInts();
        copied++;
    }
    /* Decode only after releasing BUSREQ. Volatile keeps LTO from moving it. */
    for (i = 0; i < copied; i++)
    {
        u16 generation = raw[i][6] | ((u16)raw[i][7] << 8);
        ClpEvent *e;
        if (generation != request)
            continue;
        e = &events[n++];
        e->type = raw[i][0];
        e->flags = raw[i][1];
        e->id = raw[i][2] | ((u16)raw[i][3] << 8);
        e->parameter = raw[i][4] | ((u16)raw[i][5] << 8);
        e->generation = generation;
        e->tick = raw[i][8] | ((u32)raw[i][9] << 8) | ((u32)raw[i][10] << 16) | ((u32)raw[i][11] << 24);
    }
    return n;
}
/* Private driver layouts stay inside Calliope, not in consumers. */
#ifndef CALLIOPE_SFX_STATE
#define CALLIOPE_SFX_STATE ((vu8 *)0xA01800)
#endif
#ifndef CALLIOPE_PCM_STATE
#define CALLIOPE_PCM_STATE ((vu8 *)0xA01EA0)
#endif
bool clp_sfxActive(u16 token)
{
    bool taken, active;
    u16 i;
    if (!bankData) return FALSE;
    SYS_disableInts();
    taken = Z80_getAndRequestBus(TRUE);
    active = M[1] != M[2];
    for (i = 0; !active && i < 4; i++)
    {
        vu8 *context = CALLIOPE_SFX_STATE + i * 16;
        if (context[0] && context[4] == (u8)token && context[5] == (u8)(token >> 8))
            active = TRUE;
    }
    if (!active && CALLIOPE_PCM_STATE[32] && CALLIOPE_PCM_STATE[12] &&
        CALLIOPE_PCM_STATE[14] == (u8)token && CALLIOPE_PCM_STATE[15] == (u8)(token >> 8))
        active = TRUE;
    if (!taken) Z80_releaseBus();
    SYS_enableInts();
    return active;
}

ClpStatus clp_status(void)
{
    ClpStatus s;
    bool taken;
    volatile u8 raw[13];
    SYS_disableInts();
    taken = Z80_getAndRequestBus(TRUE);
    raw[0] = M[0];
    raw[1] = M[14];
    raw[2] = M[5];
    raw[3] = M[26];
    raw[4] = M[1];
    raw[5] = M[2];
    raw[6] = M[8];
    raw[7] = M[9];
    raw[8] = M[20];
    raw[9] = M[21];
    raw[10] = M[22];
    raw[11] = M[24];
    raw[12] = M[25];
    if (!taken)
        Z80_releaseBus();
    SYS_enableInts();
    s.ready = raw[0];
    s.playing = raw[1];
    s.fault = raw[2];
    s.pcmUnderrun = raw[3];
    s.pending = (u8)(raw[4] - raw[5]);
    s.generation = raw[6] | ((u16)raw[7] << 8);
    s.completedGeneration = raw[8] | ((u16)raw[9] << 8);
    s.completed = raw[10] != 0;
    s.eventsLost = raw[11] | ((u16)raw[12] << 8);
    return s;
}

bool clp_setMusicVolume(u8 fm, u8 psg)
{
    if (fm > 127 || psg > 15)
        return FALSE;
    return send(12, request, (127 - fm) | ((u32)(15 - psg) << 8));
}

bool clp_setMusicPcmPriority(u8 priority)
{
    bool taken;
    if (!bankData)
        return FALSE;
    SYS_disableInts();
    taken = Z80_getAndRequestBus(TRUE);
    M[30] = priority;
    if (!taken)
        Z80_releaseBus();
    SYS_enableInts();
    return TRUE;
}


bool clp_setPartGroups(u8 part, u16 groups)
{
    u16 previous;
    if (part >= partCount)
        return FALSE;
    previous = partGroups[part];
    partGroups[part] = groups;
    if (clp_setParts(selectedParts, CLP_IMMEDIATE, 0))
        return TRUE;
    partGroups[part] = previous;
    return FALSE;
}

void clp_update(void) {}
/* The sequence also guards carry propagation and start resets. */
u32 clp_getElapsed(void)
{
    bool taken;
    volatile u8 raw[6];
    if (!bankData) return 0;
    SYS_disableInts();
    taken = Z80_getAndRequestBus(TRUE);
    raw[0] = M[33];
    raw[1] = M[10]; raw[2] = M[11]; raw[3] = M[12]; raw[4] = M[13];
    raw[5] = M[33];
    if (!taken) Z80_releaseBus();
    SYS_enableInts();
    if (!(raw[0] & 1) && raw[0] == raw[5])
        elapsedTick = raw[1] | ((u32)raw[2] << 8) | ((u32)raw[3] << 16) | ((u32)raw[4] << 24);
    return elapsedTick;
}
#endif
