/* Editor-only, observational telemetry. Cycles use the core's master clock. */
#include <stdint.h>
#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif
extern unsigned char zram[8192];
EXPORT const unsigned char *clp_editor_zram(void) { return zram; }

extern unsigned char work_ram[65536];
static unsigned int wait_address;
static int profiling;
static uint64_t busy_cycles;

EXPORT void clp_editor_cpu_begin(unsigned int address)
{
    wait_address = address & 65535;
    profiling = 1;
    busy_cycles = 0;
}

int clp_editor_cpu_busy(void)
{
#ifdef LSB_FIRST
    return profiling && !work_ram[wait_address ^ 1];
#else
    return profiling && !work_ram[wait_address];
#endif
}

void clp_editor_cpu_add(unsigned int cycles) { busy_cycles += cycles; }
EXPORT uint64_t clp_editor_cpu_cycles(void) { return busy_cycles; }
