"""Build a directly converted Calliope bank and SGDK audition ROM."""

from toolchain import sgdk_path, sgdk_tool
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(args, cwd=ROOT, env=None):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True)


def write(path, text):
    raw = text.replace("\r\n", "\n").replace("\n", "\r\n").encode()
    if not path.exists() or path.read_bytes() != raw:
        path.write_bytes(raw)


def main(project=None, manifest_path=None):
    project = Path(project or ROOT / "audition").resolve()
    manifest_path = Path(manifest_path or ROOT / "examples/bank.json").resolve()
    out = ROOT / "out" if project == ROOT / "audition" else project / "out/calliope"
    p = argparse.ArgumentParser()
    p.add_argument("--sgdk", type=Path, default=None)
    p.add_argument("--region", choices=("ntsc", "pal"), default="ntsc")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--hybrid",
        action="store_const",
        const=1,
        dest="hybrid",
        help="override header/Makefile: 68000 sequencing, 13.32 kHz PCM",
    )
    mode.add_argument(
        "--pure-z80",
        action="store_const",
        const=0,
        dest="hybrid",
        help="override header/Makefile: Z80 sequencing, 6.66 kHz PCM",
    )
    pcm = p.add_mutually_exclusive_group()
    pcm.add_argument(
        "--host-pcm",
        action="store_const",
        const=1,
        dest="host_pcm",
        help="hybrid comparison: upload PCM on the 68000",
    )
    pcm.add_argument(
        "--z80-pcm",
        action="store_const",
        const=0,
        dest="host_pcm",
        help="hybrid: fetch PCM on the Z80",
    )
    p.add_argument(
        "--config-header",
        type=Path,
        help="consumer configuration header; defaults to Makefile/public header",
    )
    a = p.parse_args()
    try:
        a.sgdk = sgdk_path(a.sgdk)
    except ValueError as e:
        p.error(str(e))
    out.mkdir(parents=True, exist_ok=True)
    make_args = [
        "GDK=" + a.sgdk.as_posix(),
        "CALLIOPE_HOME=" + ROOT.as_posix(),
        "CALLIOPE_PYTHON=" + sys.executable,
        "CALLIOPE_BUILD_DIR=" + Path(os.path.relpath(out, project)).as_posix(),
    ]
    if a.hybrid is not None:
        make_args.append("CALLIOPE_HYBRID=" + str(a.hybrid))
    if a.host_pcm is not None:
        make_args.append("CALLIOPE_HOST_PCM=" + str(a.host_pcm))
    if a.config_header:
        make_args.append(
            "CALLIOPE_CONFIG_HEADER=" + a.config_header.resolve().as_posix()
        )
    make_args += ["CALLIOPE_BANK=" + manifest_path.as_posix()]
    # Player templates embed the adapter for private read-only telemetry.
    if project.parent.name == "editor-player":
        write(
            project / "src/clp_dma.c",
            '#include <config.h>\n#undef HALT_Z80_ON_DMA\n#define HALT_Z80_ON_DMA 0\n#include "'
            + (a.sgdk / "src/dma.c").as_posix()
            + '"\n',
        )
    run(
        [sgdk_tool(a.sgdk, "make"), "release"]
        + make_args
        + ["CALLIOPE_REGION=" + a.region],
        project,
    )
    config = json.loads((out / "calliope_config.json").read_text())
    data = (project / "out/rom.bin").read_bytes()
    report = dict(
        config, rom_sha256=hashlib.sha256(data).hexdigest(), rom_bytes=len(data)
    )
    (out / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Built", project / "out/rom.bin")


if __name__ == "__main__":
    main()
