"""Deterministic integer-PCM WAV decoding and windowed-sinc resampling."""

from pathlib import Path
import math
import os
import struct
import wave
from .furnace import FormatError

TIMER_A = int(os.environ.get("CALLIOPE_PCM_TIMER", "1016"))
if TIMER_A not in (1016, 1017, 1018, 1019, 1020):
    raise ValueError("unsupported CALLIOPE_PCM_TIMER")
DAC_CLOCKS = 144 * (1024 - TIMER_A)
RATES = {"ntsc": 53693175 / 7 / DAC_CLOCKS, "pal": 53203424 / 7 / DAC_CLOCKS}


def decode(path):
    try:
        with wave.open(str(path), "rb") as w:
            channels, width, rate, count = (
                w.getnchannels(),
                w.getsampwidth(),
                w.getframerate(),
                w.getnframes(),
            )
            if (
                w.getcomptype() != "NONE"
                or channels not in (1, 2)
                or width not in (1, 2, 3, 4)
            ):
                raise FormatError(
                    "WAV must be integer PCM, mono/stereo, 8/16/24/32-bit"
                )
            if not 1 <= rate <= 384000 or count > rate * 600:
                raise FormatError("invalid/excessive WAV duration")
            raw = w.readframes(count)
    except (wave.Error, EOFError) as e:
        raise FormatError(str(e)) from e
    if len(raw) != count * width * channels or not count:
        raise FormatError("empty/truncated WAV")
    samples = []
    for i in range(0, len(raw), width * channels):
        values = []
        for c in range(channels):
            b = raw[i + c * width : i + (c + 1) * width]
            values.append(
                (b[0] - 128) / 128
                if width == 1
                else int.from_bytes(b, "little", signed=True) / (1 << (width * 8 - 1))
            )
        samples.append(sum(values) / channels)
    return (
        samples,
        rate,
        {
            "frames": count,
            "channels": channels,
            "bits": width * 8,
            "downmixed": channels == 2,
        },
    )


def convert(path, region="ntsc", gain=1.0, loop=None, *, pcm_timer=None):
    src, rate, info = decode(path)
    return resample(src, rate, info, region, gain, loop, pcm_timer=pcm_timer)


def resample(src, rate, info, region="ntsc", gain=1.0, loop=None, *, pcm_timer=None):
    if region not in RATES or not math.isfinite(gain) or gain < 0:
        raise FormatError("invalid PCM region/gain")
    if not src or not math.isfinite(rate) or rate <= 0:
        raise FormatError("invalid PCM source rate/data")
    timer = TIMER_A if pcm_timer is None else pcm_timer
    if timer not in (1016, 1017, 1018, 1019, 1020):
        raise FormatError("invalid PCM timer")
    target = {"ntsc": 53693175, "pal": 53203424}[region] / 7 / (144 * (1024 - timer))
    ratio = rate / target
    count = max(1, round(len(src) / ratio))
    cutoff = min(1.0, target / rate) * 0.94
    radius = 24 / max(cutoff, 0.01)
    output = bytearray()
    clipped = 0
    for i in range(count):
        pos = i * ratio
        lo = max(0, math.ceil(pos - radius))
        hi = min(len(src), math.floor(pos + radius) + 1)
        total = norm = 0.0
        for j in range(lo, hi):
            x = (pos - j) * cutoff
            sinc = 1 if abs(x) < 1e-12 else math.sin(math.pi * x) / (math.pi * x)
            window = 0.5 + 0.5 * math.cos(math.pi * (pos - j) / radius)
            weight = sinc * window
            total += src[j] * weight
            norm += weight
        value = round((total / norm if norm else 0) * gain * 128)
        if value < -128 or value > 127:
            clipped += 1
        value = max(-128, min(127, value))
        output.append(value & 255)
    bounds = None
    if loop is not None:
        if (
            len(loop) != 2
            or any(type(x) != int for x in loop)
            or not 0 <= loop[0] < loop[1] <= len(src)
        ):
            raise FormatError("loop must be [start,end) source-frame indices")
        bounds = [round(x / ratio) for x in loop]
        if bounds[0] >= bounds[1] or bounds[1] > count:
            raise FormatError("loop collapsed during resampling")
    info.update(
        rate=target, bytes=len(output), loop=bounds, gain=gain, clipped_samples=clipped
    )
    return bytes(output), info
