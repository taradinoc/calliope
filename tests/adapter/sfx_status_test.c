/* Reuse the real hybrid sequencer and host fixtures without unrelated bank hashes. */
#define main full_hybrid_test_main
#include "hybrid_test.c"
#undef main
int main(void) {
    multi_fm();tone_groups();pcmPriorities();sfx_active_query();
    assert(!owned && !disabled);
    puts("hybrid SFX token status, FM/tone ownership and PCM checks passed");
    return 0;
}
