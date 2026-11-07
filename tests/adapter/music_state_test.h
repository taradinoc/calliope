static void music_state_tests(void)
{
    static const u16 generations[] = {0, 1, 255, 256, 65535};
    u8 saved[48]; const u8 *savedBank = bankData; unsigned i, j, k, done, fault, ownership;
#if CALLIOPE_HYBRID
    ClpStatus savedStatus = cachedStatus; u8 savedFault = localFault;
    localFault = 0;
#endif
    for (i = 0; i < 48; ++i) saved[i] = M[i];
    bankData = NULL; assert(!clp_musicState(1)); bankData = clp_driver;
    M[33] = 0;
    for (i = 0; i < 5; ++i) for (j = 0; j < 5; ++j) for (k = 0; k < 5; ++k)
    for (done = 0; done < 2; ++done) for (fault = 0; fault < 3; ++fault)
    {
        ClpStatus status; u8 expected;
        M[8] = (u8)generations[j]; M[9] = (u8)(generations[j] >> 8);
        M[20] = (u8)generations[k]; M[21] = (u8)(generations[k] >> 8);
        M[22] = (u8)done; M[5] = (u8)(fault * 127);
        status = clp_status(); expected = status.fault ? CLP_MUSIC_FAULT : 0;
        if (generations[i] && status.generation == generations[i] && status.completed && status.completedGeneration == generations[i])
            expected |= CLP_MUSIC_COMPLETED;
        for (ownership = 0; ownership < 2; ++ownership)
        {
            owned = ownership != 0;
            assert(clp_musicState(generations[i]) == expected);
            assert(owned == (ownership != 0) && !disabled);
            owned = false;
        }
    }
    for (i = 0; i < 48; ++i) M[i] = saved[i];
    bankData = savedBank;
#if CALLIOPE_HYBRID
    cachedStatus = savedStatus; localFault = savedFault;
#endif
}
