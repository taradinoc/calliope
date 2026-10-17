"""Standalone converter and rescomp process entrypoint."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from calliope.compiler import compile_bank, ids_header, load_manifest
from calliope.furnace import FormatError, parse
from calliope.pcm import TIMER_A, RATES


def same_contents(path, data):
    try:
        return path.read_bytes() == data
    except (FileNotFoundError, PermissionError):
        return False


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if same_contents(path, data):
        return
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                # Windows readers temporarily deny replacement; a concurrent
                # publisher may also have installed the same immutable result.
                if same_contents(path, data):
                    break
                if attempt == 19:
                    raise
                time.sleep(0.01)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def compile_cached(manifest, region, cache_dir=None, mode=None):
    """One immutable cache record per content key; publish only complete results."""
    manifest = Path(manifest).resolve()
    m = load_manifest(manifest)
    inputs = {manifest, Path(__file__).resolve()}
    inputs.update(p.resolve() for p in Path(__file__).parent.glob("calliope/*.py"))
    inputs.update(
        (manifest.parent / item["source"]).resolve()
        for category in ("songs", "sfx")
        for item in m.get(category, [])
    )
    hashes = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(inputs)
    }
    timer = (1020 if mode == "hybrid" else 1016) if mode is not None else TIMER_A
    key = hashlib.sha256(
        json.dumps(
            {"inputs": hashes, "region": region, "pcm_timer": timer}, sort_keys=True
        ).encode()
    ).hexdigest()
    cache = Path(cache_dir) / f"{key}.json" if cache_dir is not None else None
    if cache is not None and cache.exists():
        try:
            entry = json.loads(cache.read_text())
            payload = entry["payload"]
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()
            if entry["key"] == key and entry["sha256"] == digest:
                data = base64.b64decode(payload["bank"], validate=True)
                return data, payload["report"], payload["header"], True
        except (OSError, ValueError, KeyError, TypeError):
            pass  # A damaged cache is expendable; the source remains authoritative.
    data, report, header = compile_bank(manifest, region, pcm_timer=timer)
    if not set(report["dependencies"]).issubset(hashes):
        raise FormatError("converter dependency discovery changed; update cache inputs")
    if any(
        hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()
    ):
        raise FormatError("input changed during conversion; retry the build")
    report["pcm_timer"] = timer
    report["pcm_rate"] = (
        {"ntsc": 53693175, "pal": 53203424}[region] / 7 / (144 * (1024 - timer))
    )
    if mode is not None:
        report["driver_mode"] = mode
    report["dependencies"] = list(hashes)
    report["input_hashes"] = hashes
    if cache is not None:
        payload = {
            "bank": base64.b64encode(data).decode("ascii"),
            "report": report,
            "header": header,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        atomic(
            cache,
            (
                json.dumps(
                    {"key": key, "sha256": digest, "payload": payload}, sort_keys=True
                )
                + "\n"
            ).encode(),
        )
    return data, report, header, False


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    c = sub.add_parser("compile")
    c.add_argument("manifest", type=Path)
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--region", choices=("ntsc", "pal"), default="ntsc")
    c.add_argument("--cache-dir", type=Path)
    c.add_argument("--ids-header", type=Path)
    c.add_argument("--mode", choices=("z80", "hybrid"), default=None)
    h = sub.add_parser("ids")
    h.add_argument("manifest", type=Path)
    h.add_argument("--output", type=Path, required=True)
    i = sub.add_parser("inspect")
    i.add_argument("source", type=Path)
    i.add_argument("--subsong", type=int, default=0)
    i.add_argument("--region", choices=("ntsc", "pal"), default="ntsc")
    i.add_argument("--mode", choices=("z80", "hybrid"), default="z80")
    v = sub.add_parser("validate")
    v.add_argument("manifest", type=Path)
    v.add_argument("--region", choices=("ntsc", "pal"), default="ntsc")
    v.add_argument("--mode", choices=("z80", "hybrid"), default="z80")
    v.add_argument("--json", action="store_true", dest="json_output")
    a = p.parse_args()
    try:
        if a.action == "ids":
            if a.output.resolve() == a.manifest.resolve():
                raise FormatError("IDs output would overwrite the input manifest")
            atomic(
                a.output,
                ids_header(load_manifest(a.manifest)).replace("\n", "\r\n").encode(),
            )
            return 0
        if a.action == "inspect":
            from calliope.editor.services import inspect_source

            print(
                json.dumps(
                    inspect_source(
                        a.source, region=a.region, mode=a.mode, subsong=a.subsong
                    ),
                    indent=2,
                )
            )
            return 0
        if a.action == "validate":
            from calliope.editor.services import validate_document

            try:
                document = json.loads(a.manifest.read_text(encoding="utf-8"))
                diagnostics = validate_document(
                    document, a.manifest.resolve().parent, a.region, a.mode
                )
            except (ValueError, OSError) as e:
                from calliope.editor.services import diagnostic

                diagnostics = [diagnostic(e, "manifest.read")]
            if a.json_output:
                print(
                    json.dumps(
                        {"valid": not diagnostics, "diagnostics": diagnostics}, indent=2
                    )
                )
            else:
                for d in diagnostics:
                    print(
                        f"{d['severity']}: {d.get('resource') or 'bank'}: {d['message']}"
                    )
                if not diagnostics:
                    print("Calliope bank is compatible")
            return int(bool(diagnostics))
        outputs = [a.output.resolve()] + [
            a.output.with_suffix(suffix).resolve()
            for suffix in (".json", ".deps", ".h")
        ]
        if len(set(outputs)) != len(outputs):
            raise FormatError("bank and sidecar output paths must be distinct")
        if a.manifest.resolve() in outputs:
            raise FormatError(
                "output would overwrite the input manifest; use a separate output basename or directory"
            )
        data, report, header, hit = compile_cached(
            a.manifest, a.region, a.cache_dir, a.mode
        )
        if a.ids_header is not None and a.ids_header.read_text() != header:
            raise FormatError(
                "generated IDs differ from the prerequisite header; regenerate IDs before compiling consumers"
            )
        if set(outputs).intersection(Path(p).resolve() for p in report["dependencies"]):
            raise FormatError("output would overwrite a conversion input")
        # Outputs are staged per invocation. Rescomp sees them only on success.
        atomic(a.output, data)
        atomic(
            a.output.with_suffix(".json"),
            (json.dumps(report, indent=2) + "\n").replace("\n", "\r\n").encode(),
        )
        atomic(
            a.output.with_suffix(".deps"), "\n".join(report["dependencies"]).encode()
        )
        atomic(a.output.with_suffix(".h"), header.replace("\n", "\r\n").encode())
        print(
            f'BGM1: {len(data)} bytes, {report["songs"]} songs, {report["patches"]} patches; cache {"hit" if hit else "miss"}'
        )
        return 0
    except (FormatError, OSError, KeyError, TypeError, ValueError) as e:
        print(f"calliope: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
