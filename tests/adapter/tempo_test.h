static void tempo_api_tests(void)
{
    const volatile u8 *c;
    partCount=0;assert(!clp_setTempoScale(256,CLP_IMMEDIATE,0));partCount=1;request=0x1234;
    assert(!clp_setTempoScale(0,CLP_IMMEDIATE,0));
    assert(!clp_setTempoScale(127,CLP_IMMEDIATE,0));
    assert(!clp_setTempoScale(513,CLP_IMMEDIATE,0));
    assert(!clp_setTempoScale(256,(ClpBoundary)3,0));
#if CALLIOPE_HYBRID
    commandW=commandR=0;c=commands[0];
#else
    M[1]=M[2]=0;c=M+64;
#endif
    assert(clp_setTempoScale(384,CLP_NEXT_MARKER,0xfeed));
    assert(c[0]==13 && c[1]==16 && c[2]==0x34 && c[3]==0x12);
    assert(c[4]==0x80 && c[5]==1 && c[6]==0xed && c[7]==0xfe);
    assert(clp_setTempoScale(128,CLP_NEXT_BAR,0));
    assert(clp_setTempoScale(512,CLP_IMMEDIATE,0));
#if CALLIOPE_HYBRID
    commandW=16;commandR=0;
#else
    M[1]=16;M[2]=0;
#endif
    assert(!clp_setTempoScale(256,CLP_IMMEDIATE,0));
}
