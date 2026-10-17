static void start_generation_tests(void)
{
    static u8 songs[80];
    const volatile u8 *first;
    memset(songs,0,sizeof(songs));
    songs[6]=1;songs[10]=32;songs[32]=7;
    songs[40]=64;songs[43]=1;songs[44]=1;songs[67]=1;songs[68]=1;
    bankData=NULL;request=0;
    assert(!clp_start(7) && !request);
    bankData=songs;bankSize=sizeof(songs);
#if CALLIOPE_HYBRID
    commandW=commandR=0;first=commands[0];
#else
    M[1]=M[2]=0;first=M+64;
#endif
    assert(!clp_start(8) && !request);
    assert(clp_start(7)==1 && request==1);
    assert(first[0]==1 && first[2]==1 && first[3]==0);
    assert(clp_start(7)==2 && request==2);
#if CALLIOPE_HYBRID
    commandW=16;commandR=0;
#else
    M[1]=16;M[2]=0;
#endif
    assert(!clp_start(7) && request==2);
#if CALLIOPE_HYBRID
    commandR=commandW;
#else
    M[2]=M[1];
#endif
    assert(clp_start(7)==3 && request==3);
    songs[43]=0;assert(!clp_start(7) && request==3);songs[43]=1;
    request=254;assert(clp_start(7)==255 && request==255);
    assert(clp_start(7)==256 && request==256);
    request=65534;assert(clp_start(7)==65535 && request==65535);
    assert(clp_start(7)==1 && request==1);
}
