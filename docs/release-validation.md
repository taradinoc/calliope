# Release candidate validation

Validated on Windows x64 on October 15, 2026. These results cover release
preparation; they do not close the runtime timing and physical hardware limits
in `status.md`.

| Check | Result |
|---|---|
| Python converter/reference/build/editor suite | 222 tests passed, no skips with SGDK and Furnace configured |
| MSVC adapter checks | Pure Z80, hybrid ROM PCM and hybrid host PCM passed |
| External consumer | NTSC/PAL x pure Z80/hybrid passed |
| Vendored consumer | Same four combinations passed |
| Relocated source export | Same four combinations passed |
| Consumer incremental behavior | Unchanged object timestamps, mode/region switches without cleaning, asset edits and regenerated IDs passed |
| Existing SGDK resources | Ordinary BIN resource coexists with generated bank |
| Python executable path containing spaces | Passed in consumer checks |
| Library path containing spaces | GNU Make splits the include path; unsupported and documented |
| NTSC audition | 18000 frames with DMA/SFX: zero faults/underruns, guards intact, 17878 music ticks |
| Pinned-source trace core | Built successfully using the public pinned checkout and local instrumentation |
| PCM ownership regression | PSG allocation, equal-priority effects, music PCM and priorities passed with zero faults/underruns |
| Windows player templates | All four rebuilt through direct-bank integration |
| Packaged bank-byte parity | All four profiles match the source converter |
| Packaged preview | All four profiles produce audio; no worker stderr |
| Packaged pitched-audio regression | Passed with PCM muted |
| GUI integration | Offscreen GUI with packaged worker passed; Qt worker pipes require running outside the restricted sandbox |
| Frozen GUI launch | Started outside checkout with development variables removed; no startup stderr |
| Karaoke | Built; all 113 lyric contacts, highlighting, scrolling, stop/replay passed |

The editor was built with Python 3.10, PySide6 6.12.0 and Nuitka 4.1.1.
The archive inventory records exact dependency versions and file checksums.
The initial packaging attempts hit a shared compiler-cache lock and a blocked
dependency download; a workspace-local cache with the existing cached dependency
allowed a clean build. No stale standalone package was used as the candidate.

No publication was performed. Pending asset permissions, adapted harness terms,
and exact Qt/third-party source and redistribution review remain blockers in
`releasing.md`. Native Linux/macOS and physical Genesis hardware were not tested.
