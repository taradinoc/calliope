# Runtime status and tempo controls

Elapsed queries and tempo scaling are implemented. The channel telemetry APIs
below remain proposals.

## Channel usage

A public snapshot comparable to the editor is feasible with modest work. The
editor currently reads private Z80 RAM in `tools/calliope/editor/preview.py`,
and the hybrid player exports host state through its debug mailbox. These are
implementation details, not a stable game API.

The pure Z80 driver has nine FM/PSG ownership bytes at 1F20h..1F28h, four
FM/PSG effect records at 1800h..183Fh (priority and token), logical music
part enable/state records, and separate PCM music/SFX state. Owner zero alone
does not distinguish silent hardware from music. A useful public record should
include physical slot, owner kind (idle/music/SFX), logical part, gate/active
state, token, and effect ID. Tone-derived noise reserves both tone 3 and noise;
the snapshot must represent this coupling, even when the tone output is silent.
Physical muting, pending fresh-note entry, disabled parts, paused music, and
PCM FIFO tails must be distinguished from an active logical note.

Recommendation: add `clp_getChannels` returning ten fixed physical records
(FM1..5, PSG1..3, noise, PCM), with generation and music tick in the snapshot.
Keep logical-part status separate if all sixteen alternative parts are needed.
Do not extend the cue queue with per-frame status messages. Stage bounded byte
copies under BUSREQ, then decode after releasing it; avoid one long bus hold
for the entire snapshot. A sequence counter or double buffer is needed to
prevent partial updates across bounded reads. Measure DAC deadlines and RAM
cost before choosing the publication layout. Hybrid must publish at consumed
transport deadlines; reading host `voices`/`owners` directly shows future state.

A first example could draw ten rows of owner, part, effect ID/token and active
state and refresh once per VBlank. This demonstrates current usage; effects
that start and finish between snapshots will not be displayed. Existing editor
telemetry has the same observation limitation. Keep this example separate from
the piano display so the basic API stays small.

## Notes and effect identity

Live gates and pitches are feasible, but cost more than ownership. Music is
compiled to YM frequency/block or PSG periods, with sparse pitch automation.
Original note IDs are not retained in the runtime stream. FM logical voices
retain frequency and gate, while PSG needs explicit active-state tracking if
volume-zero/released notes must differ from a note-off. Physical state must
track writes actually accepted after ownership and mute checks, rather than
just the logical alternative's current note. An FM key-off can leave an audible
release tail; a gate flag cannot describe actual acoustic silence.

For a piano display, expose raw hardware pitch plus gate first. The game can
map pitch to a nearest key using region/tuning data; slides and noise should
have an explicit display policy. Exact authored note numbers require converter
metadata/opcodes and a bank revision, and still need rules for arpeggio,
portamento and detuning. PCM can expose sample/effect identity but has no
universal piano note.

Effect tokens already exist, but they are caller-chosen and are not effect IDs.
Commands carry descriptor offsets. Retain the resolved ID in effect/PCM owner
state at admission, including every channel in multi-channel/coupled effects;
rejected requests must never appear active. Using a game-side token-to-ID map
is possible for a prototype, provided tokens are unique among live requests.

Frame snapshots suffice for a live keyboard view with that sampling limit.
If every short note-on/off matters, add a separate bounded event ring with
timestamp, generation, channel, pitch and identity, plus overflow accounting
and snapshot resynchronization. Size and benchmark it against dense macros;
pitch changes on every tick should not flood the existing authored cue queue.
Acceptance needs both regions, all driver modes, stealing, stale generations,
pause/stop, release tails, PCM preemption, coupled resources and overflow.

## Runtime tempo (implemented)

Feasible for sequenced FM/PSG and PCM trigger timing. `driver/timing.i80` uses
fifth-Hz music credit against an independent nominal 60 Hz SFX clock. Opcode
34h replaces the music tick rate and resets fractional credit. Hybrid has the
same rate/credit model in `src/hybrid.c`. Row speeds/grooves are expanded by the
converter, so changing tick rate scales rows, effects and macro progression
together. It does not resample PCM: sample pitch and duration stay fixed,
which can change the alignment of sustained PCM with the score. SFX timing
should keep its independent clock.

`clp_setTempoScale(u16 scale, ClpBoundary boundary, u16 marker)` queues an
8.8 multiplier over the authored rate. `CLP_TEMPO_NORMAL` is 256; the supported
range is `CLP_TEMPO_MIN` (128, 0.5x) through `CLP_TEMPO_MAX` (512, 2x).
The effective rate is `(authored_rate5 * scale + 128) >> 8`, rounded to the
nearest 0.2 Hz. For example, 25 Hz at half speed becomes 12.6 Hz. Maximum
rate is 240 Hz, or four music ticks per nominal 60 Hz scheduling slice.
The Z80 caches that rate when scale or authored rate changes, with DAC polls
inside its bounded multiply. Hybrid uses a wide intermediate for the same rule.

The API uses the same `CLP_IMMEDIATE`, `CLP_NEXT_BAR` and `CLP_NEXT_MARKER`
boundaries as parts. Immediate means command consumption, including while
paused. Bars follow the Furnace major highlight; zero highlight disables them.
Markers must occur in the compiled conductor. Deferred requests wait through
pause and apply when that boundary next occurs. The boundary tick has already
begun; the updated rate supplies credit starting with the next scheduling slice.

Tempo has one independent pending request. The latest consumed tempo request
replaces it; an immediate request cancels a deferred one. Part/arrangement/group
controls do not cancel tempo requests. Both requests can apply at the same
conductor boundary. Music stop cancels a deferred tempo request. A consumed
song start resets the scale to 256 and clears pending tempo. Authored rate
changes retain the scale and preserve their existing fractional-credit reset.
Runtime scale changes preserve accumulated musical credit instead of resetting
phase. Elapsed music ticks and authored cues retain their existing units.

Requests are scoped to the latest accepted song generation. Invalid scales,
invalid boundaries, requests before a song has been accepted, and full queues
return false. A true return means queued, not already applied. A missing marker
or disabled bar boundary leaves a deferred request pending until superseded,
stopped or replaced by a song start. No bank format change is required; rebuild
the adapter and driver together.

Hybrid applies changes at its future preparation frontier. Already queued chip
writes and PCM remain committed, so immediate changes have the existing audio
lookahead latency. It does not flush/rebuild transport or resample PCM. Higher
rates consume more decoder/transport budget; existing workload limits still
apply. The unchanged authored-cue timestamps describe score position, not elapsed
wall time at the scaled tempo.

Validation includes all three adapter modes; independent and simultaneous part/
tempo boundaries; stale requests, pause, stop and song replacement; rounded-rate
arithmetic at every supported scale and both authored rate extremes; separate
SFX timing; and PCM-heavy maximum-scale playback in NTSC/PAL. Run
`python tools/tempo_smoke.py --frames 6000` after an NTSC audition build, and
add `--pal` after a PAL build. The smoke test also checks stack guards, faults
and underruns. These checks do not replace the existing strict DAC timing gates.
