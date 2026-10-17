/* Minimal Calliope integration: all generated audio sources live in out/. */
#include <genesis.h>
#include "calliope.h"
#include "audio.h"
#include "clp_ids.h"

int main(bool hardReset)
{
    u16 previous = 0;
    bool ready = clp_init(clp_bank, sizeof(clp_bank));
    VDP_drawText("A: replay B: stop C: click", 2, 2);
    if (!ready) VDP_drawText("Audio initialization failed", 2, 4);
    else if (!clp_start(CLP_SONGS_MELODY))
        VDP_drawText("Start request rejected", 2, 4);
    while (TRUE) {
        u16 buttons = JOY_readJoypad(JOY_1);
        if (ready) {
            ClpEvent events[4];
            ClpStatus status;
            u16 count;
            /* Call once every frame in both modes, before VBlank/DMA. */
            clp_update();
            if ((buttons & BUTTON_A) && !(previous & BUTTON_A)) {
                /* Each accepted start assigns a new generation internally. */
                if (!clp_start(CLP_SONGS_MELODY))
                    VDP_drawText("Start request rejected", 2, 4);
            }
            if ((buttons & BUTTON_B) && !(previous & BUTTON_B)) clp_stop();
            if ((buttons & BUTTON_C) && !(previous & BUTTON_C)) {
                if (!clp_playPcm(CLP_SFX_CLICK, 1, 1, 1))
                    VDP_drawText("Effect request rejected", 2, 4);
            }
            do {
                count = clp_poll(events, 4);
                /* Handle notifications here. Poll full batches again so
                   simultaneous events are consumed promptly. */
            } while (count == 4);
            status = clp_status();
            {
                char elapsed[11];
                uintToStr(clp_getElapsed(), elapsed, 10);
                VDP_drawText("Music ticks:", 2, 7);
                VDP_drawText(elapsed, 15, 7);
            }
            if (status.fault || status.eventsLost)
                VDP_drawText("Audio fault or lost event", 2, 5);
        }
        previous = buttons;
        SYS_doVBlankProcess();
    }
    return 0;
}
