# Atomic Z80 ROM bank selection — 2026-10-21

The pure Z80 read path and suspended PCM refill's bank restore now finish all
nine serial latch writes before software DAC service can deliver queued patches.
A queued patch can recursively read ROM; servicing it mid-selection restores
the software bank but corrupts the unfinished hardware selection. VInt DAC
service does not read ROM and remains enabled. Either latch span is under 200
Z80 clocks. No format or public ABI change is required.

`tests/test_bank_latch.py` covers all 512 initial latch states and 256 supported
bank bytes, reproduces nested-restore corruption, and checks both real spans.
A Bonk skull-transfer replay in ordinary BlastEm reported an invalid banked
read before the fix and no such warning after rebuilding. The two source
changes are `driver/calliope.s80` and `driver/pcm.i80`.

The complete Python suite ran 247 tests: 237 passed, three skipped, and seven
failed because the unrelated karaoke fixture file
`examples/karaoke/always_coca_cola_karaoke.fur` is missing. The latch tests pass.
Bonk's portable and cartridge checks are recorded in its corresponding
`docs/native-runtime-skull-arrival-2026-10-21.md` receipt.

An isolated old-driver replacement in the current Bonk cartridge was also clean
on that sequence. The serial-latch defect is established independently, but the
precise interleaving of the user's original fault remains unconfirmed.
