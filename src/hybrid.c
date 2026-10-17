/* Experimental 68000 sequencer. BGM1 stays musical; only the RAM transport
   contains final chip writes. This file is selected by CALLIOPE_HYBRID. */
#include "calliope.h"
extern const u8 clp_driver[8192];
#ifndef CALLIOPE_MAILBOX
#define CALLIOPE_MAILBOX ((vu8 *)0xA01D00)
#endif
#define M CALLIOPE_MAILBOX
#define CLH_RECORDS 512
#define CLH_PAGES 8
#define CLH_PAGE 256
#define CLH_PCM_BASE 0x1800
#define CLH_PCM_MASK 1023
#define CLH_PCM_TARGET 896
#define CLH_LOOKAHEAD 1024
#define CLH_COPY_QUOTA 48
#if defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM
#define CLH_HEADER 12
#define CLH_PER_PAGE 81
#define CLH_RECORD_BYTES 3
#else
#define CLH_HEADER 28
#define CLH_PER_PAGE 76
#define CLH_RECORD_BYTES 3
#endif
static const u8 *bankData;
static u32 bankSize, partMeta;
static u16 request, selectedParts, mutedGroups, partGroups[16];
static u8 partCount, arrangementCount;
static u16 le16(const u8 *p) { return p[0] | ((u16)p[1] << 8); }
static u32 le24(const u8 *p) { return p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16); }
#include "effect_index.h"
static void put16(volatile u8 *p, u16 v)
{
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
}
static void put24(volatile u8 *p, u32 v)
{
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
    p[2] = (u8)(v >> 16);
}
static void put32(volatile u8 *p, u32 v)
{
    put24(p, v);
    p[3] = (u8)(v >> 24);
}
static void clear(void *v, u16 size)
{
    u8 *p = v;
    while (size--)
        *p++ = 0;
}
typedef struct
{
    u16 frequency;
    u8 slot, enabled, patch, gate, tl[4], sensitivity;
    u8 volume, noise, pan;
} ClhVoice;
typedef struct
{
    u32 pc, loop, age;
    u16 token, mask;
    u8 active, priority, type, wait;
    ClhVoice tone, noise;
} ClhSfx;
static ClhVoice voices[16];
/* Only one effect can own a physical FM channel at a time. */
static ClhVoice sfxFm[5];
static ClhSfx effects[4];
static u8 owners[9], dirty[9], physicalPatch[5], fmGain, psgGain, noiseShadow;
static u16 physicalMute, switchMask, switchMarker, songGeneration;
static u8 switchFlags, selectedVoice, voiceCount, playing, paused, completed, loopOrder;
static u16 orderCount, order;
static u16 tickRate, credit, tempoScale, tempoPending, tempoMarker;
static u8 tempoFlags;
static u32 orders, pc, songTick, ageCounter, pcmEnd, elapsedTick;
static u32 channelPc[16];
static u8 channelWait[16], channelDecode;
static u16 waitTicks, pcmToken;
static u8 pcmOwner, pcmPriority, pcmMusicPriority;
static u8 commands[16][8], commandW, commandR;
#define CLH_RENDER_PAGES ((CLH_RECORDS + CLH_PER_PAGE - 1) / CLH_PER_PAGE)
static u8 renderPages[CLH_RENDER_PAGES][256];
static u8 *recordWrite, *recordEnd;
static u8 recordPage, publishPage, publishCount;
static u16 publishBytes;
static const u8 *publishSource;
static volatile u8 *publishTarget;
static u16 recordCount, publishRecord;
static u8 header[CLH_HEADER], pendingRender, localFault, initialized;
static u8 transportW;
static u16 fragment;
static u32 timeline, played, clockRemainder, masterClock, nextTimeline, clockFraction, nextRemainder;
static u16 clockWhole;
static u16 lastClock;
static u16 sampleClock(void);
static void refreshClock(void)
{
    u16 now = sampleClock();
    played += (u16)(now - lastClock);
    lastClock = now;
}
static u8 ranks[8];
#if defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM
static u8 preparedPcm[2048];
static u32 pcmPrepared, pcmSent, renderPointer, renderRemaining, renderLoop, renderLoopLength;
static void preparePcm(u32 end)
{
    while (pcmPrepared < end)
    {
        u16 index = (u16)pcmPrepared & 2047, amount = (u16)(end - pcmPrepared);
        u16 tail = 2048 - index;
        if (pcmPrepared - pcmSent + amount > 2048)
        {
            localFault = 11;
            return;
        }
        if (amount > tail)
            amount = tail;
        if (!renderRemaining && renderLoopLength)
        {
            renderPointer = renderLoop;
            renderRemaining = renderLoopLength;
        }
        if (renderRemaining)
        {
            if (amount > renderRemaining)
                amount = (u16)renderRemaining;
            memcpy(preparedPcm + index, bankData + renderPointer, amount);
            renderPointer += amount;
            renderRemaining -= amount;
        }
        else
            memset(preparedPcm + index, 0x80, amount);
        pcmPrepared += amount;
    }
}
#endif
static u8 ymValues[2][256], ymValid[2][256], psgVolumes[4], psgValid[4];
static u16 fmFrequencies[5], psgFrequencies[3];
static u8 fmFrequencyValid[5], psgFrequencyValid[3];
typedef struct
{
    u32 deadline;
    ClpEvent event;
} ClhCue;
static ClhCue scheduled[256];
static ClpEvent delivered[16];
static u16 scheduledW, scheduledR, eventLoss;
static u8 eventW, eventR;
static ClpStatus cachedStatus;
static void loseEvent(void)
{
    if (eventLoss != 65535)
        ++eventLoss;
}
static void queueCue(u16 id, u16 parameter)
{
    ClhCue *cue;
    if ((u16)(scheduledW - scheduledR) >= 256)
    {
        loseEvent();
        return;
    }
    cue = &scheduled[scheduledW++ & 255];
    cue->deadline = timeline;
    cue->event.type = 1;
    cue->event.flags = 0;
    cue->event.id = id;
    cue->event.parameter = parameter;
    cue->event.generation = songGeneration;
    cue->event.tick = songTick;
}
static void deliverCues(void)
{
    while (scheduledR != scheduledW && scheduled[scheduledR & 255].deadline <= played)
    {
        ClpEvent *e = &scheduled[scheduledR++ & 255].event;
        if ((u8)(eventW - eventR) >= 16)
            loseEvent();
        else
            delivered[eventW++ & 15] = *e;
    }
}
static void emit(u8 type, u8 a, u8 b, u8 c)
{
    u8 *r;
    if (recordCount == CLH_RECORDS)
    {
        localFault = 1;
        return;
    }
    if (!recordCount)
    {
        recordPage = 0;
        recordWrite = renderPages[0] + CLH_HEADER;
        recordEnd = recordWrite + CLH_PER_PAGE * CLH_RECORD_BYTES;
    }
    else if (recordWrite == recordEnd)
    {
        recordWrite = renderPages[++recordPage] + CLH_HEADER;
        recordEnd = recordWrite + CLH_PER_PAGE * CLH_RECORD_BYTES;
    }
    ++recordCount;
    r = recordWrite;
    recordWrite += CLH_RECORD_BYTES;
    r[0] = type;
    r[1] = a;
    r[2] = b;
#if CLH_RECORD_BYTES == 4
    r[3] = c;
#else
    (void)c;
#endif
}
static void ym(u8 ch, u8 reg, u8 value)
{
    u8 port = ch < 3 ? 0 : 1;
    /* Frequency pairs own the YM high-byte latch; key events are never folded. */
    if (reg != 0x28 && (reg < 0xa0 || reg > 0xa6))
    {
        if (ymValid[port][reg] && ymValues[port][reg] == value)
            return;
        ymValid[port][reg] = 1;
        ymValues[port][reg] = value;
    }
    emit(port + 1, reg, value, 0);
}
static void psg(u8 value)
{
    if ((value & 0x90) == 0x90)
    {
        u8 ch = (value >> 5) & 3;
        if (psgValid[ch] && psgVolumes[ch] == value)
            return;
        psgValid[ch] = 1;
        psgVolumes[ch] = value;
    }
    emit(3, value, 0, 0);
}
static void pcmStop(u8 musicOnly)
{
    if (musicOnly && pcmOwner == 2)
        return;
    pcmOwner = 0;
    header[1] = 2;
#if defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM
    renderRemaining = renderLoopLength = 0;
#endif
}
static void keyoff(u8 slot)
{
    if (slot < 5)
        ym(0, 0x28, slot < 3 ? slot : slot + 1);
    else if (slot < 9)
        psg((u8)(0x9f + ((slot - 5) << 5)));
}
static void silence(u8 slot)
{
    u8 i;
    keyoff(slot);
    if (slot < 5)
        for (i = 0; i < 4; i++)
            ym(slot, (u8)(0x40 + i * 4 + slot % 3), 127);
}
static u8 audible(ClhVoice *v, u8 sfx)
{
    return sfx || (playing && v->enabled && !paused && !(physicalMute & (1 << v->slot)) && (v->slot == 9 || !owners[v->slot]));
}
static void levels(ClhVoice *v, u8 sfx)
{
    u8 i, ch = v->slot % 3, port = v->slot;
    for (i = 0; i < 4; i++)
    {
        u16 value = v->tl[i];
        if (!sfx && (v->sensitivity & (1 << i)))
            value += fmGain;
        ym(port, (u8)(0x40 + i * 4 + ch), (u8)(value > 127 ? 127 : value));
    }
}
static void patch(ClhVoice *v, u8 sfx, u8 force)
{
    const u8 *p;
    u8 g, i, ch = v->slot % 3;
    if (v->patch >= bankData[8])
    {
        localFault = 2;
        return;
    }
    p = bankData + le24(bankData + 16) + (u16)v->patch * 32;
    if (force || physicalPatch[v->slot] != v->patch)
    {
        /* The Z80 installs a resident patch in one bounded hardware loop. */
        u8 port = v->slot < 3 ? 0 : 1;
        emit(4, v->slot, v->patch, 0);
        for (g = 0; g < 7; g++)
            for (i = 0; i < 4; i++)
            {
                u8 reg = (u8)(0x30 + g * 16 + i * 4 + ch);
                ymValid[port][reg] = 1;
                ymValues[port][reg] = p[g * 4 + i];
            }
        ymValid[port][0xb0 + ch] = ymValid[port][0xb4 + ch] = 1;
        ymValues[port][0xb0 + ch] = p[28];
        ymValues[port][0xb4 + ch] = p[29];
        physicalPatch[v->slot] = v->patch;
    }
    levels(v, sfx);
    if (v->pan)
        ym(v->slot, (u8)(0xb4 + ch), v->pan);
}
static void frequency(ClhVoice *v)
{
    u8 ch = v->slot % 3;
    if (fmFrequencyValid[v->slot] && fmFrequencies[v->slot] == v->frequency)
        return;
    fmFrequencyValid[v->slot] = 1;
    fmFrequencies[v->slot] = v->frequency;
    ym(v->slot, (u8)(0xa4 + ch), (u8)(v->frequency >> 8));
    ym(v->slot, (u8)(0xa0 + ch), (u8)v->frequency);
}
static void tone(ClhVoice *v, u8 sfx)
{
    u8 ch = v->slot - 5;
    u16 level = v->volume;
    if (!psgFrequencyValid[ch] || psgFrequencies[ch] != v->frequency)
    {
        psg((u8)(0x80 + (ch << 5) + (v->frequency & 15)));
        psg((u8)(v->frequency >> 4));
        psgFrequencyValid[ch] = 1;
        psgFrequencies[ch] = v->frequency;
    }
    if (!sfx)
        level += psgGain;
    psg((u8)(0x90 + (ch << 5) + (level > 15 ? 15 : level)));
}
static u8 noiseAllowed(ClhVoice *v, u8 sfx)
{
    return audible(v, sfx) && (sfx || (v->noise & 3) != 3 || (!owners[7] && !(physicalMute & (1 << 7))));
}
static void noise(ClhVoice *v, u8 sfx, u8 fresh)
{
    u8 control = v->noise & 7;
    u16 level = v->volume;
    if (fresh || control != noiseShadow)
    {
        psg((u8)(0xe0 | control));
        noiseShadow = control;
    }
    if (!sfx)
        level += psgGain;
    psg((u8)(0xf0 + (level > 15 ? 15 : level)));
}
static void restore(ClhVoice *v, u8 sfx)
{
    if (!v->gate || !audible(v, sfx))
        return;
    if (v->slot < 5)
    {
        keyoff(v->slot);
        patch(v, sfx, dirty[v->slot]);
        frequency(v);
        ym(0, 0x28, (u8)((bankData[le24(bankData + 16) + (u16)v->patch * 32 + 30] << 4) | (v->slot < 3 ? v->slot : v->slot + 1)));
    }
    else if (v->slot < 8)
        tone(v, sfx);
    else if (v->slot == 8 && noiseAllowed(v, sfx))
        noise(v, sfx, 1);
    if (v->slot < 9)
        dirty[v->slot] = 0;
}
static void boundary(u8 flags, u16 marker)
{
    u8 i;
    if ((tempoFlags & flags) && (!(tempoFlags & CLP_NEXT_MARKER) || tempoMarker == marker))
    {
        tempoScale = tempoPending;
        tempoFlags = 0;
    }
    if (!(switchFlags & flags) || ((switchFlags & 16) && switchMarker != marker))
        return;
    for (i = 0; i < voiceCount; i++)
    {
        ClhVoice *v = &voices[i];
        u8 enabled = (switchMask & (1 << i)) != 0;
        if (v->enabled == enabled)
            continue;
        v->enabled = enabled;
        if (v->slot < 9)
        {
            dirty[v->slot] = 1;
            if (!owners[v->slot])
                keyoff(v->slot);
        }
        else
            pcmStop(1);
    }
    switchFlags = 0;
}
static void stopMusic(void)
{
    u8 i, wasPlaying = playing;
    playing = 0;
    paused = 0;
    switchFlags = tempoFlags = 0;
    for (i = 0; i < 9; i++)
    {
        dirty[i] = 1;
        if (wasPlaying && !owners[i])
            silence(i);
    }
    pcmStop(1);
}
static void releaseEffect(u8 index)
{
    u8 i;
    ClhSfx *e = &effects[index];
    if (!e->active)
        return;
    for (i = 0; i < 9; i++)
        if (owners[i] == index + 1)
        {
            keyoff(i);
            owners[i] = 0;
            dirty[i] = 1;
        }
    e->active = 0;
}
static void loadOrder(void)
{
    u8 i;
    u32 record;
    if (orders + (u32)order * 3 + 3 > bankSize)
    {
        localFault = 3;
        return;
    }
    record = le24(bankData + orders + (u32)order * 3);
    if (record + (u32)(voiceCount + 1) * 3 > bankSize)
    {
        localFault = 3;
        return;
    }
    pc = le24(bankData + record);
    waitTicks = 0;
    for (i = 0; i < voiceCount; i++)
    {
        channelPc[i] = le24(bankData + record + 3 + i * 3);
        channelWait[i] = 0;
    }
}
static void startMusic(u32 descriptor, u16 generation)
{
    const u8 *d = bankData + descriptor;
    u8 i;
    u32 meta = le24(d + 8);
    stopMusic();
    voiceCount = d[11] & 31;
    songGeneration = generation;
    songTick = 0;
    credit = 0;
    tempoScale = CLP_TEMPO_NORMAL;
    tempoFlags = 0;
    waitTicks = 0;
    order = 0;
    if (!voiceCount || voiceCount > 16 || meta + (u32)voiceCount * 4 > bankSize)
    {
        localFault = 5;
        return;
    }
    orderCount = d[2] + 1;
    loopOrder = d[3];
    orders = le24(d + 4);
    loadOrder();
    tickRate = le16(d + 14);
    selectedVoice = 255;
    completed = 0;
    playing = 1;
    for (i = 0; i < 8; i++)
        ranks[i] = d[16 + i];
    for (i = 0; i < voiceCount; i++)
    {
        ClhVoice *v = &voices[i];
        clear(v, sizeof(*v));
        v->slot = bankData[meta + i * 4];
        v->enabled = bankData[meta + i * 4 + 3];
        v->patch = 255;
        v->volume = 15;
    }
}
static void startPcm(u32 descriptor, u8 source, u8 priority, u16 token)
{
    const u8 *p = bankData + descriptor;
    u32 address, length, loopStart, loopEnd;
    if (!source && (paused || (physicalMute & (1 << 9))))
        return;
    if (pcmOwner && timeline >= pcmEnd)
        pcmOwner = 0;
    if (!source)
        priority = pcmMusicPriority;
    if (pcmOwner == 2 && priority <= pcmPriority)
        return;
    if (source && pcmOwner == 1 && priority < pcmMusicPriority)
        return;
    address = (u32)bankData + le24(p + 4);
    length = le24(p + 7);
    loopStart = le24(p + 10);
    loopEnd = le24(p + 13);
#if !(defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM)
    header[1] = 1;
    put24(header + 12, address);
    put24(header + 15, loopEnd ? loopEnd : length);
    put24(header + 18, loopEnd ? address + loopStart : 0);
    put24(header + 21, loopEnd ? loopEnd - loopStart : 0);
#else
    (void)address;
#endif
    pcmOwner = source ? 2 : 1;
    pcmPriority = priority;
    pcmToken = token;
    pcmEnd = loopEnd ? 0xffffffffUL : timeline + length;
#if defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM
    renderPointer = le24(p + 4);
    renderRemaining = loopEnd ? loopEnd : length;
    renderLoop = renderPointer + loopStart;
    renderLoopLength = loopEnd ? loopEnd - loopStart : 0;
#endif
}
static void startEffect(u32 descriptor, u8 priority, u16 token)
{
    const u8 *d = bankData + descriptor;
    u8 type = d[2], i, j, chosen = 255, victim = 255, freeIndex = 255;
    u16 eligible = type == 8 ? (d[7] & 31) : type == 6 ? ((u16)(d[7] & 15) << 5)
                                         : type == 5   ? ((1 << 7) | (1 << 8))
                                         : type == 4   ? (1 << 8)
                                                       : d[7];
    u16 mask = 0;
    u8 bestClass = 255, bestPriority = 255, bestRank = 255;
    u32 oldest = 0;
    if (type == 1)
    {
        startPcm(descriptor, 1, priority, token);
        return;
    }
    for (i = 0; i < 4; i++)
        if (!effects[i].active)
        {
            freeIndex = i;
            break;
        }
    if (type == 7)
    {
        u8 needed = d[7], pass;
        if (needed < 2 || needed > 3)
            return;
        for (pass = 0; pass < 2 && needed; pass++)
            for (i = 5; i < 8 && needed; i++)
            {
                u8 owner = owners[i];
                if ((!pass && owner) || (pass && (!owner || effects[owner - 1].priority >= priority)))
                    continue;
                mask |= 1 << i;
                needed--;
            }
        if (needed)
            return;
        chosen = 5;
    }
    else if (type >= 5)
    {
        u8 valid = 1;
        for (i = 0; i < 9; i++)
            if ((eligible & (1 << i)) && owners[i] && effects[owners[i] - 1].priority >= priority)
                valid = 0;
        if (valid && eligible)
        {
            mask = eligible;
            chosen = type == 8 ? 0 : 5;
        }
    }
    else
        for (i = 0; i < 9; i++)
            if (eligible & (1 << i))
            {
                u8 owner = owners[i], cls, prio = 0, rank = i < 8 ? ranks[i] : 0;
                u32 age = 0;
                if (owner)
                {
                    ClhSfx *e = &effects[owner - 1];
                    if (e->priority >= priority)
                        continue;
                    cls = 2;
                    prio = e->priority;
                    age = e->age;
                }
                else
                {
                    cls = 0;
                    for (j = 0; j < voiceCount; j++)
                        if (playing && !paused && voices[j].slot == i && voices[j].enabled && !(physicalMute & (1 << i)))
                            cls = 1;
                }
                if (cls < bestClass || (cls == bestClass && (prio < bestPriority || (prio == bestPriority && (cls == 2 ? age < oldest : rank < bestRank)))))
                {
                    chosen = i;
                    bestClass = cls;
                    bestPriority = prio;
                    oldest = age;
                    bestRank = rank;
                }
            }
    if (chosen == 255)
        return;
    if (!mask)
        mask = 1 << chosen;
    /* Preempting any participating channel releases the entire instance. */
    for (i = 0; i < 9; i++)
        if ((mask & (1 << i)) && owners[i])
        {
            victim = owners[i] - 1;
            releaseEffect(victim);
            if (freeIndex == 255)
                freeIndex = victim;
        }
    if (freeIndex == 255)
        return;
    {
        ClhSfx *e = &effects[freeIndex];
        clear(e, sizeof(*e));
        e->active = 1;
        e->pc = le24(d + 4);
        e->loop = le24(d + 8);
        e->priority = priority;
        e->token = token;
        e->mask = mask;
        e->type = type;
        e->age = ++ageCounter;
        e->tone.slot = chosen;
        e->tone.enabled = 1;
        e->tone.patch = 255;
        e->tone.volume = 15;
        e->noise.slot = 8;
        e->noise.enabled = 1;
        e->noise.volume = 15;
        for (i = 0; i < 9; i++)
            if (mask & (1 << i))
            {
                keyoff(i);
                owners[i] = freeIndex + 1;
                dirty[i] = 1;
                if (type == 8 && i < 5)
                {
                    ClhVoice *v = &sfxFm[i];
                    clear(v, sizeof(*v));
                    v->slot = i;
                    v->enabled = 1;
                    v->patch = 255;
                }
            }
    }
}
/* Fixed operand widths; PSG packets validate their variable tail below. */
static const u8 operandBytes[0x3a] = {
    1,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    2,
    3,
    0,
    1,
    1,
    3,
    2,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    4,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    1,
    3,
    0,
    2,
    2,
    1,
    3,
    6,
    1,
    1,
};
static void decode(u8 context)
{
    ClhSfx *e = context ? &effects[context - 1] : 0;
    u32 *stream = context ? &e->pc : &pc;
    u16 budget = 1024;
    const u8 *cursor, *end = bankData + bankSize;
    if (*stream >= bankSize)
    {
        localFault = 3;
        return;
    }
    cursor = bankData + *stream;
    while (budget && !localFault)
    {
        --budget;
        u8 op, n;
        const u8 *a;
        ClhVoice *v;
        if (cursor >= end)
        {
            localFault = 3;
            goto done;
        }
        op = *cursor++;
        n = op >= 0x40 && op < 0x50 ? 1 : (op < sizeof(operandBytes) ? operandBytes[op] : 0);
        if (!context && channelDecode && (op == 0x10 || op == 0x11 || op == 0x13 || op == 0x16 || op == 0x36 || op == 0x37))
            --n;
        if ((u32)(end - cursor) < n)
        {
            localFault = 3;
            goto done;
        }
        a = cursor;
        cursor += n;
        v = context ? &e->tone : (selectedVoice < voiceCount ? &voices[selectedVoice] : 0);
        if (!context && op >= 0x80 && op < 0xfd)
        {
            waitTicks = op - 0x7f;
            goto done;
        }
        if (op == 0)
        {
            u8 delay = a[0];
            if (!delay)
            {
                localFault = 4;
                goto done;
            }
            if (context)
                e->wait = delay;
            else
                waitTicks = delay;
            goto done;
        }
        if (op == 0x15)
        {
            u8 f = a[0];
            u16 m = le16(a + 1);
            if (!context)
                boundary(f, m);
            continue;
        }
        if (op == 0x20)
        {
            u16 id = le16(a), parameter = le16(a + 2);
            queueCue(id, parameter);
            continue;
        }
        if (op == 0x34)
        {
            tickRate = le16(a);
            credit = 0;
            continue;
        }
        if (op == 0xfd)
        {
            if (context || channelDecode)
            {
                localFault = 6;
                goto done;
            }
            if (++order == orderCount)
            {
                if (loopOrder == 255)
                {
                    completed = 1;
                    stopMusic();
                    goto done;
                }
                order = loopOrder;
            }
            loadOrder();
            if (localFault || pc >= bankSize)
            {
                localFault = 3;
                goto done;
            }
            cursor = bankData + pc;
            continue;
        }
        if (op == 255)
        {
            if (!context && channelDecode)
            {
                localFault = 6;
                goto done;
            }
            if (context)
            {
                if (e->loop)
                {
                    if (e->loop >= bankSize)
                    {
                        localFault = 3;
                        goto done;
                    }
                    cursor = bankData + e->loop;
                    continue;
                }
                releaseEffect(context - 1);
            }
            else
            {
                completed = 1;
                stopMusic();
            }
            goto done;
        }
        if (context && e->type == 8)
        {
            if ((op != 0x10 && op != 0x11 && op != 0x13 && op != 0x36 && op != 0x37 && op != 0x16) || a[0] >= 5 || !(e->mask & (1 << a[0])))
            {
                localFault = 12;
                goto done;
            }
            v = &sfxFm[a[0]];
        }
        if (op >= 0x40 && op < 0x50)
        {
            if (context || channelDecode || (op & 15) >= voiceCount)
            {
                localFault = 5;
                goto done;
            }
            v = &voices[op & 15];
        }
        if (!v)
        {
            localFault = 7;
            goto done;
        }
        if (op == 0x10 || (op >= 0x40 && op < 0x50))
        {
            const u8 *p;
            u8 i;
            v->patch = op == 0x10 && context ? a[1] : a[0];
            if (v->patch >= bankData[8])
            {
                localFault = 2;
                goto done;
            }
            p = bankData + le24(bankData + 16) + (u16)v->patch * 32;
            for (i = 0; i < 4; i++)
                v->tl[i] = p[4 + i];
            v->sensitivity = p[31];
            if (audible(v, context) && !dirty[v->slot])
                patch(v, context, 0);
        }
        else if (op == 0x11 || op == 0x36)
        {
            v->frequency = le16(a + (context ? 1 : 0));
            if (op == 0x11)
                v->gate = 1;
            if (audible(v, context))
            {
                if (op == 0x11)
                    restore(v, context);
                else if (v->gate && !dirty[v->slot])
                    frequency(v);
            }
        }
        else if (op == 0x13)
        {
            v->gate = 0;
            if (audible(v, context))
                keyoff(v->slot);
        }
        else if (op == 0x37)
        {
            u8 i;
            for (i = 0; i < 4; i++)
                v->tl[i] = a[i + (context ? 1 : 0)];
            v->sensitivity = a[context ? 5 : 4];
            if (audible(v, context) && !dirty[v->slot])
                levels(v, context);
        }
        else if (op == 0x16)
        {
            if (v->slot >= 5)
            {
                localFault = 5;
                goto done;
            }
            v->pan = a[context ? 1 : 0];
            if (audible(v, context) && !dirty[v->slot])
                ym(v->slot, (u8)(0xb4 + v->slot % 3), v->pan);
        }
        else if (op == 0x30 || op == 0x35)
        {
            u8 count = a[0], latch = 0, i, hasPitch = 0;
            const u8 *values = cursor;
            if (count > 16)
            {
                localFault = 8;
                goto done;
            }
            if ((u32)(end - cursor) < count)
            {
                localFault = 3;
                goto done;
            }
            cursor += count;
            if (context && e->type >= 4)
            {
                /* Fixed-resource PSG streams already contain physical latches. */
                for (i = 0; i < count; i++)
                {
                    u8 value = values[i];
                    if (e->type == 7 && (value & 128))
                    {
                        u8 ordinal = (value >> 5) & 3, physical;
                        for (physical = 5; physical < 8; physical++)
                            if (e->mask & (1 << physical))
                            {
                                if (!ordinal)
                                    break;
                                ordinal--;
                            }
                        value = (value & 0x9f) | ((physical - 5) << 5);
                    }
                    if ((value & 0xf8) == 0xe0)
                        noiseShadow = value;
                    psg(value);
                }
                continue;
            }
            /* Every packet is explicit-latch. SFX channel IDs are remapped. */
            for (i = 0; i < count; i++)
            {
                u8 value = values[i];
                if (value & 128)
                {
                    latch = value;
                    if (value & 16)
                    {
                        v->volume = value & 15;
                        v->gate = v->volume < 15;
                    }
                    else
                    {
                        v->frequency = (v->frequency & 0x3f0) | (value & 15);
                        hasPitch = 1;
                    }
                }
                else if (!(latch & 16))
                    v->frequency = (v->frequency & 15) | ((u16)(value & 63) << 4);
            }
            if (audible(v, context) && (op == 0x30 || !dirty[v->slot]))
            {
                if (v->slot < 8)
                {
                    if (hasPitch)
                    {
                        tone(v, context);
                        dirty[v->slot] = 0;
                    }
                    else if (!dirty[v->slot] || !v->gate)
                    {
                        u16 level = v->volume + (context ? 0 : psgGain);
                        psg((u8)(0x90 + ((v->slot - 5) << 5) + (level > 15 ? 15 : level)));
                    }
                }
            }
        }
        else if (op == 0x39)
        {
            ym(0, 0x22, a[0]);
        }
        else if (op == 0x33)
        {
            u8 control = a[0], vol = a[1];
            if (context)
                v = &e->noise;
            v->noise = control;
            v->volume = vol;
            v->gate = vol < 15;
            if (noiseAllowed(v, context) && (context || !dirty[8] || (control & 8)))
            {
                noise(v, context, (control & 8) != 0);
                dirty[8] = 0;
            }
        }
        else if (op == 0x31)
        {
            u32 descriptor = le24(a);
            if (audible(v, context))
                startPcm(descriptor, 0, 0, 0);
        }
        else if (op == 0x32)
        {
            if (audible(v, context))
                pcmStop(1);
        }
        else
        {
            localFault = 9;
            goto done;
        }
    }
    if (!budget)
        localFault = 10;
done:
    *stream = (u32)(cursor - bankData);
}
static void controls(void)
{
    while (commandR != commandW)
    {
        const u8 *c = commands[commandR++ & 15];
        u8 op = c[0], i;
        u16 gen = le16(c + 2), arg = le16(c + 4);
        u32 value = le24(c + 4);
        if ((op == 2 || (op >= 7 && op != 9)) && gen != songGeneration)
            continue;
        if (op == 1)
            startMusic(value, gen);
        else if (op == 2)
            stopMusic();
        else if (op == 3)
        {
            stopMusic();
            for (i = 0; i < 4; i++)
                releaseEffect(i);
            pcmStop(0);
        }
        else if (op == 4 || op == 5)
            startPcm(value, op == 4, c[7], gen);
        else if (op == 6)
        {
            for (i = 0; i < 4; i++)
                if (effects[i].token == arg)
                    releaseEffect(i);
            if (pcmOwner == 2 && pcmToken == arg)
                pcmStop(0);
        }
        else if (op == 7)
        {
            if (!paused)
            {
                paused = 1;
                for (i = 0; i < 9; i++)
                {
                    dirty[i] = 1;
                    if (!owners[i])
                        silence(i);
                }
                pcmStop(1);
            }
        }
        else if (op == 8)
            paused = 0;
        else if (op == 9)
            startEffect(value, c[7], gen);
        else if (op == 10)
        {
            u16 changed = physicalMute ^ arg;
            physicalMute = arg;
            for (i = 0; i < 9; i++)
                if (changed & (1 << i))
                {
                    dirty[i] = 1;
                    if (!owners[i])
                        keyoff(i);
                }
            if (arg & (1 << 9))
                pcmStop(1);
        }
        else if (op == 11)
        {
            switchMask = arg;
            switchFlags = c[1];
            switchMarker = le16(c + 6);
            boundary(CLP_IMMEDIATE, 0);
        }
        else if (op == 13)
        {
            tempoPending = arg;
            tempoFlags = c[1];
            tempoMarker = le16(c + 6);
            boundary(CLP_IMMEDIATE, 0);
        }
        else if (op == 12)
        {
            fmGain = c[4];
            psgGain = c[5];
            for (i = 0; i < voiceCount; i++)
            {
                ClhVoice *v = &voices[i];
                if (!audible(v, 0) || v->slot >= 9 || dirty[v->slot])
                    continue;
                if (v->slot < 5)
                    levels(v, 0);
                else if (v->slot < 8)
                {
                    u16 level = v->volume + psgGain;
                    psg((u8)(0x90 + ((v->slot - 5) << 5) + (level > 15 ? 15 : level)));
                }
                else
                    noise(v, 0, 0);
            }
        }
    }
}
static void render(void)
{
    u8 i;
    clear(header, sizeof(header));
    recordCount = publishRecord = 0;
    publishPage = 0;
    nextRemainder = clockRemainder + clockFraction;
    nextTimeline = timeline + clockWhole;
    if (nextRemainder >= 241920UL)
    {
        nextRemainder -= 241920UL;
        ++nextTimeline;
    }
    controls();
    if (playing && !paused)
    {
        credit += (u16)(((u32)tickRate * tempoScale + 128) >> 8);
        while (credit >= 300 && playing && !localFault)
        {
            credit -= 300;
            songTick++;
            if (waitTicks)
                --waitTicks;
            if (!waitTicks)
            {
                selectedVoice = 255;
                channelDecode = 0;
                decode(0);
            }
            if (playing && !localFault)
            {
                u8 pass;
                u32 conductorPc = pc;
                u16 conductorWait = waitTicks;
                for (i = 0; i < voiceCount; i++)
                    if (channelWait[i])
                        --channelWait[i];
                for (pass = 0; pass < 2; pass++)
                    for (i = 0; i < voiceCount && !localFault; i++)
                        if (!channelWait[i] && ((voices[i].slot == 9) == (pass == 0)))
                        {
                            pc = channelPc[i];
                            waitTicks = 0;
                            selectedVoice = i;
                            channelDecode = 1;
                            decode(0);
                            channelPc[i] = pc;
                            channelWait[i] = (u8)waitTicks;
                        }
                pc = conductorPc;
                waitTicks = conductorWait;
                selectedVoice = 255;
                channelDecode = 0;
            }
        }
    }
    for (i = 0; i < 4; i++)
        if (effects[i].active)
        {
            if (effects[i].wait)
                --effects[i].wait;
            if (!effects[i].wait)
                decode(i + 1);
        }
    put16(header + 2, (u16)timeline);
    put16(header + 4, songGeneration);
    put32(header + 6, songTick);
    header[10] = playing;
    header[11] = completed;
#if !(defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM)
    put16(header + 24, (u16)nextTimeline);
#endif
#if defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM
    preparePcm(nextTimeline);
#endif
    pendingRender = 1;
}
/* Keep index arithmetic out of BUSREQ even under SGDK's LTO. */
#ifdef CALLIOPE_HOST_TEST
#define CLH_NOINLINE __declspec(noinline)
#else
#define CLH_NOINLINE __attribute__((noinline, noclone))
#endif
#ifndef CALLIOPE_HOST_TEST
extern void clh_write1(volatile u8 *target, const volatile u8 *staged);
extern u16 clh_read2(const volatile u8 *source);
extern void clh_statusa(volatile u8 *raw);
extern void clh_statusb(volatile u8 *raw);
extern void clh_statusc(volatile u8 *raw);
extern void clh_write8(volatile u8 *target, const volatile u8 *staged);
extern void clh_write_pcm32(volatile u8 *target, const u8 *source, u32 publication);
#endif
static CLH_NOINLINE void copy8(volatile u8 *target, const volatile u8 *staged)
{
#ifndef CALLIOPE_HOST_TEST
    clh_write8(target, staged);
#else
    bool taken;
    SYS_disableInts();
    taken = Z80_getAndRequestBus(TRUE);
    {
        u8 i;
        for (i = 0; i < 8; i++)
            target[i] = staged[i];
    }
    if (!taken)
        Z80_releaseBus();
    SYS_enableInts();
#endif
}
static CLH_NOINLINE void copy1(volatile u8 *target, u8 value)
{
#ifndef CALLIOPE_HOST_TEST
    const volatile u8 staged = value;
    clh_write1(target, &staged);
#else
    bool taken;
    SYS_disableInts();
    taken = Z80_getAndRequestBus(TRUE);
    *target = value;
    if (!taken)
        Z80_releaseBus();
    SYS_enableInts();
#endif
}
#if defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM
static CLH_NOINLINE void copyPcm(volatile u8 *target, const u8 *source, u32 publication)
{
#ifndef CALLIOPE_HOST_TEST
    clh_write_pcm32(target, source, publication);
#else
    bool taken;
    u8 i;
    SYS_disableInts();
    taken = Z80_getAndRequestBus(TRUE);
    for (i = 0; i < 32; i++)
        target[i] = source[i];
    M[0x187] = (u8)publication;
    M[0x188] = (u8)(publication >> 8);
    M[0x18b] = (u8)(publication >> 16);
    if (!taken)
        Z80_releaseBus();
    SYS_enableInts();
#endif
}
static void uploadPcm(void)
{
    for (;;)
    {
        u32 limit = played + CLH_PCM_TARGET, publication;
        u16 index = (u16)pcmSent & 2047, out = (u16)pcmSent & CLH_PCM_MASK;
        u16 amount, tail;
        u8 blocks;
        const u8 *source;
        volatile u8 *target;
        if (limit > pcmPrepared)
            limit = pcmPrepared;
        if (limit < pcmSent + 32)
            return;
        amount = (u16)(limit - pcmSent);
        if (amount > 128)
            amount = 128;
        tail = 2048 - index;
        if (amount > tail)
            amount = tail;
        tail = CLH_PCM_MASK + 1 - out;
        if (amount > tail)
            amount = tail;
        blocks = (u8)(amount >> 5);
        amount = (u16)blocks << 5;
        source = preparedPcm + index;
        target = M + (CLH_PCM_BASE - 0x1d00) + out;
        publication = (CLH_PCM_BASE + out + 32) | ((u32)(u8)((pcmSent + 32) >> 3) << 16);
        do
        {
            /* No ring wraps inside a planned block. Holds remain eight bytes. */
            if ((publication & 65535) == CLH_PCM_BASE + CLH_PCM_MASK + 1)
                publication -= CLH_PCM_MASK + 1;
            copyPcm(target, source, publication);
            target += 32;
            source += 32;
            publication += 0x00040020UL;
        } while (--blocks);
        pcmSent += amount;
        if (pcmSent + 32 > limit)
            return;
        if (amount == 128)
            refreshClock();
    }
}

