#include "calliope_config.h"
#if CALLIOPE_HYBRID
/* Short 68000/Z80 transfers. Arguments use SGDK's stack ABI. Preserve the
   exact SR and existing bus ownership. All indexing/staging happens in C. */
.section .text
.balign 2
.global clh_write8
.global clh_write_pcm32
.macro acquire normal taken
    move.w %sr,%d1
    ori.w #0x0700,%sr
    move.w 0xa11100,%d0
    btst #8,%d0
    beq \taken
    move.w #0x0100,0xa11100
\normal:
    move.w 0xa11100,%d0
    btst #8,%d0
    bne \normal
.endm
.macro bytes8
    .rept 8
    move.b (%a1)+,(%a0)+
    .endr
.endm
.macro pcm8
    .rept 8
    move.b (%a1)+,(%a0)+
    .endr
    move.b %d2,0xa01e87
    move.b %d3,0xa01e88
    swap %d2
    move.b %d2,0xa01e8b
.endm
clh_write8:
    move.l 4(%sp),%a0
    move.l 8(%sp),%a1
    acquire .Lwait8 .Lheld8
    bytes8
    move.w #0,0xa11100
    move.w %d1,%sr
    rts
.Lheld8:
    bytes8
    move.w %d1,%sr
    rts
clh_write_pcm32:
    movem.l %d2-%d4,-(%sp)
    move.l 16(%sp),%a0
    move.l 20(%sp),%a1
    move.l 24(%sp),%d2
    move.l %d2,%d3
    lsr.l #8,%d3
    acquire .Lwaitpcm .Lheldpcm
    moveq #2,%d4
.Lpcmfirst:
    .rept 8
    move.b (%a1)+,(%a0)+
    .endr
    move.w #0,0xa11100
    /* Let the Z80 advance between holds; no pointer arithmetic runs halted. */
    .rept 16
    nop
    .endr
    move.w #0x0100,0xa11100
.Lpcmwait:
    move.w 0xa11100,%d0
    btst #8,%d0
    bne .Lpcmwait
    dbra %d4,.Lpcmfirst
    pcm8
    move.w #0,0xa11100
    move.w %d1,%sr
    movem.l (%sp)+,%d2-%d4
    rts
.Lheldpcm:
    .rept 24
    move.b (%a1)+,(%a0)+
    .endr
    pcm8
    move.w %d1,%sr
    movem.l (%sp)+,%d2-%d4
    rts

/* Contiguous reads and one-byte publication avoid C work while halted. */
.global clh_write1
clh_write1:
    move.l 4(%sp),%a0
    move.l 8(%sp),%a1
    acquire .Lwait1 .Lheld1
    move.b (%a1),(%a0)
    move.w #0,0xa11100
    move.w %d1,%sr
    rts
.Lheld1:
    move.b (%a1),(%a0)
    move.w %d1,%sr
    rts
.global clh_read2
clh_read2:
    move.l 4(%sp),%a0
    acquire .Lwaitr2 .Lheldr2
    moveq #0,%d0
    move.b 1(%a0),%d0
    lsl.w #8,%d0
    move.b (%a0),%d0
    move.w #0,0xa11100
    move.w %d1,%sr
    rts
.Lheldr2:
    moveq #0,%d0
    move.b 1(%a0),%d0
    lsl.w #8,%d0
    move.b (%a0),%d0
    move.w %d1,%sr
    rts
.macro statusa
    move.b 0xa01d21,(%a0)
    move.b 0xa01d00,2(%a0)
    move.b 0xa01d05,3(%a0)
    move.b 0xa01d1a,7(%a0)
.endm
.macro statusb
    move.b 0xa01d0e,8(%a0)
    move.b 0xa01d08,9(%a0)
    move.b 0xa01d09,10(%a0)
    move.b 0xa01d16,11(%a0)
.endm
.macro statusc
    move.b 0xa01d14,12(%a0)
    move.b 0xa01d15,13(%a0)
    move.b 0xa01d21,1(%a0)
.endm
.macro statusfunction name data
.global \name
\name:
    move.l 4(%sp),%a0
    acquire .Lwait\name .Lheld\name
    \data
    move.w #0,0xa11100
    move.w %d1,%sr
    rts
.Lheld\name:
    \data
    move.w %d1,%sr
    rts
.endm
statusfunction clh_statusa statusa
statusfunction clh_statusb statusb
statusfunction clh_statusc statusc

#endif
