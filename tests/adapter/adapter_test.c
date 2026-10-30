#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "genesis.h"
static vu8 mailbox[384], sfxState[64], pcmState[33];
#define CALLIOPE_SFX_STATE sfxState
#define CALLIOPE_PCM_STATE pcmState
#define CALLIOPE_MAILBOX mailbox
#include "../../src/calliope.c"
const u8 clp_driver[8192]={0};
static bool owned; static int releases, requests, disabled;
static ClpEvent *pendingOutput;
void SYS_disableInts(void) { disabled++; }
void SYS_enableInts(void) { assert(disabled>0); disabled--; }
bool Z80_getAndRequestBus(bool wait) { bool old=owned; assert(wait && disabled); owned=true; requests++; return old; }
void Z80_releaseBus(void) { assert(owned); if(pendingOutput) assert(pendingOutput->id==0xaaaa); owned=false; releases++; }
void Z80_requestBus(bool wait) { assert(wait); owned=true; }
void Z80_loadCustomDriver(const u8 *p,u16 n) { (void)p; (void)n; }
void Z80_useBusProtection(u16 p) { (void)p; }
void Z80_setForceDelayDMA(bool b) { (void)b; }
bool SYS_isPAL(void) { return false; }
void SYS_doVBlankProcess(void) {}
static void event(u8 counter,u16 gen,u16 id) {
    u16 p=192+(counter&15)*12;
    mailbox[p]=1; mailbox[p+1]=0; mailbox[p+2]=(u8)id; mailbox[p+3]=(u8)(id>>8);
    mailbox[p+4]=0x78; mailbox[p+5]=0x56; mailbox[p+6]=(u8)gen; mailbox[p+7]=(u8)(gen>>8);
    mailbox[p+8]=0xef; mailbox[p+9]=0xcd; mailbox[p+10]=0xab; mailbox[p+11]=0x89;
}
#include "effect_index_test.h"
#include "tempo_test.h"
#include "start_generation_test.h"
int main(void) {
    ClpEvent result[5]; ClpStatus status; int before;
    assert(!clp_sfxActive(0x4001));
    assert(!clp_setMusicPcmPriority(2));
    assert(clp_getElapsed()==0);
    request=0x1234; memset(result,0xaa,sizeof(result));
    mailbox[4]=254; mailbox[3]=3;
    event(254,0,1); event(255,request,2); event(0,request,3); event(1,1,4); event(2,request,5);
    pendingOutput=result;
    assert(clp_poll(result,5)==2); pendingOutput=NULL;
    assert(mailbox[4]==2 && result[0].id==2 && result[1].id==3);
    assert(result[0].parameter==0x5678 && result[0].tick==0x89abcdef);
    assert(clp_poll(result,4)==1 && result[0].id==5 && mailbox[4]==3);
    before=requests; assert(clp_poll(NULL,4)==0 && clp_poll(result,0)==0 && requests==before);
    owned=true; before=releases; assert(clp_poll(result,4)==0 && owned && releases==before); owned=false;
    mailbox[24]=255;mailbox[25]=255;mailbox[5]=0;mailbox[20]=0x34;mailbox[21]=0x12;mailbox[22]=1;
    bankData=clp_driver;
    mailbox[10]=0x78;mailbox[11]=0x56;mailbox[12]=0x34;mailbox[13]=0x12;
    assert(clp_getElapsed()==0x12345678UL);
    owned=true;before=releases;assert(clp_getElapsed()==0x12345678UL && owned && releases==before);owned=false;
    mailbox[33]=1;mailbox[10]=0;assert(clp_getElapsed()==0x12345678UL);
    mailbox[33]=2;assert(clp_getElapsed()==0x12345600UL);
    status=clp_status();assert(status.eventsLost==65535 && !status.fault && status.completedGeneration==request && status.completed);
    mailbox[1]=8;mailbox[2]=248;assert(!send(2,request,0) && mailbox[1]==8);
    mailbox[1]=255;mailbox[2]=255;assert(sendAt(11,16,request,0xfeed8000));
    assert(mailbox[1]==0 && mailbox[184]==11 && mailbox[185]==16 && mailbox[186]==0x34 && mailbox[187]==0x12);
    assert(mailbox[188]==0 && mailbox[189]==0x80 && mailbox[190]==0xed && mailbox[191]==0xfe);
    mailbox[1]=0;mailbox[2]=0;
    assert(!clp_setMusicVolume(128,15) && !clp_setMusicVolume(127,16));
    assert(clp_setMusicVolume(107,10));
    assert(mailbox[64]==12 && mailbox[68]==20 && mailbox[69]==5 && mailbox[1]==1);
    {
        static const u8 metadata[8]={0,1,0,1,1,2,0,1};
        bankData=metadata;partMeta=0;partCount=2;selectedParts=3;mutedGroups=1;
        assert(clp_setMusicPcmPriority(2) && mailbox[30]==2);
        owned=true;before=releases;
        assert(clp_setMusicPcmPriority(255) && mailbox[30]==255 && owned && releases==before);
        owned=false;
        partGroups[0]=1;partGroups[1]=2;mailbox[1]=0;mailbox[2]=0;
        assert(clp_setParts(3,CLP_IMMEDIATE,0));
        assert(mailbox[65]==CLP_IMMEDIATE && mailbox[68]==2);
        assert(clp_setPartGroups(0,2));assert(mailbox[76]==3 && partGroups[0]==2);
        assert(!clp_setPartGroups(2,0));
        mailbox[1]=16;mailbox[2]=0;
        assert(!clp_setPartGroups(0,1) && partGroups[0]==2);
        mailbox[1]=mailbox[2]=0;
        assert(clp_setParts(3,CLP_IMMEDIATE,0) && mailbox[65]==CLP_IMMEDIATE);
        assert(clp_setParts(3,CLP_NEXT_BAR,0) && mailbox[73]==CLP_NEXT_BAR);
        assert(!clp_setParts(3,(ClpBoundary)3,0));
        assert(!clp_setParts(3,(ClpBoundary)2,0) && !clp_setParts(3,(ClpBoundary)4,0));
    }
    {
        static u8 multi[1072];multi[22]=48;multi[48]=48;multi[49]=3;multi[816+9]=1;multi[7]=1;multi[13]=32;multi[32]=9;multi[34]=6;multi[39]=15;
        bankData=multi;bankSize=sizeof(multi);mailbox[1]=mailbox[2]=0;
        assert(clp_playSfx(9,2,99));assert(mailbox[64]==9 && mailbox[66]==99 && mailbox[71]==2);
        multi[34]=7;multi[39]=2;mailbox[1]=mailbox[2]=0;
        assert(clp_playSfx(9,8,13));assert(mailbox[64]==9 && mailbox[66]==13 && mailbox[71]==8);
        multi[34]=8;multi[39]=31;assert(clp_playSfx(9,8,13));
        multi[34]=9;assert(!clp_playSfx(9,8,13));
    }
    {
        static u8 invalid[64];invalid[6]=1;invalid[10]=32;invalid[32]=1;
        bankData=invalid;bankSize=sizeof(invalid);mailbox[1]=mailbox[2]=0;
        assert(!clp_start(1) && !mailbox[1]);
        invalid[43]=128;assert(!clp_start(1) && !mailbox[1]);
        invalid[43]=17;assert(!clp_start(1) && !mailbox[1]);
    }
    mailbox[1]=mailbox[2]=0;bankData=clp_driver;
    memset((void *)sfxState,0,sizeof(sfxState));memset((void *)pcmState,0,sizeof(pcmState));
    assert(!clp_sfxActive(0x4001));
    for (unsigned i=0;i<4;i++) {
        unsigned p=i*16;sfxState[p]=1;sfxState[p+4]=1;sfxState[p+5]=0x40;
        assert(clp_sfxActive(0x4001) && !clp_sfxActive(0x4002));
        sfxState[p]=0;assert(!clp_sfxActive(0x4001));
    }
    mailbox[1]=1;assert(clp_sfxActive(0x4001));mailbox[2]=1;
    pcmState[32]=1;pcmState[12]=2;pcmState[14]=1;pcmState[15]=0x40;
    assert(clp_sfxActive(0x4001));pcmState[32]=0;assert(!clp_sfxActive(0x4001));
    owned=true;before=releases;assert(!clp_sfxActive(0x4001) && owned && releases==before);owned=false;
    tempo_api_tests();
    start_generation_tests();
    effect_index_tests();
    assert(!owned && !disabled); puts("adapter mailbox checks passed"); return 0;
}
