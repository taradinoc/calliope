static void indexPut24(u8 *p,u32 value)
{
    p[0]=(u8)value;p[1]=(u8)(value>>8);p[2]=(u8)(value>>16);
}
static void effect_index_tests(void)
{
    static u8 indexed[71000];
    u16 i;u32 next=4880;
    memset(indexed,0,sizeof(indexed));
    indexed[7]=255;indexPut24(indexed+13,32);indexPut24(indexed+22,4112);
    /* Reversed sparse IDs, including zero, maximum valid ID and ordinal 255. */
    for(i=0;i<255;i++) {
        u16 id=i==0?65534:(u16)((254-i)*257);
        u32 descriptor=32+(u32)i*16,entry=4112+(id>>8)*3;
        u32 page=le24(indexed+entry);
        if(!page) { page=next;next+=256;indexPut24(indexed+entry,page); }
        indexed[descriptor]=(u8)id;indexed[descriptor+1]=(u8)(id>>8);
        indexed[descriptor+2]=(i&1)?1:3;
        indexed[page+(id&255)]=(u8)(i+1);
    }
    bankData=indexed;bankSize=next;
    assert(effectIndexValid(indexed,next));
    for(i=0;i<255;i++) {
        u32 descriptor=32+(u32)i*16;
        u16 id=le16(indexed+descriptor);
        const u8 *command;
#if CALLIOPE_HYBRID
        commandW=commandR=0;
#else
        mailbox[1]=mailbox[2]=0;
#endif
        assert(effectOffset(id)==descriptor && clp_playSfx(id,77,1234));
#if CALLIOPE_HYBRID
        command=commands[0];
#else
        command=(const u8 *)mailbox+64;
#endif
        assert(command[0]==((i&1)?4:9) && le16(command+2)==1234);
        assert(le24(command+4)==descriptor && command[7]==77);
        assert(clp_playPcm(id,0,9,1)==((i&1)!=0));
        assert(clp_playPcm(id,1,9,2)==((i&1)!=0));
        assert(!clp_playPcm(id,2,9,3));
    }
    assert(!effectOffset(65535) && !clp_playSfx(65535,1,1));
    assert(!clp_playSfx(1,1,1)); /* Missing ID in an occupied page. */
    assert(!clp_playSfx(0xfe00,1,1)); /* Missing high-byte page. */
    indexed[22]=indexed[23]=indexed[24]=0;
    assert(!effectIndexValid(indexed,next) && !clp_playSfx(0,1,1));
    indexPut24(indexed+22,4112);
    indexPut24(indexed+4112,next-255); /* Truncated page. */
    assert(!clp_playSfx(0,1,1));
    indexPut24(indexed+4112,4880); /* Page belongs to another ID. */
    indexed[4880]=1;
    assert(!clp_playSfx(0,1,1));
    bankSize=31;assert(!clp_playSfx(0,1,1));
    bankData=NULL;assert(!clp_playSfx(0,1,1));
}
