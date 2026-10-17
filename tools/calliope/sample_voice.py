"""Genesis DAC note selection, including format-232 legacy sample banks.
Legacy mapping follows Furnace legacySample.cpp; SMP2's first rate is legacyRate.
"""

from .furnace import FormatError


class SampleVoice:
    def __init__(self, module):
        self.module = module
        self.mode = 0
        self.bank = 0

    def begin(self, cell, instrument):
        disabled = False
        for _, effect, value in cell.effects:
            if effect not in (0x17, 0xEB):
                continue
            if self.module.version != 232:
                raise FormatError("legacy DAC effects require Furnace format 232")
            value = value or 0
            if effect == 0xEB:
                self.bank = value
            else:
                self.mode = 1 if value else 0
                disabled = not value
        if (
            cell.instrument is not None
            and instrument is not None
            and instrument["kind"] == 4
        ):
            self.mode = 2
        return disabled

    def select(self, note, instrument):
        if self.mode == 1:
            index = self.bank * 12 + note % 12
            if index >= len(self.module.samples):
                raise FormatError("legacy sample bank/note selects a missing sample")
            return index, self.module.samples[index]["rate"]
        if self.mode != 2 or instrument is None or instrument["kind"] != 4:
            raise FormatError(
                "DAC requires a sample instrument or enabled legacy sample mode"
            )
        if set(instrument["features"]) - {"NA", "SM", "NE"}:
            raise FormatError("unsupported sample instrument feature")
        sm = instrument["features"].get("SM", bytes((0, 0, 0, 31)))
        if len(sm) != 4 or sm[2] & 5:
            raise FormatError("sample maps/wavetables not yet supported")
        index = int.from_bytes(sm[:2], "little")
        if index >= len(self.module.samples):
            raise FormatError("invalid sample index")
        return index, self.module.samples[index]["center_rate"] * 2 ** (
            (note - 108) / 12
        )

    def validate_loop(self, initial, cells):
        mode_changed = self.mode != initial[0]
        bank_changed = self.bank != initial[1]
        mode = initial[0]
        for cell in cells:
            for _, effect, value in cell.effects:
                if effect == 0x17:
                    mode = 1 if value else 0
                    mode_changed = False
                elif effect == 0xEB:
                    bank_changed = False
            if (
                cell.instrument is not None
                and self.module.instruments[cell.instrument]["kind"] == 4
            ):
                mode = 2
                mode_changed = False
            if cell.note is not None and cell.note < 180:
                if mode_changed:
                    raise FormatError(
                        "PCM loop must reset changed sample mode before a note"
                    )
                if mode == 1 and bank_changed:
                    raise FormatError(
                        "PCM loop must reset changed sample bank before a legacy note"
                    )
