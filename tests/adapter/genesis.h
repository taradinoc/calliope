#ifndef TEST_GENESIS_H
#define TEST_GENESIS_H
#include <stdint.h>
#include <stdbool.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef volatile u8 vu8;
#define TRUE true
#define FALSE false
void SYS_disableInts(void); void SYS_enableInts(void); bool Z80_getAndRequestBus(bool wait);
void Z80_releaseBus(void); void Z80_requestBus(bool wait); void Z80_loadCustomDriver(const u8 *p,u16 n);
void Z80_useBusProtection(u16 p); void Z80_setForceDelayDMA(bool b);
bool SYS_isPAL(void); void SYS_doVBlankProcess(void);
#endif