#endif
static CLH_NOINLINE u16 consumers(void)
{
#ifndef CALLIOPE_HOST_TEST
    return clh_read2(M + 0x181);
#else
    volatile u8 raw[2];
    bool taken;
    SYS_disableInts();
    taken = Z80_getAndRequestBus(TRUE);
    raw[0] = M[0x181];
    raw[1] = M[0x182];
    if (!taken)
        Z80_releaseBus();
    SYS_enableInts();
    return raw[0] | ((u16)raw[1] << 8);
#endif
}
static u8 publish(void)
{
    if (!fragment)
    {
        u16 positions = consumers();
        u8 count, *page = renderPages[publishPage];
        if ((u8)(transportW - (u8)positions) >= 8 || (u8)(transportW - (u8)(positions >> 8)) >= 8)
            return 0;
        count = (u8)(recordCount - publishRecord > CLH_PER_PAGE ? CLH_PER_PAGE : recordCount - publishRecord);
        memcpy(page, header, CLH_HEADER);
        page[0] = count;
#if !(defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM)
        page[26] = (publishRecord + count == recordCount);
#endif
        publishCount = count;
        publishBytes = CLH_HEADER + (u16)count * CLH_RECORD_BYTES;
        publishSource = page;
        publishTarget = M - 0xd00 + ((u16)(transportW & 7) << 8);
    }
    /* Transfer cursors are planned once per page, outside every bus hold. */
    copy8(publishTarget, publishSource);
    publishTarget += 8;
    publishSource += 8;
    fragment += 8;
    if (fragment < publishBytes)
        return 1;
    copy1(M + 0x180, ++transportW);
    fragment = 0;
    ++publishPage;
    publishRecord += publishCount;
    header[1] = 0;
    if (publishRecord == recordCount)
    {
        pendingRender = 0;
        clockRemainder = nextRemainder;
        timeline = nextTimeline;
    }
    return 1;
}
static bool sendAt(u8 op, u8 flags, u16 generation, u32 argument)
{
    u8 *c;
    if ((u8)(commandW - commandR) == 16)
        return FALSE;
    c = commands[commandW++ & 15];
    c[0] = op;
    c[1] = flags;
    put16(c + 2, generation);
    put32(c + 4, argument);
    return TRUE;
}
static bool send(u8 op, u16 generation, u32 argument) { return sendAt(op, 0, generation, argument); }
static CLH_NOINLINE u16 sampleClock(void)
{
    u8 tries;
#ifdef CALLIOPE_HOST_TEST
    volatile u8 raw[2];
    bool taken;
#endif
    for (tries = 0; tries < 4; tries++)
    {
        u16 clock;
#ifdef CALLIOPE_HOST_TEST
        SYS_disableInts();
        taken = Z80_getAndRequestBus(TRUE);
        raw[0] = M[34];
        raw[1] = M[35];
        if (!taken)
            Z80_releaseBus();
        SYS_enableInts();
        clock = raw[0] | ((u16)raw[1] << 8);
#else
        clock = clh_read2(M + 34);
#endif
        /* The Z80 stores low first. A halt between the two stores at wrap
           yields an old high/new low word: a large backward delta, never a
           falsely advanced clock. Normal service must run within 1024 samples. */
        if ((u16)(clock - lastClock) <= 1024)
            return clock;
#ifndef CALLIOPE_HOST_TEST
        __asm__ volatile(".rept 96\n nop\n .endr" ::: "memory");
#endif
    }
    return lastClock;
}
void clp_update(void)
{
    u16 budget = CLH_COPY_QUOTA;
    u8 batch = 0;
    u32 goal;
    if (!initialized)
        return;
    refreshClock();
    goal = played + CLH_LOOKAHEAD;
    deliverCues();
#if defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM
    uploadPcm();
#endif
    while (budget-- && (pendingRender || timeline < goal))
    {
        if (!pendingRender)
            render();
        if (!publish())
            break;
        if (!(++batch & 7))
        {
            refreshClock();
#if defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM
            uploadPcm();
#endif
        }
    }
#if defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM
    refreshClock();
    uploadPcm();
#endif
}
bool clp_init(const u8 *bank, u32 length)
{
    u32 address = (u32)bank;
    u16 i, frames;
    if ((address & 32767) || length < 32 || address + length > 0x400000 || bank[0] != 'B' || bank[1] != 'G' || bank[2] != 'M' || bank[3] != '1' || bank[4] != 12 || bank[5] || le16(bank + 19) != 1020 || bank[21] != (SYS_isPAL() ? 1 : 0))
        return FALSE;
    if (!effectIndexValid(bank, length))
        return FALSE;
    bankData = bank;
    bankSize = length;
    partCount = arrangementCount = 0;
    request = 0;
    tempoScale = CLP_TEMPO_NORMAL;
    tempoFlags = 0;
    partMeta = 0;
    mutedGroups = selectedParts = 0;
    clear(voices, sizeof(voices));
    clear(effects, sizeof(effects));
    clear(owners, sizeof(owners));
    clear(ymValid, sizeof(ymValid));
    clear(psgValid, sizeof(psgValid));
    clear(fmFrequencyValid, sizeof(fmFrequencyValid));
    clear(psgFrequencyValid, sizeof(psgFrequencyValid));
    scheduledW = scheduledR = eventLoss = 0;
    eventW = eventR = 0;
    clear(&cachedStatus, sizeof(cachedStatus));
    for (i = 0; i < 9; i++)
        dirty[i] = 1;
    for (i = 0; i < 5; i++)
        physicalPatch[i] = 255;
    initialized = 1;
    timeline = played = clockRemainder = elapsedTick = 0;
    lastClock = 0;
    playing = paused = completed = pcmOwner = pcmMusicPriority = 0;
    fmGain = psgGain = 0;
    physicalMute = 0;
    commandW = commandR = pendingRender = localFault = 0;
    noiseShadow = 255;
    voiceCount = 0;
    songTick = 0;
    songGeneration = 0;
    masterClock = SYS_isPAL() ? 53203424UL : 53693175UL;
    clockWhole = (u16)(masterClock / 241920UL);
    clockFraction = masterClock % 241920UL;
    transportW = 0;
    fragment = 0;
#if defined(CALLIOPE_HOST_PCM) && CALLIOPE_HOST_PCM
    pcmPrepared = pcmSent = renderPointer = renderRemaining = renderLoop = renderLoopLength = 0;
#endif
    Z80_loadCustomDriver(clp_driver, 8192);
    /* Init is still waiting for its handshake, so no DAC deadline exists. */
    if (bank[8] > 16 || le24(bank + 16) + (u32)bank[8] * 32 > length)
    {
        initialized = 0;
        return FALSE;
    }
    SYS_disableInts();
    Z80_requestBus(TRUE);
    for (i = 0; i < (u16)bank[8] * 32; i++)
        (M - 0x1500)[i] = bank[le24(bank + 16) + i];
    Z80_releaseBus();
    SYS_enableInts();
    /* Publish silence before letting init advance past its pointer handshake. */
    clp_update();
    SYS_disableInts();
    Z80_requestBus(TRUE);
    M[16] = 1;
    M[7] = SYS_isPAL() ? 1 : 0;
    Z80_releaseBus();
    SYS_enableInts();
    Z80_useBusProtection(0x1D06);
    Z80_setForceDelayDMA(TRUE);
    for (frames = 0; frames < 120; frames++)
    {
        clp_update();
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
bool clp_setMusicVolume(u8 fm, u8 psg)
{
    if (fm > 127 || psg > 15)
        return FALSE;
    return send(12, request, (127 - fm) | ((u32)(15 - psg) << 8));
}

bool clp_setMusicPcmPriority(u8 priority)
{
    if (!initialized)
        return FALSE;
    pcmMusicPriority = priority;
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

u16 clp_poll(ClpEvent *events, u16 capacity)
{
    u16 n = 0, copied = 0;
    if (!events)
        return 0;
    if (capacity > 4)
        capacity = 4;
    deliverCues();
    while (copied++ < capacity && eventR != eventW)
    {
        ClpEvent *e = &delivered[eventR++ & 15];
        if (e->generation == request)
            events[n++] = *e;
    }
    return n;
}
static CLH_NOINLINE u8 statusA(volatile u8 *raw)
{
#ifndef CALLIOPE_HOST_TEST
    clh_statusa(raw);
    return raw[0];
#else
    bool taken;
    SYS_disableInts();
    taken = Z80_getAndRequestBus(TRUE);
    raw[0] = M[33];
    raw[2] = M[0];
    raw[3] = M[5];
    raw[7] = M[26];
    if (!taken)
        Z80_releaseBus();
    SYS_enableInts();
    return raw[0];
#endif
}
static CLH_NOINLINE void statusB(volatile u8 *raw)
{
#ifndef CALLIOPE_HOST_TEST
    clh_statusb(raw);
#else
    bool taken;
    SYS_disableInts();
    taken = Z80_getAndRequestBus(TRUE);
    raw[8] = M[14];
    raw[9] = M[8];
    raw[10] = M[9];
    raw[11] = M[22];
    if (!taken)
        Z80_releaseBus();
    SYS_enableInts();
#endif
}
static CLH_NOINLINE void statusC(volatile u8 *raw)
{
#ifndef CALLIOPE_HOST_TEST
    clh_statusc(raw);
#else
    bool taken;
    SYS_disableInts();
    taken = Z80_getAndRequestBus(TRUE);
    raw[12] = M[20];
    raw[13] = M[21];
    raw[1] = M[33];
    if (!taken)
        Z80_releaseBus();
    SYS_enableInts();
#endif
}
ClpStatus clp_status(void)
{
    volatile u8 raw[14];
    ClpStatus *s = &cachedStatus;
    if (!(statusA(raw) & 1))
    {
        statusB(raw);
        statusC(raw);
        if (raw[0] == raw[1])
        {
            s->ready = raw[2];
            s->fault = raw[3];
            s->pcmUnderrun = raw[7];
            s->playing = raw[8];
            s->generation = raw[9] | ((u16)raw[10] << 8);
            s->completed = raw[11] != 0;
            s->completedGeneration = raw[12] | ((u16)raw[13] << 8);
        }
    }
    if (localFault)
        s->fault = localFault;
    s->eventsLost = eventLoss;
    s->pending = (u8)(commandW - commandR);
    return *s;
}

/* The Z80 publishes consumed page headers under the status sequence. */
u32 clp_getElapsed(void)
{
    bool taken;
    volatile u8 raw[6];
    if (!initialized) return 0;
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
