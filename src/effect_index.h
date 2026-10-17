/* BGM1-12 extension: high-byte directory -> low-byte descriptor ordinal.
   Zero pointers/ordinals denote missing IDs. Kept in ROM, shared by adapters. */
static bool effectIndexValid(const u8 *bank, u32 length)
{
    u32 index = le24(bank + 22), descriptors = le24(bank + 13);
    return descriptors + (u32)bank[7] * 16 <= length &&
           (!bank[7] || (index >= 32 && index + 768 <= length));
}
static u32 effectOffset(u16 effect)
{
    u32 index, page, offset;
    u8 ordinal;
    if (!bankData || bankSize < 32 || !bankData[7])
        return 0;
    index = le24(bankData + 22);
    if (index < 32 || index + 768 > bankSize)
        return 0;
    page = le24(bankData + index + (effect >> 8) * 3);
    if (page < 32 || page + 256 > bankSize)
        return 0;
    ordinal = bankData[page + (effect & 255)];
    if (!ordinal || ordinal > bankData[7])
        return 0;
    offset = le24(bankData + 13) + (u16)(ordinal - 1) * 16;
    if (offset < 32 || offset + 16 > bankSize || le16(bankData + offset) != effect)
        return 0;
    return offset;
}
