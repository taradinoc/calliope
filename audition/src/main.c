#include <genesis.h>
#include "calliope.h"
#include "audio.h"
#include "clp_ids.h"
static const u16 dmaSource[2048] = {0};
volatile u16 gsTestPcmUnderrun;
volatile u16 gsTestAudioActive;
volatile u32 gsTestFrames;
volatile u16 gsTestEvents;
volatile u16 gsTestCompleted;
volatile u16 gsTestFault;
volatile u16 gsTestLastId;
volatile u16 gsTestLastParameter;
int main(bool hardReset)
{
    ClpEvent events[4]; u16 previous=0; u16 frame=0; bool drumsOnly=FALSE; bool sad=FALSE; bool muteMelody=FALSE; bool quiet=FALSE;
    VDP_drawText("Calliope FM / PSG / PCM",2,2);
    VDP_drawText("A: Green Hill B: stop C: ring",2,4);
    VDP_drawText("UP: elevator START: DMA stress",2,5);
    VDP_drawText("DOWN: toggle music drums only",2,10);
    VDP_drawText("LEFT: PSG ring RIGHT: chu",2,11);
    VDP_drawText("X: parts Y: happy/sad Z: mute",2,12);
    VDP_drawText("MODE+DOWN: multi PSG SFX",2,15);
    VDP_drawText("MODE+A: multi FM SFX",2,16);
    VDP_drawText("MODE+LEFT/RIGHT: noise/pair",2,13);
    VDP_drawText("MODE+Z: volume",2,14);
    if (!clp_init(clp_bank,sizeof(clp_bank))) {
        VDP_drawText("Driver init failed",2,4);
        while (TRUE) SYS_doVBlankProcess();
    }
    if (!clp_start(CLP_SONGS_GREEN_HILL_ZONE)) clp_start(CLP_SONGS_MELODY);
    while (TRUE) {
        u16 n,i,buttons=JOY_readJoypad(JOY_1);
        ClpStatus status;
        gsTestAudioActive=1;
        clp_update();
        status=clp_status();
        gsTestAudioActive=0;
        ++gsTestFrames;
        gsTestFault=status.fault;
        gsTestPcmUnderrun=status.pcmUnderrun;
        if (frame==30) { clp_playPcm(CLP_SFX_CLICK,0,1,1); clp_playPcm(CLP_SFX_CLICK,1,1,2); }
        frame++;
        if (frame>=90 && (buttons & BUTTON_START)) DMA_queueDma(DMA_VRAM,(void *)dmaSource,0x8000,2048,2);
        gsTestCompleted=status.completed;
        n=clp_poll(events,4);
        for (i=0;i<n;i++) {
            gsTestEvents++; gsTestLastId=events[i].id; gsTestLastParameter=events[i].parameter;
        }
        if ((buttons & BUTTON_A) && !(previous & BUTTON_A)) {
            if (buttons & BUTTON_MODE) clp_playSfx(CLP_SFX_MULTI_FM,2,10);
            else {
                if (!clp_start(CLP_SONGS_GREEN_HILL_ZONE)) clp_start(CLP_SONGS_MELODY);
            }
        }
        if ((buttons & BUTTON_B) && !(previous & BUTTON_B)) clp_stop();
        if ((buttons & BUTTON_C) && !(previous & BUTTON_C)) clp_playSfx((buttons & BUTTON_MODE)?CLP_SFX_COUPLED_LOOP:CLP_SFX_FM_RING,2,3);
        if ((buttons & BUTTON_UP) && !(previous & BUTTON_UP)) clp_start((buttons & BUTTON_MODE)?CLP_SONGS_NOISE_MUSIC:CLP_SONGS_ELEVATOR);
        if ((buttons & BUTTON_DOWN) && !(previous & BUTTON_DOWN)) {
            if (buttons & BUTTON_MODE) clp_playSfx(CLP_SFX_MULTI_PSG,2,9);
            else { drumsOnly=!drumsOnly; clp_mutePhysical(drumsOnly?0xff:0); }
        }
        if ((buttons & BUTTON_LEFT) && !(previous & BUTTON_LEFT)) clp_playSfx((buttons & BUTTON_MODE)?CLP_SFX_NOISE:CLP_SFX_PSG_RING,2,4);
        if ((buttons & BUTTON_RIGHT) && !(previous & BUTTON_RIGHT)) clp_playSfx((buttons & BUTTON_MODE)?CLP_SFX_COUPLED:CLP_SFX_CHU,2,5);
        if ((buttons & BUTTON_X) && !(previous & BUTTON_X)) { clp_start(CLP_SONGS_ARRANGEMENTS); sad=FALSE; muteMelody=FALSE; }
        if ((buttons & BUTTON_Y) && !(previous & BUTTON_Y)) { sad=!sad; clp_selectArrangement(sad?CLP_ARRANGEMENT_ARRANGEMENTS_SAD:CLP_ARRANGEMENT_ARRANGEMENTS_HAPPY,CLP_IMMEDIATE,0); }
        if ((buttons & BUTTON_Z) && !(previous & BUTTON_Z)) {
            if (buttons & BUTTON_MODE) { quiet=!quiet; clp_setMusicVolume(quiet?95:127,quiet?9:15); }
            else { muteMelody=!muteMelody; clp_muteGroups(muteMelody?CLP_GROUP_ARRANGEMENTS_MELODY:0); }
        }
        previous=buttons;
        VDP_drawText(status.playing?"Playing  ":"Stopped  ",2,6);
        VDP_drawText(status.fault?"FAULT":"     ",2,8);
        SYS_doVBlankProcess();
    }
    return 0;
}
