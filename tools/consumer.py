"""Prepare bank sources and verify a linked SGDK cartridge."""

import argparse
import json
from pathlib import Path
import re
from clpconvert import atomic, compile_cached


def prepare_bank(manifest, output, region, hybrid, symbol):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", symbol):
        raise ValueError("Bank symbol must be a C identifier")
    output = Path(output)
    data, report, header, _ = compile_cached(
        manifest, region, output / "cache", "hybrid" if hybrid else "z80"
    )
    outputs = {
        (output / name).resolve()
        for name in ("bank.bin", "bank.s", "audio.h", "clp_ids.h", "bank.json")
    }
    if outputs.intersection(Path(name).resolve() for name in report["dependencies"]):
        raise ValueError(
            "Generated bank outputs would overwrite an input; use a separate build directory"
        )
    assembly = [".section .rodata", ".balign 32768", ".global " + symbol, symbol + ":"]
    assembly += [
        ".byte " + ",".join(map(str, data[i : i + 32])) for i in range(0, len(data), 32)
    ]
    declarations = (
        "/* Generated; edit the manifest and rebuild. */\n#pragma once\n"
        "#include <genesis.h>\nextern const u8 "
        + symbol
        + "["
        + str(len(data))
        + "];\n"
    )
    for name, content in (
        ("bank.bin", data),
        ("bank.s", ("\n".join(assembly) + "\n").encode()),
        ("audio.h", declarations.encode()),
        ("clp_ids.h", header.encode()),
        ("bank.json", (json.dumps(report, indent=2) + "\n").encode()),
    ):
        atomic(output / name, content)


def finalize(rom, symbols, config, symbol):
    settings = json.loads(Path(config).read_text())
    match = re.search(
        r"^([0-9a-fA-F]+)\s+\w\s+" + re.escape(symbol) + r"$",
        Path(symbols).read_text(),
        re.M,
    )
    if not match:
        raise ValueError("Missing bank symbol: " + symbol)
    address = int(match[1], 16)
    path = Path(rom)
    data = bytearray(path.read_bytes())
    if address % 32768 or address + 32 > len(data) or len(data) > 0x400000:
        raise ValueError("Bank alignment or cartridge ROM bounds invalid")
    if data[address : address + 6] != b"BGM1\x0c\x00":
        raise ValueError("Linked bank revision mismatch")
    if (
        int.from_bytes(data[address + 19 : address + 21], "little")
        != settings["pcm_timer"]
    ):
        raise ValueError("Linked bank PCM timer mismatch")
    if data[address + 21] != int(settings["region"] == "pal"):
        raise ValueError("Linked bank region mismatch")
    # Compare the whole linked bank, not just its signature, to detect stale links.
    bank = (Path(config).parent / "bank.bin").read_bytes()
    if data[address : address + len(bank)] != bank:
        raise ValueError("Linked bank contents differ from the prepared bank")
    if len(data) % 2:
        raise ValueError("Cartridge ROM has odd length")
    data[0x1A4:0x1A8] = (len(data) - 1).to_bytes(4, "big")
    checksum = (
        sum(int.from_bytes(data[i : i + 2], "big") for i in range(0x200, len(data), 2))
        & 65535
    )
    data[0x18E:0x190] = checksum.to_bytes(2, "big")
    atomic(path, data)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rom", type=Path, required=True)
    p.add_argument("--symbols", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--symbol", default="clp_bank")
    a = p.parse_args()
    try:
        finalize(a.rom, a.symbols, a.config, a.symbol)
    except (ValueError, OSError) as error:
        p.exit(1, "calliope: " + str(error) + "\n")


if __name__ == "__main__":
    main()
