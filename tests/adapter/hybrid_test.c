#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "genesis.h"
static vu8 zram[8192];
#define CALLIOPE_MAILBOX (zram+0x1d00)
#define CALLIOPE_HYBRID 1
#ifndef CALLIOPE_HOST_PCM
#define CALLIOPE_HOST_PCM 1
#endif
#define CALLIOPE_HOST_TEST 1
#include "../../src/calliope.c"
const u8 clp_driver[8192]={0};
static bool owned;static int disabled,releases;
void SYS_disableInts(void) { ++disabled; }
void SYS_enableInts(void) { assert(disabled);--disabled; }
bool Z80_getAndRequestBus(bool wait) { bool previous=owned;assert(wait && disabled);owned=true;return previous; }
void Z80_releaseBus(void) { assert(owned);owned=false;++releases; }
void Z80_requestBus(bool wait) { assert(wait);owned=true; }
void Z80_loadCustomDriver(const u8 *p,u16 n) { (void)p;(void)n; }
void Z80_useBusProtection(u16 p) { (void)p; }
void Z80_setForceDelayDMA(bool b) { (void)b; }
bool SYS_isPAL(void) { return false; }
void SYS_doVBlankProcess(void) {}
static u8 data[200000];
static void reset(void)
{
    u8 i;
    bankData=data;bankSize=sizeof(data);request=1;
    clear(voices,sizeof(voices));clear(effects,sizeof(effects));clear(owners,sizeof(owners));clear(ymValid,sizeof(ymValid));clear(psgValid,sizeof(psgValid));
    clear(sfxFm,sizeof(sfxFm));
    clear(fmFrequencyValid,sizeof(fmFrequencyValid));clear(psgFrequencyValid,sizeof(psgFrequencyValid));clear((void *)zram,sizeof(zram));
    for(i=0;i<9;i++) dirty[i]=1;
    for(i=0;i<5;i++) physicalPatch[i]=255;
    for(i=0;i<8;i++) ranks[i]=i;
    commandW=commandR=transportW=0;fragment=recordCount=publishRecord=0;
    channelDecode=switchFlags=0;clear(channelWait,sizeof(channelWait));
    eventW=eventR=0;scheduledW=scheduledR=eventLoss=0;clear(&cachedStatus,sizeof(cachedStatus));
    pcmOwner=pcmMusicPriority=paused=playing=completed=localFault=pendingRender=0;fmGain=psgGain=0;physicalMute=0;noiseShadow=255;
    tempoScale=CLP_TEMPO_NORMAL;tempoFlags=0;
    playing=1;songGeneration=1;voiceCount=1;selectedVoice=0;voices[0].slot=0;voices[0].enabled=1;voices[0].patch=0;
    timeline=played=clockRemainder=lastClock=0;
#if CALLIOPE_HOST_PCM
    pcmPrepared=pcmSent=0;renderRemaining=renderLoopLength=0;
#endif
    masterClock=53693175UL;clockWhole=221;clockFraction=228855;initialized=1;partCount=arrangementCount=0;
}
static const u8 *recordAt(u16 i)
{
    return renderPages[i/CLH_PER_PAGE]+CLH_HEADER+(i%CLH_PER_PAGE)*CLH_RECORD_BYTES;
}
static bool has(u8 reg,u8 value)
{
    u16 i;for(i=0;i<recordCount;i++) if(recordAt(i)[0]==1 && recordAt(i)[1]==reg && recordAt(i)[2]==value) return true;
    return false;
}
static bool hasPsg(u8 value)
{
    u16 i;for(i=0;i<recordCount;i++) if(recordAt(i)[0]==3 && recordAt(i)[1]==value) return true;
    return false;
}
static void multi_psg(void)
{
    static const u8 stream[]={0x30,2,0x81,0x12,0x30,1,0x90,
        0x30,2,0xc2,0x23,0x30,1,0xd3,0x30,1,0xe6,0x30,1,0xf4,0,1,255};
    u8 i;
    reset();data[102]=6;data[107]=13;put24(data+104,256);put24(data+108,0);memcpy(data+256,stream,sizeof(stream));
    startEffect(100,2,10);assert(owners[5] && owners[5]==owners[7] && owners[7]==owners[8] && !owners[6]);
    recordCount=0;decode(1);assert(hasPsg(0x81) && hasPsg(0x12) && hasPsg(0x90));
    assert(hasPsg(0xc2) && hasPsg(0x23) && hasPsg(0xd3) && hasPsg(0xe6) && hasPsg(0xf4));
    assert(noiseShadow==0xe6 && !localFault);
    recordCount=0;decode(1);assert(!effects[0].active && !owners[5] && !owners[7] && !owners[8]);
    assert(hasPsg(0x9f) && hasPsg(0xdf) && hasPsg(0xff));
    /* Rejection on the last channel must preserve every earlier victim. */
    reset();data[102]=3;data[107]=32;startEffect(100,1,11);
    data[107]=128;startEffect(100,4,12);
    data[102]=6;data[107]=13;startEffect(100,3,13);
    assert(effects[0].token==11 && effects[0].active && effects[1].token==12 && effects[1].active && !owners[8]);
    startEffect(100,5,14);assert(owners[5]==owners[7] && owners[7]==owners[8] && owners[5]);
    data[102]=3;data[107]=128;startEffect(100,5,15);assert(owners[5]==owners[7] && owners[7]==owners[8]);
    startEffect(100,6,16);assert(!owners[5] && owners[7] && !owners[8]);
    /* One context per effect; no free context means no partial claim. */
    reset();data[102]=2;
    for(i=0;i<4;i++) { data[107]=(u8)(1<<i);startEffect(100,2,(u16)(20+i)); }
    data[102]=6;data[107]=15;startEffect(100,3,30);
    for(i=5;i<9;i++) assert(!owners[i]);
    for(i=0;i<4;i++) assert(effects[i].active && effects[i].token==20+i);
    /* Loop termination, music stop, token stop and stop-all release the set. */
    reset();data[102]=6;data[107]=15;put24(data+108,256);startEffect(100,2,40);
    decode(1);decode(1);assert(effects[0].active && !localFault);
    stopMusic();for(i=5;i<9;i++) assert(owners[i]);
    assert(clp_stopSfx(40));controls();for(i=5;i<9;i++) assert(!owners[i]);
    startEffect(100,2,41);assert(clp_stop());controls();for(i=5;i<9;i++) assert(!owners[i]);
    /* Existing fixed-noise/pair streams must also emit their raw latches. */
    reset();data[102]=5;data[107]=0;put24(data+108,0);startEffect(100,2,50);
    recordCount=0;decode(1);assert(hasPsg(0xe6) && hasPsg(0xf4) && hasPsg(0xc2));
}
static void tone_groups(void)
{
    static const u8 stream[]={0x30,6,0x81,0x12,0x90,0xa2,0x23,0xb3,0,1,255};
    u8 occupied,i;
    for(occupied=5;occupied<8;occupied++) {
        reset();data[102]=3;data[107]=(u8)(1<<occupied);put24(data+104,256);put24(data+108,0);
        startEffect(100,8,1);
        data[102]=7;data[107]=2;memcpy(data+256,stream,sizeof(stream));startEffect(100,8,2);
        assert(effects[0].active && effects[0].token==1 && effects[1].active && effects[1].token==2);
        assert(!(effects[1].mask&(1<<occupied)) && effects[1].mask==((7<<5)^(1<<occupied)));
        recordCount=0;decode(2);
        { u8 ordinal=1;for(i=5;i<8;i++) if(i!=occupied) assert(hasPsg((u8)(0x80+((i-5)<<5)+ordinal++))); }
        decode(2);assert(!effects[1].active && owners[occupied]==1);
        for(i=5;i<8;i++) if(i!=occupied) assert(!owners[i]);
    }
    reset();data[102]=3;put24(data+104,256);put24(data+108,0);
    for(i=5;i<8;i++) { data[107]=(u8)(1<<i);startEffect(100,i==7?8:1,i); }
    data[102]=7;data[107]=3;startEffect(100,8,9);
    for(i=0;i<3;i++) assert(effects[i].active && effects[i].token==i+5);
    startEffect(100,9,10);assert(effects[owners[5]-1].active && effects[owners[5]-1].token==10);
    assert(owners[5]==owners[6] && owners[6]==owners[7] && owners[5]);
    assert(clp_stopSfx(10));controls();for(i=5;i<8;i++) assert(!owners[i]);
    /* Loop and stop retain/release the entire dynamically allocated set. */
    data[107]=2;put24(data+108,256);data[7]=1;put24(data+13,100);put16(data+100,9);
    put24(data+22,5000);memset(data+5000,0,1024);put24(data+5000,5768);data[5768+9]=1;
    assert(clp_playSfx(9,8,11));controls();decode(1);decode(1);
    assert(effects[0].active && !localFault);assert(clp_stop());controls();for(i=5;i<8;i++) assert(!owners[i]);
}
static void lfo(void)
{
    static const u8 stream[]={0x39,8,0,1,0x39,0,255};
    reset();selectedVoice=255;pc=4000;memcpy(data+4000,stream,sizeof(stream));recordCount=0;decode(0);
    assert(has(0x22,8) && !localFault);recordCount=0;decode(0);assert(has(0x22,0) && !localFault);
}
static void panning(void)
{
    static const u8 music[]={0x10,0,0x16,0x95,0x11,0x34,0x12,0x80,0x16,0x55,0x80};
    static const u8 fx[]={0x10,0,0,0x16,0,0x55,0x11,0,0x45,0x23,0,1,0x16,0,0x95,0,1,255};
    reset();data[8]=1;put24(data+16,64);data[94]=15;data[93]=0xd5;
    channelDecode=1;pc=4000;memcpy(data+4000,music,sizeof(music));decode(0);
    assert(!localFault && voices[0].pan==0x95 && has(0xb4,0x95) && has(0x28,0xf0));
    recordCount=0;decode(0);assert(has(0xb4,0x55) && !has(0x28,0xf0) && !has(0x28,0));
    /* Stolen music pan advances without changing the SFX or acquiring FM0. */
    data[102]=2;data[107]=1;put24(data+104,256);memcpy(data+256,fx,sizeof(fx));
    startEffect(100,2,77);decode(1);assert(effects[0].tone.pan==0x55 && has(0xb4,0x55));
    pc=4008;data[4009]=0xb5;recordCount=0;decode(0);assert(!recordCount && voices[0].pan==0xb5);
    recordCount=0;decode(1);assert(has(0xb4,0x95) && !has(0x28,0xf0));
    releaseEffect(0);pc=4008;recordCount=0;decode(0);assert(!recordCount && dirty[0]);
    restore(&voices[0],0);assert(!dirty[0] && has(0xb4,0xb5) && has(0x28,0xf0));
    /* Enabling a part waits for its next note. */
    voices[0].enabled=0;switchMask=1;switchFlags=CLP_IMMEDIATE;
    recordCount=0;boundary(1,0);assert(!has(0xb4,0xb5));
    voices[0].enabled=0;pc=4008;data[4009]=0x95;recordCount=0;decode(0);
    assert(!recordCount && voices[0].pan==0x95);
    /* FM4 uses the second YM port and register B5. */
    reset();data[8]=1;put24(data+16,64);data[94]=15;data[93]=0xd5;voices[0].slot=4;
    channelDecode=1;pc=4000;memcpy(data+4000,music,sizeof(music));decode(0);
    assert(ymValid[1][0xb5] && ymValues[1][0xb5]==0x95 && !localFault);
    reset();channelDecode=1;bankSize=4001;data[4000]=0x16;pc=4000;decode(0);assert(localFault==3);
}
static void operator_masks(void)
{
    u8 mask,slot;
    for(mask=0;mask<16;mask++) for(slot=0;slot<5;slot++) {
        reset();data[8]=1;put24(data+16,64);data[94]=mask;
        voices[0].slot=slot;voices[0].gate=1;voices[0].frequency=0x1234;
        restore(&voices[0],0);
        assert(!localFault && has(0x28,(u8)((mask<<4)|(slot<3?slot:slot+1))));
        recordCount=0;restore(&voices[0],1);
        assert(has(0x28,(u8)((mask<<4)|(slot<3?slot:slot+1))));
    }
}
static bool hasYm(u8 port,u8 reg,u8 value)
{
    u16 i;for(i=0;i<recordCount;i++) if(recordAt(i)[0]==port+1 && recordAt(i)[1]==reg && recordAt(i)[2]==value) return true;
    return false;
}
static void multi_fm(void)
{
    static const u8 stream[]={0x10,0,0,0x37,0,30,31,32,33,15,0x11,0,0x11,0x22,
        0x10,4,1,0x37,4,40,41,42,43,15,0x11,4,0x45,0x23,0,1,
        0x36,0,0x56,0x22,0x37,4,50,51,52,53,15,0,1,0x13,0,0,1,255};
    u8 i;
    reset();data[8]=2;put24(data+16,64);memset(data+64,0,64);data[92]=data[124]=7;data[95]=data[127]=15;
    data[102]=8;data[107]=17;put24(data+104,256);put24(data+108,0);memcpy(data+256,stream,sizeof(stream));
    startEffect(100,2,10);assert(owners[0] && owners[0]==owners[4] && !owners[1] && !owners[5]);
    recordCount=0;decode(1);assert(!localFault);
    assert(sfxFm[0].gate && sfxFm[4].gate && sfxFm[0].patch==0 && sfxFm[4].patch==1);
    assert(sfxFm[0].frequency==0x2211 && sfxFm[4].frequency==0x2345);
    assert(hasYm(0,0xa0,0x11) && hasYm(1,0xa1,0x45) && hasYm(0,0x4c,33) && hasYm(1,0x4d,43));
    recordCount=0;decode(1);
    assert(sfxFm[0].frequency==0x2256 && sfxFm[4].frequency==0x2345 && sfxFm[0].tl[3]==33 && sfxFm[4].tl[3]==53);
    assert(hasYm(0,0xa0,0x56) && hasYm(1,0x4d,53) && !hasYm(1,0xa1,0x56));
    decode(1);assert(!sfxFm[0].gate && sfxFm[4].gate);
    decode(1);assert(!effects[0].active && !owners[0] && !owners[4] && dirty[0] && dirty[4]);
    /* Every victim is checked before any owner is released. */
    reset();data[102]=2;data[107]=1;startEffect(100,1,11);data[107]=16;startEffect(100,4,12);
    data[102]=8;data[107]=17;startEffect(100,3,13);
    assert(effects[0].active && effects[0].token==11 && effects[1].active && effects[1].token==12);
    startEffect(100,5,14);assert(owners[0] && owners[0]==owners[4]);
    data[102]=2;data[107]=16;startEffect(100,5,15);assert(owners[0]==owners[4]);
    startEffect(100,6,16);assert(!owners[0] && owners[4] && effects[owners[4]-1].token==16);
    /* Four unrelated PSG contexts cannot be partially displaced. */
    reset();data[102]=3;for(i=0;i<3;i++) { data[107]=(u8)(32<<i);startEffect(100,1,(u16)(20+i)); }
    data[102]=4;startEffect(100,1,23);data[102]=8;data[107]=31;startEffect(100,3,30);
    for(i=0;i<5;i++) assert(!owners[i]);for(i=0;i<4;i++) assert(effects[i].active && effects[i].token==20+i);
    /* A persistent five-channel effect is one instance and retains ownership. */
    reset();data[102]=8;data[107]=31;put24(data+108,256);startEffect(100,2,40);
    for(i=0;i<5;i++) assert(owners[i]==1);
    decode(1);decode(1);decode(1);decode(1);assert(effects[0].active && !localFault);
    stopMusic();for(i=0;i<5;i++) assert(owners[i]);
    assert(clp_stopSfx(40));controls();for(i=0;i<5;i++) assert(!owners[i]);
    startEffect(100,2,41);assert(clp_stop());controls();for(i=0;i<5;i++) assert(!owners[i]);
    /* Reject physical FM6 and unowned operands before touching channel state. */
    reset();data[102]=8;data[107]=17;put24(data+108,0);data[256]=0x11;data[257]=5;data[258]=0;data[259]=0;
    startEffect(100,2,50);decode(1);assert(localFault==12 && !sfxFm[0].gate && !sfxFm[4].gate);
    reset();data[257]=1;startEffect(100,2,51);decode(1);assert(localFault==12);
}
static void ownership(void)
{
    static const u8 fx[]={0x10,0,0,0x11,0,0x11,0x11,0,2,255};
    static const u8 music[]={0x36,0x56,0x23,0x80,0x11,0x67,0x24,0x80};
    reset();data[8]=1;put24(data+16,64);data[94]=15;data[68]=10;data[69]=20;data[70]=30;data[71]=40;data[95]=8;
    data[102]=2;data[103]=2;put24(data+104,256);data[107]=1;put24(data+108,0);memcpy(data+256,fx,sizeof(fx));
    startEffect(100,2,7);assert(owners[0]==1);decode(1);assert(effects[0].tone.gate);
    channelDecode=1;pc=512;memcpy(data+512,music,sizeof(music));decode(0);assert(voices[0].frequency==0x2356);
    releaseEffect(0);assert(!owners[0] && dirty[0]);recordCount=0;
    pc=512;decode(0);assert(!recordCount);decode(0);assert(!dirty[0] && has(0x28,0xf0) && has(0xa0,0x67));
    voices[0].enabled=0;switchMask=1;switchFlags=CLP_IMMEDIATE;recordCount=0;boundary(1,0);
    assert(voices[0].enabled && !has(0x28,0xf0));
    pc=516;decode(0); /* Fresh note reacquires the channel before gain checks. */
    voices[0].sensitivity=8;voices[0].tl[3]=40;recordCount=0;
    assert(clp_setMusicVolume(107,15));controls();assert(fmGain==20 && has(0x4c,60));
    /* A paired effect cannot leave its tone-3 owner behind when preempted. */
    clear(effects,sizeof(effects));clear(owners,sizeof(owners));data[102]=5;data[107]=0;
    startEffect(100,2,11);assert(owners[7] && owners[7]==owners[8]);
    data[102]=3;data[107]=128;startEffect(100,2,12);assert(owners[7]==owners[8]);
    startEffect(100,3,13);assert(owners[7] && !owners[8]);
}
static void immediate_parts(void)
{
    u32 tick;reset();partCount=voiceCount=2;partMeta=2000;
    memset(data+2000,0,8);data[2004]=1;voices[1].slot=1;voices[1].patch=0;
    voices[1].gate=1;voices[1].frequency=0x1234;tick=songTick;
    assert(clp_setParts(2,CLP_NEXT_BAR,0));controls();assert(voices[0].enabled && switchFlags==CLP_NEXT_BAR);
    assert(clp_setParts(2,CLP_IMMEDIATE,0));controls();assert(!voices[0].enabled && voices[1].enabled && !switchFlags && songTick==tick);
    assert(!clp_setParts(2,(ClpBoundary)2,0) && !clp_setParts(2,(ClpBoundary)4,0));
    assert(clp_setParts(1,CLP_IMMEDIATE,0));controls();paused=1;recordCount=0;
    assert(clp_setParts(2,CLP_IMMEDIATE,0));controls();
    assert(voices[1].enabled && !switchFlags && !has(0x28,0xf1));
}
#if CALLIOPE_HOST_PCM
static void pcm(void)
{
    u16 i;reset();data[202]=1;put24(data+204,1024);put24(data+207,64);put24(data+210,0);put24(data+213,0);
    for(i=0;i<64;i++) data[1024+i]=(u8)i;
    physicalMute=512;startPcm(200,0,0,1);assert(!pcmOwner);physicalMute=0;
    paused=1;startPcm(200,0,0,1);assert(!pcmOwner);paused=0;
    startPcm(200,0,0,1);preparePcm(16);assert(pcmOwner==1 && preparedPcm[15]==15);
    timeline=16;data[302]=1;put24(data+304,2048);put24(data+307,64);put24(data+310,0);put24(data+313,0);memset(data+2048,240,64);
    startPcm(300,1,2,2);startPcm(200,0,0,1);startPcm(200,1,2,3);preparePcm(32);
    assert(pcmOwner==2 && pcmToken==2 && preparedPcm[16]==240);
    startPcm(200,1,3,4);preparePcm(48);assert(pcmToken==4 && preparedPcm[32]==0);
    assert(clp_stopSfx(4));controls();preparePcm(64);assert(!pcmOwner && preparedPcm[48]==128);
    /* Uploads wrap both rings and publish only complete batches. */
    pcmSent=992;pcmPrepared=1056;played=992;put16(M+34,992);lastClock=992;
    for(i=992;i<1056;i++) preparedPcm[i&2047]=(u8)i;
    uploadPcm();assert(pcmSent==1056 && le16((const u8 *)M+0x187)==0x1820);
    for(i=992;i<1056;i++) assert(zram[0x1800+(i&1023)]==(u8)i);
    owned=true;i=(u16)releases;pcmPrepared=1088;uploadPcm();assert(owned && releases==i);owned=false;
    /* A forward loop plays its prefix once, then repeats the exact bounds. */
    reset();put24(data+204,1024);put24(data+207,6);put24(data+210,2);put24(data+213,5);
    for(i=0;i<6;i++) data[1024+i]=(u8)(10+i);
    startPcm(200,1,2,9);preparePcm(12);
    for(i=0;i<12;i++) assert(preparedPcm[i]==10+(i<5?i:2+(i-5)%3));
    timeline=12;pcmStop(0);preparePcm(16);assert(preparedPcm[12]==128 && !renderLoopLength);
}
#else
static void pcm(void)
{
    reset();put24(data+204,1024);put24(data+207,64);put24(data+210,2);put24(data+213,10);
    startPcm(200,1,2,9);
    assert(pcmOwner==2 && header[1]==1 && le24(header+15)==10 && le24(header+21)==8);
    startPcm(200,0,0,1);startPcm(200,1,2,10);assert(pcmToken==9);
    startPcm(200,1,3,11);assert(pcmToken==11);
    assert(clp_stopSfx(11));controls();assert(header[1]==2 && !pcmOwner);
}
#endif
static void pcmPriorities(void)
{
    reset();put24(data+204,1024);put24(data+207,64);put24(data+210,0);put24(data+213,0);
    initialized=0;assert(!clp_setMusicPcmPriority(2));initialized=1;
    assert(clp_setMusicPcmPriority(2));
    startPcm(200,0,0,1);assert(pcmOwner==1);
    startPcm(200,1,1,18);assert(pcmOwner==1 && pcmToken==1);
    startPcm(200,1,8,4);assert(pcmOwner==2 && pcmToken==4);
    startPcm(200,0,0,1);assert(pcmOwner==2 && pcmToken==4);
    pcmStop(0);startPcm(200,1,1,18);assert(pcmOwner==2 && pcmToken==18);
    startPcm(200,0,0,1);assert(pcmOwner==1 && pcmMusicPriority==2);
    startPcm(200,1,2,18);assert(pcmOwner==2 && pcmToken==18);
    startPcm(200,0,0,1);assert(pcmOwner==2 && pcmToken==18);
    timeline=pcmEnd;startPcm(200,1,1,19);assert(pcmOwner==2 && pcmToken==19);
    pcmStop(0);startPcm(200,0,0,1);timeline=pcmEnd;
    startPcm(200,1,1,18);assert(pcmOwner==2 && pcmToken==18);
}
static void packets(void)
{
    u16 i;u8 page;
    reset();recordCount=publishRecord=fragment=0;publishPage=0;transportW=250;
    M[0x181]=M[0x182]=250;
    for(i=0;i<200;i++) emit(3,(u8)i,0,0);
    put16(header+2,1234);pendingRender=1;nextTimeline=4321;
    while(pendingRender) assert(publish());
    assert(transportW==253 && M[0x180]==253 && timeline==4321);
    for(page=0;page<3;page++) {
        u16 first=(u16)page*CLH_PER_PAGE;u8 count=(u8)(page==2?200-first:CLH_PER_PAGE);
        const volatile u8 *bytes=zram+0x1000+((250+page)&7)*256;
        assert(bytes[0]==count && (bytes[2]|((u16)bytes[3]<<8))==1234);
        for(i=0;i<count;i++) assert(bytes[CLH_HEADER+i*3]==3 && bytes[CLH_HEADER+i*3+1]==(u8)(first+i));
    }
    reset();recordCount=0;for(i=0;i<CLH_RECORDS;i++) emit(3,(u8)i,0,0);
    assert(!localFault);emit(3,0,0,0);assert(localFault==1);
}
static void events_and_limits(void)
{
    ClpEvent e[4];u16 i;reset();songGeneration=1;songTick=9;timeline=100;
    for(i=0;i<20;i++) queueCue(i,77);
    played=99;deliverCues();assert(eventW==0);played=100;deliverCues();assert(eventW==16 && eventLoss==4);
    assert(clp_poll(e,4)==4 && e[0].tick==9 && e[3].id==3);
    request=2;assert(clp_poll(e,4)==0);
    eventLoss=65535;played=timeline;eventW=16;eventR=0;queueCue(9,1);deliverCues();assert(eventLoss==65535);
    assert(send(7,2,0));controls();assert(!paused);
    assert(send(7,1,0));controls();assert(paused);assert(send(8,1,0));controls();assert(!paused);
    /* Truncated fixed and variable operands fail before touching voice state. */
    reset();channelDecode=1;bankSize=4002;data[4000]=0x36;data[4001]=0;pc=4000;voices[0].frequency=1234;decode(0);
    assert(localFault==3 && voices[0].frequency==1234 && pc==4001);
    reset();bankSize=4004;data[4000]=0x30;data[4001]=3;pc=4000;decode(0);assert(localFault==3);
    reset();for(i=0;i<1024;i++) { data[4000+i*3]=0x34;data[4001+i*3]=44;data[4002+i*3]=1; }pc=4000;decode(0);assert(localFault==10);
    reset();data[1002]=255;data[1003]=255;put24(data+1004,3000);data[1011]=1;put24(data+1008,2300);data[2300]=0;data[2303]=1;put16(data+1014,300);
    for(i=0;i<256;i++) put24(data+3000+i*3,3800);
    put24(data+3800,4000);put24(data+3803,4500);
    data[4000]=0;data[4001]=1;data[4002]=0xfd;startMusic(1000,1);assert(orderCount==256);
    for(i=0;i<257;i++) { recordCount=0;decode(0); }
    assert(completed && !playing && !localFault);
}
static void music_silence(void)
{
    u8 pause;
    for(pause=0;pause<2;pause++) {
        u16 i,count=0;
        reset();owners[1]=1;effects[0].active=1;effects[0].token=99;
        recordCount=0;
        if(pause) { assert(send(7,1,0));controls();assert(paused && playing); }
        else { stopMusic();assert(!playing); }
        assert(owners[1]==1 && effects[0].active && effects[0].token==99);
        for(i=0;i<recordCount;i++) {
            const u8 *r=recordAt(i);
            if((r[0]==1 || r[0]==2) && (r[1]&0xf0)==0x40) {
                u8 slot=(u8)((r[1]&3)+(r[0]==2?3:0));
                assert(slot!=1 && r[2]==127);count++;
            }
        }
        assert(count==16 && !localFault);
    }
}
static void authored_bank(const char *name)
{
    /* Melody now writes silent TLs at EOF; the other song traces are unchanged. */
    static const u32 golden[]={0x4f7f47a4UL,0x76b1144eUL,0xe64a4057UL,0xd8f659e2UL,0x4af5e588UL};
    FILE *file;size_t n;u8 song;
    assert(fopen_s(&file,name,"rb")==0);n=fread(data,1,sizeof(data),file);assert(!ferror(file));fclose(file);
    assert(n>32 && memcmp(data,"BGM1",4)==0);
    for(song=0;song<data[6];song++) {
        u32 descriptor=le24(data+10)+(u32)song*24;u16 tick;
        reset();bankSize=(u32)n;startMusic(descriptor,1);
        u32 trace=2166136261UL;
        for(tick=0;tick<6000;tick++) {
            u16 record;u8 byte;
            render();if(localFault) fprintf(stderr,"song %u clock %u fault %u pc %lu order %u channel %u mode %u\n",song,tick,localFault,(unsigned long)pc,order,selectedVoice,channelDecode);assert(!localFault && recordCount<=CLH_RECORDS);
            for(record=0;record<recordCount;record++) for(byte=0;byte<CLH_RECORD_BYTES;byte++) trace=(trace^recordAt(record)[byte])*16777619UL;
            trace=(trace^recordCount)*16777619UL;trace=(trace^songTick)*16777619UL;trace=(trace^pc)*16777619UL;
            timeline=nextTimeline;clockRemainder=nextRemainder;pendingRender=0;
#if CALLIOPE_HOST_PCM
            pcmSent=pcmPrepared;
#endif
        }
        assert(songTick>100 && song<sizeof(golden)/sizeof(golden[0]) && trace==golden[song]);
        {
            u16 scale;
            for(scale=128;scale<=512;scale+=384) {
                reset();bankSize=(u32)n;startMusic(descriptor,1);tempoScale=scale;
                for(tick=0;tick<6000;tick++) {
                    render();assert(!localFault && recordCount<=CLH_RECORDS);
                    timeline=nextTimeline;clockRemainder=nextRemainder;pendingRender=0;
#if CALLIOPE_HOST_PCM
                    pcmSent=pcmPrepared;
#endif
                }
            }
        }
    }
}
static void tempo_controls(void)
{
    u16 i;u32 ticks;reset();partCount=1;voiceCount=0;paused=1;credit=123;
    assert(clp_setTempoScale(384,CLP_IMMEDIATE,0));controls();
    assert(tempoScale==384 && !tempoFlags && credit==123 && paused);
    switchFlags=CLP_NEXT_BAR;switchMask=0;
    assert(clp_setTempoScale(128,CLP_NEXT_MARKER,9));controls();
    boundary(CLP_NEXT_MARKER,8);assert(tempoScale==384 && tempoFlags);
    boundary(CLP_NEXT_BAR,0);assert(!switchFlags && tempoFlags);
    boundary(CLP_NEXT_MARKER,9);assert(tempoScale==128 && !tempoFlags && credit==123);
    assert(clp_setTempoScale(512,CLP_NEXT_BAR,0));controls();
    assert(clp_setTempoScale(192,CLP_NEXT_MARKER,7));controls();
    boundary(CLP_NEXT_BAR,0);assert(tempoScale==128);
    boundary(CLP_NEXT_MARKER,7);assert(tempoScale==192);
    assert(clp_setTempoScale(512,CLP_NEXT_BAR,0));controls();
    assert(clp_setTempoScale(256,CLP_IMMEDIATE,0));controls();assert(!tempoFlags && tempoScale==256);
    assert(sendAt(13,CLP_IMMEDIATE,2,512));controls();assert(tempoScale==256);
    assert(clp_setTempoScale(512,CLP_NEXT_BAR,0));controls();stopMusic();assert(!tempoFlags);
    /* Exact half-rate rounding and independent SFX clock. */
    reset();voiceCount=0;tickRate=125;tempoScale=128;waitTicks=60000;songTick=0;credit=0;
    for(i=0;i<300;i++) render();assert(songTick==63 && !credit && !localFault);
    reset();voiceCount=0;tickRate=300;tempoScale=512;waitTicks=60000;songTick=0;credit=0;
    effects[0].active=1;effects[0].wait=100;
    for(i=0;i<30;i++) render();ticks=songTick;
    assert(ticks==60 && effects[0].wait==70 && !localFault);
    reset();voiceCount=0;tickRate=300;tempoScale=128;waitTicks=60000;songTick=0;credit=0;
    effects[0].active=1;effects[0].wait=100;
    for(i=0;i<30;i++) render();assert(songTick==15 && effects[0].wait==70 && !localFault);
    /* Authored rate changes retain the scale and retain their existing phase reset. */
    reset();voiceCount=0;tempoScale=512;credit=123;pc=4000;channelDecode=0;
    data[4000]=0x34;data[4001]=0xf4;data[4002]=1;data[4003]=0;data[4004]=1;
    decode(0);assert(tickRate==500 && tempoScale==512 && !credit && !localFault);
    /* A new song restores unity and cancels the pending request. */
    memset(data+2000,0,100);data[2011]=1;data[2012]=1;put24(data+2008,2050);put24(data+2004,2070);
    data[2014]=0x2c;data[2015]=1;put24(data+2070,2080);put24(data+2080,2090);put24(data+2083,2095);
    tempoFlags=CLP_NEXT_MARKER;startMusic(2000,8);assert(tempoScale==256 && !tempoFlags);
}
#include "effect_index_test.h"
static void elapsed_position(void)
{
    initialized=0;assert(clp_getElapsed()==0);
    initialized=1;elapsedTick=0;songTick=999;
    M[10]=0x78;M[11]=0x56;M[12]=0x34;M[13]=0x12;M[33]=2;
    assert(clp_getElapsed()==0x12345678UL);
    M[33]=3;M[10]=0;assert(clp_getElapsed()==0x12345678UL);
    M[33]=4;assert(clp_getElapsed()==0x12345600UL);
    owned=true;{int before=releases;assert(clp_getElapsed()==0x12345600UL && owned && releases==before);}owned=false;
}
#include "tempo_test.h"
#include "start_generation_test.h"
static void resident_patch_limits(void)
{
    u8 bank[32] = {0};
    put24(bank + 16, 32);
    bank[8] = 26; assert(patchTableValid(bank, 32 + 26 * 32));
    bank[8] = 63; assert(patchTableValid(bank, 32 + 63 * 32));
    assert(!patchTableValid(bank, 32 + 63 * 32 - 1));
    bank[8] = 64; assert(!patchTableValid(bank, 32 + 64 * 32));
    assert(0x800 + CLH_PATCH_LIMIT * 32 <= 0xff8);
}
int main(int argc,char **argv)
{
    resident_patch_limits();
    assert(argc==2);elapsed_position();tempo_controls();operator_masks();ownership();immediate_parts();multi_psg();multi_fm();tone_groups();lfo();panning();pcm();pcmPriorities();packets();events_and_limits();music_silence();authored_bank(argv[1]);
    tempo_api_tests();
    start_generation_tests();
    effect_index_tests();
    assert(!owned && !disabled);puts("hybrid sequencing, ownership, PCM, events and authored-bank checks passed");return 0;
}
