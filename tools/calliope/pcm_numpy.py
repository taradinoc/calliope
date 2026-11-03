"""Bounded vector evaluation of the reference windowed-sinc PCM filter."""

import math
import numpy as np


def encode(src, ratio, count, cutoff, radius, gain):
    from .pcm import _encode_scalar, _sample

    samples = np.asarray(src, dtype=np.float64)
    width = min(len(src), math.ceil(2 * radius) + 1)
    # Bound temporary matrices even for long clips and steep downsampling.
    chunk = max(1, min(1024, 262144 // width))
    taps = np.arange(width)
    output = bytearray()
    clipped = 0
    for start in range(0, count, chunk):
        positions = np.arange(start, min(count, start + chunk)) * ratio
        lo = np.maximum(0, np.ceil(positions - radius)).astype(np.int64)
        hi = np.minimum(len(src), np.floor(positions + radius) + 1).astype(np.int64)
        indices = lo[:, None] + taps
        distance = positions[:, None] - indices
        x = distance * cutoff
        angle = math.pi * x
        sinc = np.divide(np.sin(angle), angle, out=np.ones_like(x), where=np.abs(x) >= 1e-12)
        window = 0.5 + 0.5 * np.cos(math.pi * distance / radius)
        weights = sinc * window
        weights[indices >= hi[:, None]] = 0
        # cumsum preserves the scalar accumulation order; a pairwise reduction
        # can cross a PCM quantization boundary on nearly cancelling samples.
        total = np.cumsum(samples[np.minimum(indices, len(src) - 1)] * weights, axis=1)[:, -1]
        norm = np.cumsum(weights, axis=1)[:, -1]
        values = np.divide(total, norm, out=np.zeros_like(total), where=norm != 0) * gain * 128
        if not np.isfinite(values).all():
            return _encode_scalar(src, ratio, count, cutoff, radius, gain)
        rounded = np.rint(values)
        # libm and NumPy trig can differ by a few ulps. Resolve values near
        # half-integer rounding boundaries with the unchanged scalar filter.
        ties = np.flatnonzero(np.abs(values - rounded) > 0.5 - 1e-7)
        for index in ties:
            rounded[index] = _sample(src, float(positions[index]), cutoff, radius, gain)
        clipped += int(np.count_nonzero((rounded < -128) | (rounded > 127)))
        output.extend(np.clip(rounded, -128, 127).astype(np.int16).astype(np.uint8).tobytes())
    return bytes(output), clipped
