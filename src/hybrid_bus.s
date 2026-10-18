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
/* Amortize C/ABI overhead, keeping eight-byte holds and explicit run gaps. */
.global clh_write_blocks
clh_write_blocks:
    move.l %d2,-(%sp)
    move.l 8(%sp),%a0
    move.l 12(%sp),%a1
    move.l 16(%sp),%d2
    subq.w #1,%d2
.Lblocknext:
    acquire .Lwaitblock .Lheldblock
    bytes8
    move.w #0,0xa11100
    move.w %d1,%sr
    dbra %d2,.Lblockgap
    move.l (%sp)+,%d2
    rts
.Lblockgap:
    .rept 32
    nop
    .endr
    bra .Lblocknext
.Lheldblock:
    bytes8
    move.w %d1,%sr
    dbra %d2,.Lblocknext
    move.l (%sp)+,%d2
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
/* Three short status holds, retaining the publication sequence check in C. */
.global clh_status_snapshot
clh_status_snapshot:
    move.l 4(%sp),%a0
    acquire .Lwaitsnap1 .Lheldsnap
    statusa
    move.w #0,0xa11100
    move.w %d1,%sr
    .rept 32
    nop
    .endr
    ori.w #0x0700,%sr
    move.w #0x0100,0xa11100
.Lwaitsnap2:
    move.w 0xa11100,%d0
    btst #8,%d0
    bne .Lwaitsnap2
    statusb
    move.w #0,0xa11100
    move.w %d1,%sr
    .rept 32
    nop
    .endr
    ori.w #0x0700,%sr
    move.w #0x0100,0xa11100
.Lwaitsnap3:
    move.w 0xa11100,%d0
    btst #8,%d0
    bne .Lwaitsnap3
    statusc
    move.w #0,0xa11100
    move.w %d1,%sr
    rts
.Lheldsnap:
    statusa
    statusb
    statusc
    move.w %d1,%sr
    rts

#endif

#if CALLIOPE_HYBRID
/* Return due channels in the song's cached PCM-first order. */
.global clh_ready
clh_ready:
    movem.l %d2-%d3/%a2,-(%sp)
    move.l 16(%sp),%a0
    move.l 20(%sp),%a1
    move.l 24(%sp),%d2
    move.l 28(%sp),%a2
    moveq #0,%d0
    moveq #0,%d1
    tst.w %d2
    beq .Lreadydone
    subq.w #1,%d2
.Lreadynext:
    move.b (%a1)+,%d1
    move.w %d1,%d3
    add.w %d3,%d3
    tst.w (%a0,%d3.w)
    beq .Lreadyemit
    subq.w #1,(%a0,%d3.w)
    bne .Lreadycontinue
.Lreadyemit:
    move.b %d1,(%a2)+
    addq.w #1,%d0
.Lreadycontinue:
    dbra %d2,.Lreadynext
.Lreadydone:
    movem.l (%sp)+,%d2-%d3/%a2
    rts
#endif
