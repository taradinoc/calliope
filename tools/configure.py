"""Resolve one compile-time mode for C, Z80 assembly and PCM conversion."""

from toolchain import sgdk_path, sgdk_tool
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def write(path, text):
    path = Path(path)
    raw = text.replace("\r\n", "\n").replace("\n", "\r\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != raw:
        path.write_bytes(raw)


def resolve(sgdk, header, hybrid=None, host_pcm=None):
    header = Path(header).resolve()
    if not header.is_file():
        raise ValueError("configuration header not found: " + str(header))
    command = [
        str(sgdk_tool(sgdk, "gcc")),
        "-E",
        "-P",
        "-Wundef",
        "-Werror=undef",
        "-x",
        "c",
        "-B" + str(Path(sgdk) / "bin") + "/",
        "-I" + str(ROOT / "include"),
    ]
    if hybrid is not None:
        command.append("-DCALLIOPE_HYBRID=" + str(hybrid))
    if host_pcm is not None:
        command.append("-DCALLIOPE_HOST_PCM=" + str(host_pcm))
    command.append("-")
    source = '#include "' + header.as_posix() + '"\n#include "calliope_config.h"\n'
    source += "#if CALLIOPE_HYBRID\nCLP_HYBRID 1\n#else\nCLP_HYBRID 0\n#endif\n#if CALLIOPE_HOST_PCM\nCLP_HOST_PCM 1\n#else\nCLP_HOST_PCM 0\n#endif\nCLP_TIMER CALLIOPE_PCM_TIMER_A\n"
    result = subprocess.run(command, input=source, capture_output=True, text=True)
    if result.returncode:
        raise ValueError("invalid Calliope configuration:\n" + result.stderr.strip())
    fields = dict(
        re.findall(r"^CLP_(HYBRID|HOST_PCM|TIMER) (\d+)\s*$", result.stdout, re.M)
    )
    if len(fields) != 3:
        raise ValueError("configuration preprocessor returned incomplete settings")
    h, p, t = (int(fields[k]) for k in ("HYBRID", "HOST_PCM", "TIMER"))
    if hybrid is not None and h != hybrid:
        raise ValueError(
            "header overrides CALLIOPE_HYBRID; use a guarded #ifndef default"
        )
    if host_pcm is not None and p != host_pcm:
        raise ValueError(
            "header overrides CALLIOPE_HOST_PCM; use a guarded #ifndef default"
        )
    if t != (1020 if h else 1016):
        raise ValueError("PCM timer must follow CALLIOPE_HYBRID")
    return {
        "hybrid": bool(h),
        "host_pcm": bool(p),
        "pcm_timer": t,
        "config_header": str(header),
        "driver": "hybrid.s80" if p else ("hybrid_rom.s80" if h else "calliope.s80"),
    }


def prepare(sgdk, header, out, driver_asm, hybrid=None, host_pcm=None, make_writer=write):
    config = resolve(sgdk, header, hybrid, host_pcm)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    assembler = sgdk_tool(sgdk, "sjasm")
    sources = sorted(
        p for p in (ROOT / "driver").iterdir() if p.suffix in (".s80", ".i80")
    )
    digest = hashlib.sha256(
        config["driver"].encode()
        + str(assembler).encode()
        + assembler.read_bytes()
        + b"".join(p.name.encode() + p.read_bytes() for p in sources)
    ).hexdigest()
    stamp = out / "calliope_driver.cache.json"
    image = out / "driver.bin"
    listing = out / "driver.lst"
    try:
        cached = json.loads(stamp.read_text())
        hit = (
            isinstance(cached, dict)
            and cached.get("source") == digest
            and cached.get("image") == hashlib.sha256(image.read_bytes()).hexdigest()
            and cached.get("listing")
            == hashlib.sha256(listing.read_bytes()).hexdigest()
        )
    except (OSError, ValueError):
        hit = False
    if not hit:
        with tempfile.TemporaryDirectory(prefix="calliope-asm-", dir=out) as temporary:
            tmp = Path(temporary)
            result = subprocess.run(
                [
                    str(assembler),
                    "-i" + str(ROOT / "driver"),
                    str(ROOT / "driver" / config["driver"]),
                    str(tmp / "driver.bin"),
                    str(tmp / "driver.lst"),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode or not re.search(r"Errors: 0\b", result.stdout):
                raise ValueError(
                    "Z80 assembly failed:\n" + result.stdout + result.stderr
                )
            data = (tmp / "driver.bin").read_bytes()
            if len(data) != 8192:
                raise ValueError("Z80 image must be 8192 bytes")
            image.write_bytes(data)
            listing.write_bytes((tmp / "driver.lst").read_bytes())
        write(
            stamp,
            json.dumps(
                {
                    "source": digest,
                    "image": hashlib.sha256(image.read_bytes()).hexdigest(),
                    "listing": hashlib.sha256(listing.read_bytes()).hexdigest(),
                },
                sort_keys=True,
            )
            + "\n",
        )
    timer_source = (
        ROOT / "driver" / (config["driver"] if config["hybrid"] else "pcm.i80")
    )
    timer = re.search(r"PCM_TIMER_A EQU (\d+)", timer_source.read_text())
    if not timer or int(timer.group(1)) != config["pcm_timer"]:
        raise ValueError("driver/configuration PCM rate mismatch")
    text = listing.read_text()
    core = re.search(r"\b([0-9A-Fa-f]{4})\s+code_end:", text)
    if not core:
        raise ValueError("missing driver code bound")
    extension = 0
    if not config["hybrid"]:
        for begin, end in (
            ("extension_start", "extension_end"),
            ("control_extension_start", "control_extension_end"),
            ("read_extension_start", "read_extension_end"),
            ("tone_group_extension_start", "tone_group_extension_end"),
            ("pan_extension_start", "pan_extension_end"),
            ("pan_check_start", "pan_check_end"),
            ("pan_write_start", "pan_write_end"),
            ("silence_extension_start", "silence_extension_end"),
            ("level_extension_start", "level_extension_end"),
            ("pan_restore_start", "pan_restore_end"),
        ):
            first = re.search(r"\b([0-9A-Fa-f]{4})\s+" + begin + ":", text)
            last = re.search(r"\b([0-9A-Fa-f]{4})\s+" + end + ":", text)
            if not first or not last:
                raise ValueError("missing extension bounds")
            extension += int(last.group(1), 16) - int(first.group(1), 16)
    config.update(
        core_code_bytes=int(core.group(1), 16), extension_code_bytes=extension
    )
    config["code_bytes"] = config["core_code_bytes"] + extension
    if driver_asm:
        data = image.read_bytes()
        assembly = [
            ".section .rodata",
            ".balign 2",
            ".global clp_driver",
            "clp_driver:",
        ]
        assembly += [
            ".byte " + ",".join(map(str, data[i : i + 32]))
            for i in range(0, len(data), 32)
        ]
        write(driver_asm, "\n".join(assembly) + "\n")
    generated = [
        "/* Generated by Calliope. Change the input header or Makefile. */",
        "#ifndef CALLIOPE_BUILD_H",
        "#define CALLIOPE_BUILD_H",
    ]
    for name, value in (
        ("CALLIOPE_HYBRID", int(config["hybrid"])),
        ("CALLIOPE_HOST_PCM", int(config["host_pcm"])),
    ):
        generated += [
            "#ifdef " + name,
            "#if " + name + " != " + str(value),
            "#error " + name + " disagrees with the prepared driver and bank",
            "#endif",
            "#else",
            "#define " + name + " " + str(value),
            "#endif",
        ]
    generated += ['#include "calliope_config.h"', "#endif"]
    write(out / "calliope_build.h", "\n".join(generated) + "\n")
    write(out / "calliope_config.json", json.dumps(config, indent=2) + "\n")
    # Write the included makefile last. Changed mode always restarts Make, while
    # identical outputs retain timestamps and avoid an include-remake loop.
    make = [
        "# Generated; mode comes from the input header and Makefile overrides.",
        "override CALLIOPE_PCM_TIMER := " + str(config["pcm_timer"]),
        "CALLIOPE_RESOLVED_HYBRID := " + str(int(config["hybrid"])),
        "CALLIOPE_RESOLVED_HOST_PCM := " + str(int(config["host_pcm"])),
        "# Driver signature: " + digest,
    ]
    make_writer(out / "calliope_build.mk", "\n".join(make) + "\n")
    return config


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sgdk", type=Path, default=None)
    p.add_argument("--bank", type=Path)
    p.add_argument("--region", choices=("ntsc", "pal"), default="ntsc")
    p.add_argument("--symbol", default="clp_bank")
    p.add_argument(
        "--config-header", type=Path, default=ROOT / "include/calliope_config.h"
    )
    p.add_argument("--output-dir", type=Path, default=ROOT / "out")
    p.add_argument(
        "--source-wrappers",
        type=Path,
        help="generate consumer adapter, bus and protected SGDK DMA translation units",
    )
    p.add_argument("--driver-asm", type=Path)
    p.add_argument("--hybrid", type=int, choices=(0, 1))
    p.add_argument("--host-pcm", type=int, choices=(0, 1))
    a = p.parse_args()
    pending_make = {}
    try:
        config = prepare(
            sgdk_path(a.sgdk),
            a.config_header,
            a.output_dir,
            a.driver_asm,
            a.hybrid,
            a.host_pcm,
            make_writer=pending_make.__setitem__,
        )
    except (ValueError, OSError) as e:
        p.exit(1, str(e) + "\n")
    if a.bank:
        os.environ["CALLIOPE_PCM_TIMER"] = str(config["pcm_timer"])
        from consumer import prepare_bank

        try:
            prepare_bank(a.bank, a.output_dir, a.region, config["hybrid"], a.symbol)
        except (ValueError, OSError) as e:
            p.exit(1, str(e) + "\n")
        config["region"] = a.region
        write(
            a.output_dir / "calliope_config.json", json.dumps(config, indent=2) + "\n"
        )
    if a.source_wrappers:
        wrapper = a.source_wrappers
        write(
            wrapper / "adapter.c",
            '#include "' + (ROOT / "src/calliope.c").as_posix() + '"\n',
        )
        write(
            wrapper / "bus.s",
            '#include "' + (ROOT / "src/hybrid_bus.s").as_posix() + '"\n',
        )
        write(
            wrapper / "dma.c",
            '#include <config.h>\n#undef HALT_Z80_ON_DMA\n#define HALT_Z80_ON_DMA 0\n#include "'
            + (sgdk_path(a.sgdk) / "src/dma.c").as_posix()
            + '"\n',
        )
    # Publish the included Makefile only after all generated sources are ready.
    # Its signature must change for bank-only edits too, so Make drops cached
    # source timestamps before deciding what to rebuild.
    make_path, make_text = pending_make.popitem()
    if a.bank:
        signature = hashlib.sha256()
        for name in ("bank.s", "audio.h", "clp_ids.h"):
            signature.update((a.output_dir / name).read_bytes())
        make_text += "# Bank signature: " + signature.hexdigest() + "\n"
    write(make_path, make_text)
    print(
        "Calliope mode: "
        + ("hybrid" if config["hybrid"] else "Z80")
        + ", PCM timer "
        + str(config["pcm_timer"])
    )


if __name__ == "__main__":
    main()
