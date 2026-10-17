"""Shared, read-only inspection and authoritative bank validation services."""

from dataclasses import asdict, dataclass
from pathlib import Path
import copy
import hashlib
import json
import re
import base64
import sys
from ..compiler import compile_bank, infer_sfx, used_channels, validate_manifest
from ..furnace import FormatError, parse
from ..pcm import convert, decode

ERRORS = (
    FormatError,
    OSError,
    KeyError,
    TypeError,
    ValueError,
    IndexError,
    AttributeError,
    OverflowError,
)


def json_value(value):
    if isinstance(value, bytes):
        return {"hex": value.hex(), "bytes": len(value)}
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


@dataclass
class Diagnostic:
    severity: str
    code: str
    message: str
    resource: str | None = None
    field: str | None = None
    source: str | None = None
    subsong: int | None = None
    channel: int | None = None
    order: int | None = None
    pattern: int | None = None
    row: int | None = None


def diagnostic(error, code, item=None, field=None, base_dir=None):
    item = item or {}
    source = (
        str((Path(base_dir) / item["source"]).resolve())
        if base_dir and isinstance(item.get("source"), str)
        else None
    )
    d = Diagnostic(
        "error",
        code,
        str(error),
        item.get("name"),
        field,
        source,
        item.get("subsong", 0) if source else None,
    )
    for key in ("channel", "order", "pattern", "row"):
        match = re.search(r"\b" + key + r"\s*[=:]?\s*(\d+)", str(error), re.I)
        if match:
            setattr(d, key, int(match[1]))
    if source and d.order is not None and d.channel is not None:
        try:
            d.pattern = parse(source).subsongs[d.subsong].orders[d.channel][d.order]
        except ERRORS:
            pass
    return asdict(d)


def input_hashes(document, base_dir):
    paths = sorted(
        {
            str((Path(base_dir) / i["source"]).resolve())
            for cat in ("songs", "sfx")
            for i in document.get(cat, [])
            if isinstance(i, dict) and isinstance(i.get("source"), str)
        }
    )
    return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


