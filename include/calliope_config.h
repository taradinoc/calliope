#ifndef CALLIOPE_CONFIG_H
#define CALLIOPE_CONFIG_H
/* Override with -D in the build, or change these guarded project defaults.
   Every Calliope translation unit and the bank converter must use one mode. */
#ifndef CALLIOPE_HYBRID
#define CALLIOPE_HYBRID 0
#endif
#ifndef CALLIOPE_HOST_PCM
#define CALLIOPE_HOST_PCM 0
#endif
#if (CALLIOPE_HYBRID != 0 && CALLIOPE_HYBRID != 1)
#error CALLIOPE_HYBRID must be 0 or 1
#endif
#if (CALLIOPE_HOST_PCM != 0 && CALLIOPE_HOST_PCM != 1)
#error CALLIOPE_HOST_PCM must be 0 or 1
#endif
#if CALLIOPE_HOST_PCM && !CALLIOPE_HYBRID
#error CALLIOPE_HOST_PCM requires CALLIOPE_HYBRID
#endif
#if CALLIOPE_HYBRID
#define CALLIOPE_PCM_TIMER_A 1020
#else
#define CALLIOPE_PCM_TIMER_A 1016
#endif
#endif
