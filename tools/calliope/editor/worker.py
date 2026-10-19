"""JSON-lines subprocess protocol for compilation, inspection and playback."""

import base64
import json
from pathlib import Path
import queue
import sys
import threading
import time
from .services import (
    cached_compile,
    export_bank,
    fingerprint,
    inspect_source,
    validate_document,
)


def send(value):
    # ASCII JSON is valid UTF-8 even when a Windows frozen process inherits a
    # legacy console encoding. UI strings are decoded back to Unicode by JSON.
    print(json.dumps(value, ensure_ascii=True), flush=True)


def run():
    request = json.loads(sys.stdin.readline())
    revision = request.get("revision", 0)
    player = None
    try:
        action = request["action"]

        def progress(message):
            send({"type": "progress", "revision": revision, "message": message})

        progress(
            "Inspecting resource…" if action == "inspect" else "Checking bank inputs…"
        )
        if action == "inspect":
            result = inspect_source(
                request["source"],
                request["region"],
                request["mode"],
                request.get("subsong", 0),
                request.get("gain", 1),
                request.get("loop"),
            )
            send({"type": "inspection", "revision": revision, "result": result})
            return
        doc = request["document"]
        base = request["base_dir"]
        region = request["region"]
        mode = request["mode"]
        if action == "validate":
            progress("Validating resources against Calliope requirements…")
            diagnostics = validate_document(doc, base, region, mode)
            if diagnostics:
                send(
                    {
                        "type": "diagnostics",
                        "revision": revision,
                        "diagnostics": diagnostics,
                    }
                )
                return
            key = fingerprint(doc, base, region, mode)
            send(
                {
                    "type": "validated",
                    "revision": revision,
                    "fingerprint": key,
                    "diagnostics": [],
                }
            )
            return
        try:
            data, report, header, hit = cached_compile(
                doc,
                base,
                region,
                mode,
                request.get("cache_dir"),
                lambda category, index, total, name: progress(
                    f"Compiling {'music' if category=='songs' else 'SFX'} {index+1}/{total}: {name}…"
                ),
            )
        except Exception:
            progress("Collecting validation details…")
            diagnostics = validate_document(doc, base, region, mode)
            if diagnostics:
                send(
                    {
                        "type": "diagnostics",
                        "revision": revision,
                        "diagnostics": diagnostics,
                    }
                )
                return
            raise
        key = fingerprint(doc, base, region, mode)
        progress("Using cached bank…" if hit else "Bank compiled successfully…")
        if fingerprint(doc, base, region, mode) != key:
            raise ValueError("Inputs changed while compiling; retry")
        if action == "samples":
            send({"type": "samples", "revision": revision, "report": report})
            return
        if action == "export":
            export_bank(
                request["output"], data, report, header, request.get("document_path")
            )
            send(
                {
                    "type": "exported",
                    "revision": revision,
                    "fingerprint": key,
                    "report": report,
                }
            )
            return
        if action != "preview":
            raise ValueError("Unknown worker action")
        from .preview import make_rom, Player, core_path

        progress("Starting Genesis player…")
        raw, meta = make_rom(data, request["assets"], region, mode)
        player = Player(core_path(request["assets"]), raw, meta)
        player.write(24, b"\x01")
        send(
            {
                "type": "ready",
                "revision": revision,
                "fingerprint": key,
                "rate": player.sample_rate,
                "fps": player.fps,
                "report": report,
                "cached": hit,
            }
        )
        commands = queue.Queue(maxsize=128)

        def read_commands():
            try:
                for line in sys.stdin:
                    commands.put(json.loads(line))
            finally:
                commands.put({"quit": True})

        threading.Thread(target=read_commands, daemon=True).start()
        next_frame = time.perf_counter()
        frame = 0
        observations = []
        while True:
            if player.read(8, 2) == player.read(10, 2):
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    command = None
                if command:
                    if command.get("quit"):
                        break
                    args = command["command"]
                    sequence = player.command(*args)
                    if args[0] in (6, 7):
                        observations.append(
                            dict(
                                token=args[3],
                                sequence=sequence,
                                age=0,
                                before=player.status(),
                                pcm=args[0] == 7,
                            )
                        )
            audio = player.frame()
            if observations:
                status = player.status()
                for obs in observations[:]:
                    obs["age"] += 1
                    token = obs["token"]
                    active = (
                        (status["pcm_owner"] == 2 and status["pcm_token"] == token)
                        if obs["pcm"]
                        else any(
                            e["active"] and e["token"] == token
                            for e in status["effects"]
                        )
                    )
                    if active:
                        old = {
                            e["token"] for e in obs["before"]["effects"] if e["active"]
                        }
                        current = {e["token"] for e in status["effects"] if e["active"]}
                        removed = old - current
                        text = f"Token {token}: allocated" + (
                            f"; replaced tokens {sorted(removed)}" if removed else ""
                        )
                        send({"type": "allocation", "message": text})
                        observations.remove(obs)
                    elif status["ack"] == obs["sequence"] and not status["queued"]:
                        send(
                            {
                                "type": "allocation",
                                "message": f"Token {token}: rejected by adapter",
                            }
                        )
                        observations.remove(obs)
                    elif obs["age"] >= 12:
                        send(
                            {
                                "type": "allocation",
                                "message": f"Token {token}: no active instance observed (arbitration rejection or very short effect)",
                            }
                        )
                        observations.remove(obs)
            value = {"type": "audio", "data": base64.b64encode(audio).decode()}
            if not frame % 6:
                value["status"] = player.status()
            send(value)
            frame += 1
            next_frame += 1 / player.fps
            delay = next_frame - time.perf_counter()
            if delay > 0:
                time.sleep(min(delay, 0.05))
            elif delay < -0.25:
                next_frame = time.perf_counter()
    except Exception as e:
        send({"type": "error", "revision": revision, "message": str(e)})
    finally:
        if player:
            player.close()