def fingerprint(document, base_dir, region, mode):
    return hashlib.sha256(
        json.dumps(
            [
                document,
                str(Path(base_dir).resolve()),
                region,
                mode,
                input_hashes(document, base_dir),
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def inspect_source(path, region="ntsc", mode="z80", subsong=0, gain=1, loop=None):
    path = Path(path)
    timer = 1020 if mode == "hybrid" else 1016
    if path.suffix.lower() == ".wav":
        samples, rate, info = decode(path)
        _, converted = convert(path, region, gain, loop, pcm_timer=timer)
        return {
            "kind": "wav",
            "source_rate": rate,
            "duration": len(samples) / rate,
            "source": info,
            "conversion": converted,
        }
    module = parse(path)
    if not 0 <= subsong < len(module.subsongs):
        raise FormatError("invalid subsong")
    result = {
        "kind": "furnace",
        "version": module.version,
        "name": module.name,
        "channels": module.channels,
        "tuning": module.tuning,
        "subsongs": [
            dict(asdict(s), active_channels=used_channels(module, n))
            for n, s in enumerate(module.subsongs)
        ],
        "instruments": module.instruments,
        "samples": [
            {k: v for k, v in s.items() if k != "data"} for s in module.samples
        ],
        "flags": module.flags,
        "patterns": [],
        "effects": [],
        "external_commands": [],
        "parsed": True,
        "compatibility": "Run validation with a bank entry to check Calliope compatibility.",
    }
    effects = set()
    for (ss, ch, pat), cells in module.patterns.items():
        for row, cell in sorted(cells.items()):
            if not cell.active():
                continue
            result["patterns"].append(
                dict(subsong=ss, channel=ch, pattern=pat, row=row, **asdict(cell))
            )
            for column, effect, value in cell.effects:
                effects.add(effect)
                if effect == 0xEE:
                    result["external_commands"].append(
                        dict(subsong=ss, channel=ch, pattern=pat, row=row, value=value)
                    )
    result["effects"] = sorted(e for e in effects if e is not None)
    try:
        kind, channels = infer_sfx(module, subsong)
        result["sfx_suggestion"] = {"kind": kind, "channels": channels}
    except FormatError as e:
        result["sfx_suggestion"] = {"error": str(e)}
    sub = module.subsongs[subsong]
    slots = {"fm": "fm", "psg": "tone", "noise": "noise"}
    result["voice_suggestions"] = [
        dict(
            name=sub.names[ch] or f"voice_{ch}",
            channel=ch,
            slot=(
                "pcm"
                if kind == "fm" and slot == 5
                else "noise" if kind == "noise" else slots[kind] + str(slot)
            ),
        )
        for ch in used_channels(module, subsong)
        for _, kind, slot in [module.channels[ch]]
    ]
    return json_value(result)


def validate_document(document, base_dir, region="ntsc", mode="z80"):
    diagnostics = []
    if mode not in ("z80", "hybrid") or region not in ("ntsc", "pal"):
        return [diagnostic("Unknown playback profile", "profile.invalid")]
    try:
        validate_manifest(document)
    except ERRORS as e:
        diagnostics.append(diagnostic(e, "manifest.invalid"))
        # Independent entries can still provide useful conversion errors.
        if not isinstance(document, dict) or any(
            not isinstance(document.get(c, []), list) for c in ("songs", "sfx")
        ):
            return diagnostics
    timer = 1020 if mode == "hybrid" else 1016
    for cat in ("songs", "sfx"):
        for index, item in enumerate(document.get(cat, [])):
            if not isinstance(item, dict):
                continue
            mini = {
                k: copy.deepcopy(v)
                for k, v in document.items()
                if k not in ("songs", "sfx")
            }
            mini[cat] = [copy.deepcopy(item)]
            try:
                compile_bank(mini, region, base_dir=base_dir, pcm_timer=timer)
            except ERRORS as e:
                diagnostics.append(
                    diagnostic(
                        e, "resource.incompatible", item, f"{cat}[{index}]", base_dir
                    )
                )
    if not diagnostics:
        try:
            compile_bank(
                copy.deepcopy(document), region, base_dir=base_dir, pcm_timer=timer
            )
        except ERRORS as e:
            diagnostics.append(diagnostic(e, "bank.incompatible"))
    return diagnostics


def compile_document(document, base_dir, region="ntsc", mode="z80", *, progress=None):
    if mode not in ("z80", "hybrid"):
        raise FormatError("invalid driver mode")
    before = input_hashes(document, base_dir)
    data, report, header = compile_bank(
        copy.deepcopy(document),
        region,
        base_dir=base_dir,
        pcm_timer=1020 if mode == "hybrid" else 1016,
        progress=progress,
    )
    if before != input_hashes(document, base_dir):
        raise FormatError("Input changed during conversion; retry")
    report.update(
        input_hashes=before,
        driver_mode=mode,
        pcm_timer=int.from_bytes(data[19:21], "little"),
    )
    return data, report, header


def cached_compile(document, base_dir, region, mode, cache_dir=None, progress=None):
    """Content-addressed editor cache; failed/partial conversions are never reused."""
    key = fingerprint(document, base_dir, region, mode)
    compiler_dir = Path(__file__).resolve().parents[1]
    sources = sorted(compiler_dir.glob("*.py")) + sorted(
        Path(__file__).resolve().parent.glob("*.py")
    )
    implementation = hashlib.sha256(
        b"editor-cache-2" + b"".join(p.read_bytes() for p in sources)
    ).hexdigest()
    if not sources:
        executable = Path(sys.executable)
        implementation = hashlib.sha256(executable.read_bytes()).hexdigest()
    cache_key = hashlib.sha256((key + implementation).encode()).hexdigest()
    cache = Path(cache_dir) / (cache_key + ".json") if cache_dir else None
    if cache and cache.exists():
        try:
            value = json.loads(cache.read_text(encoding="utf-8"))
            data = base64.b64decode(value["bank"], validate=True)
            if (
                value["key"] == cache_key
                and hashlib.sha256(data).hexdigest() == value["report"]["sha256"]
            ):
                return data, value["report"], value["header"], True
        except (OSError, ValueError, KeyError, TypeError):
            pass
    data, report, header = compile_document(
        document, base_dir, region, mode, progress=progress
    )
    if fingerprint(document, base_dir, region, mode) != key:
        raise FormatError("Inputs changed during conversion; retry")
    if cache:
        from clpconvert import atomic

        atomic(
            cache,
            (
                json.dumps(
                    dict(
                        key=cache_key,
                        bank=base64.b64encode(data).decode(),
                        report=report,
                        header=header,
                    )
                )
                + "\n"
            ).encode(),
        )
    return data, report, header, False


def export_bank(output, data, report, header, document_path=None):
    from clpconvert import atomic

    output = Path(output).resolve()
    outputs = [output] + [output.with_suffix(s) for s in (".json", ".deps", ".h")]
    protected = {Path(p).resolve() for p in report["dependencies"]}
    if document_path:
        protected.add(Path(document_path).resolve())
    if len(set(outputs)) != 4 or protected.intersection(outputs):
        raise FormatError("Output paths collide with each other or a source input")
    for path, value in zip(
        outputs,
        [
            data,
            (json.dumps(report, indent=2) + "\n").encode(),
            ("\n".join(report["dependencies"]) + "\n").encode(),
            header.encode(),
        ],
    ):
        atomic(path, value)
