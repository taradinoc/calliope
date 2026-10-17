/* Calliope modification: expose read-only Z80 RAM after an emulated frame.
 * No CPU, driver or chip execution paths are changed. */
#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif
extern unsigned char zram[8192];
EXPORT const unsigned char *clp_editor_zram(void) { return zram; }
