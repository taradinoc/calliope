# Current capabilities and limitations

Calliope supports BGM1 revision 12 banks with matching converter, adapter and
Z80 image. Shared channel patterns, sixteen logical voices, arrangement/group
controls, FM/PSG effects, PCM preemption and external-command events are
implemented. See authoring and API documentation for supported effects and limits.

## Playback acceptance

- PCM is one exclusive source, not a mixer. Accepted SFX replace music PCM;
  suppressed drum triggers and interrupted samples do not resume.
- Strict DAC drift/jitter acceptance remains open. Zero underruns alone does
  not establish sample-accurate scheduling or hitch-free playback.
- Pure Z80 PAL full-song DMA/SFX stress previously reached 21325 ticks against
  a 21420 minimum over 18000 video frames. The gate remains an acceptance failure.
- Pure Z80 NTSC hitch measurements have exceeded the 150-stall gate (172 in a
  recorded 6000-frame workload). Workload and driver changes require fresh evidence.
- Revision-12 hybrid recorded zero underruns in NTSC/PAL stress, at approximately
  16.15% / 15.79% of a 68000 frame for audio service. Control latency, strict DAC
  jitter and fresh-patch onset alignment remain open.
- Physical hardware acceptance is outstanding. Linux/macOS source builds and
  standalone editor packaging have not been natively verified.

These are workload-specific historical measurements, not guarantees for a game.
Run the [validation commands](maintenance.md) against the integrated consumer.
The release-preparation changes do not claim to close runtime timing gates.

## Compatibility

Banks, converter and driver must be updated together. The current boundary API
uses immediate, next-bar and explicit-marker requests. Older bank revisions and
stale effect-index data are rejected. Runtime macros and arbitrary phrase calls
are not implemented; supported modulation is compiled into timed updates.

The Make integration now offers direct Python bank preparation, enabled with
`CALLIOPE_BANK`; the configuration-only/rescomp route remains available. See
[integration and migration](integration.md) before changing an existing project.
