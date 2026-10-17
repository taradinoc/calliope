"""Measure the current bank and offline phrase/source-pattern experiments.

Compile the supported songs first, then measure exact command compression and
a sparse source-pattern representation. The source representation round-trips
cells, not audio: it requires a new runtime effect/macro interpreter.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import tempfile
from unittest.mock import patch

from calliope import compiler
from calliope.flow import resolve
from calliope.furnace import Cell, FormatError, parse
from calliope.music_sync import LENGTHS
from calliope.timing import Timing

WIDTHS = {**LENGTHS, **dict.fromkeys(range(0x80,0xfd),0), **dict.fromkeys(range(0x40,0x50),1), 0: 1, 0x38: 1, 0x39: 1, 0xfd: 0, 0xff: 0,0x10:1,0x11:2,0x13:0,0x16:1,0x36:2,0x37:5}
CALL = 0x7e  # Private study opcode, never published in BGM1.


def tokens(data, start=0):
    result = []
    while start < len(data):
        op = data[start]
        if op in (0x30, 0x35):
            if start + 1 >= len(data):
                raise FormatError('truncated PSG packet')
            size = 2 + data[start + 1]
        elif op in WIDTHS:
            size = 1 + WIDTHS[op]
        else:
            raise FormatError(f'unknown study opcode {op:02x}')
        if start + size > len(data):
            raise FormatError('truncated command')
        token = bytes(data[start:start + size])
        if op == 0 and not token[1]:
            raise FormatError('zero wait')
        result.append(token)
        start += size
        if op in (0xfd, 0xff):
            return tuple(result)
    raise FormatError('missing stream terminator')


def timed_trace(stream):
    tick = 0
    result = []
    for token in stream:
        if token[0] == 0 or 0x80<=token[0]<0xfd:
            tick += token[1] if token[0]==0 else token[0]-0x7f
        else:
            result.append((tick, token))
    return result


def merge_waits(stream):
    output = []
    delay = 0

    def flush():
        nonlocal delay
        while delay:
            n = min(delay, 255)
            output.append(bytes((n+0x7f,)) if n<=125 else bytes((0, n)))
            delay -= n

    for token in stream:
        if token[0] == 0 or 0x80<=token[0]<0xfd:
            delay += token[1] if token[0]==0 else token[0]-0x7f
        else:
            flush()
            output.append(token)
    flush()
    result = tuple(output)
    if timed_trace(result) != timed_trace(stream):
        raise AssertionError('wait merge changed timed commands')
    return result


def phrase_compress(streams, limit=128):
    """Greedy flat calls on whole commands, with actual nonoverlap savings.

    Four-byte CALL (opcode + u24 address), one-byte RET. No nesting, no calls
    across order ends. Sampling candidate lengths is deliberately heuristic.
    """
    candidates = Counter()
    for stream in streams:
        body = stream[:-1]
        for width in (4, 8, 16, 32, 64):
            candidates.update(tuple(body[i:i + width])
                              for i in range(len(body) - width + 1))
    ranked = []
    for phrase, count in candidates.items():
        size = sum(map(len, phrase))
        saving = count * (size - 4) - size - 1
        if count > 1 and saving > 0:
            ranked.append((saving, phrase))
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    encoded = list(streams)
    dictionary = []
    for _, phrase in ranked:
        if len(dictionary) == limit:
            break
        call = bytes((CALL,)) + len(dictionary).to_bytes(3, 'little')
        rewritten = []
        occurrences = 0
        for stream in encoded:
            output = []
            at = 0
            while at < len(stream):
                if stream[at:at + len(phrase)] == phrase:
                    output.append(call)
                    at += len(phrase)
                    occurrences += 1
                else:
                    output.append(stream[at])
                    at += 1
            rewritten.append(tuple(output))
        size = sum(map(len, phrase))
        if occurrences * (size - 4) > size + 1:
            encoded = rewritten
            dictionary.append(phrase)
    for original, compressed in zip(streams, encoded):
        expanded = tuple(t for command in compressed
                         for t in (dictionary[int.from_bytes(command[1:], 'little')]
                                   if command[0] == CALL else (command,)))
        if expanded != original:
            raise AssertionError('phrase round-trip failed')
    return encoded, dictionary


def uleb(value):
    if value < 0:
        raise ValueError('unsigned value required')
    output = bytearray()
    while value >= 128:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


def read_uleb(data, at):
    value = shift = 0
    while at < len(data) and shift <= 28:
        byte = data[at]
        at += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, at
        shift += 7
    raise FormatError('truncated/oversized ULEB')


def encode_pattern(cells):
    output = bytearray(uleb(len(cells)))
    previous = -1
    for row, cell in enumerate(cells):
        if not cell.active():
            continue
        output.extend(uleb(row - previous))
        previous = row
        fields = (cell.note, cell.instrument, cell.volume)
        mask = sum(1 << i for i, value in enumerate(fields) if value is not None)
        output.append(mask | (8 if cell.effects else 0))
        for value in fields:
            if value is not None:
                output.extend(uleb(value))
        if cell.effects:
            output.extend(uleb(len(cell.effects)))
            for column, effect, value in cell.effects:
                output.extend(uleb(column) + uleb(effect) + uleb(0 if value is None else value + 1))
    output.append(0)  # End; positive deltas are relative to the previous event.
    return bytes(output)


def decode_pattern(data):
    rows, at = read_uleb(data, 0)
    result = [Cell() for _ in range(rows)]
    row = -1
    while True:
        delta, at = read_uleb(data, at)
        if delta == 0:
            if at != len(data):
                raise FormatError('trailing pattern bytes')
            return result
        row += delta
        if row >= rows or at >= len(data):
            raise FormatError('invalid pattern row')
        mask = data[at]
        at += 1
        if mask & ~15:
            raise FormatError('invalid pattern mask')
        values = []
        for bit in (1, 2, 4):
            if mask & bit:
                value, at = read_uleb(data, at)
                values.append(value)
            else:
                values.append(None)
        effects = []
        if mask & 8:
            count, at = read_uleb(data, at)
            for _ in range(count):
                column, at = read_uleb(data, at)
                effect, at = read_uleb(data, at)
                value, at = read_uleb(data, at)
                effects.append((column, effect, None if value == 0 else value - 1))
        result[row] = Cell(*values, effects)


def source_patterns(module, item):
    """Serialize reachable cells, shared raw macro/sample features and conductor.

    No inferred note-off or duration changes: sparse gaps retain held notes.
    Caller accounts for existing patches, sample descriptors and part metadata.
    Compatibility config is retained for a future interpreter, not implemented.
    """
    sub = item.get('subsong', 0)
    song = module.subsongs[sub]
    channels = [song.names.index(v['channel']) if isinstance(v['channel'], str)
                else v['channel'] for v in item['voices']]
    flow = resolve(module, sub, channels, item.get('loop_order'))
    timing = Timing(module, sub)
    patterns = {}
    conductors = {}
    orders = bytearray()
    used = set()
    markers = {(m['order'], m['row']): m['id'] for m in item.get('markers', [])}
    for bundle in flow.bundles:
        conductor = bytearray(uleb(len(bundle)))
        for order, row in bundle:
            ticks, commands = timing.row(order, row, channels)
            beat, bar = song.highlights
            absolute = order * song.rows + row
            flags = 2 | (4 if beat and absolute % beat == 0 else 0) | (8 if bar and absolute % bar == 0 else 0)
            marker = markers.get((order, row))
            conductor.extend(uleb(ticks) + bytes((flags | (16 if marker is not None else 0),)))
            if marker is not None:
                conductor.extend(struct.pack('<H', marker))
            conductor.extend(uleb(len(commands)) + commands)
        data = bytes(conductor)
        index = conductors.setdefault(data, len(conductors))
        orders.extend(index.to_bytes(3, 'little'))
        for channel in channels:
            cells = [module.cell(sub, order, row, channel) for order, row in bundle]
            used.update(c.instrument for c in cells if c.instrument is not None)
            data = encode_pattern(cells)
            if decode_pattern(data) != cells:
                raise AssertionError('source cell round-trip failed')
            index = patterns.setdefault(data, len(patterns))
            orders.extend(index.to_bytes(3, 'little'))
    instruments = bytearray(uleb(len(used)))
    for index in sorted(used):
        ins = module.instruments[index]
        # FM patch and PCM descriptor data are accounted for outside this payload.
        # IDs are original Furnace IDs; the future runtime needs this mapping.
        instruments.extend(uleb(index) + uleb(ins['kind']) + bytes(2))
        features = {k: v for k, v in ins['features'].items() if k not in ('NA', 'FM')}
        instruments.extend(uleb(len(features)))
        for name, data in sorted(features.items()):
            instruments.extend(name.encode('ascii') + uleb(len(data)) + data)
    config = json.dumps({
        'flags': module.flags, 'tuning': module.tuning, 'speeds': song.speeds,
        'arp_speed': song.arp_speed, 'rate': song.hz,
        'slots': [v['slot'] for v in item['voices']], 'events': item.get('events', {}),
        'external_commands': item.get('external_commands'),
    }, sort_keys=True, separators=(',', ':')).encode()
    blobs = list(conductors) + list(patterns)
    # Three-byte directory pointers and per-blob u16 lengths; all costs counted.
    total = (16 + len(orders) + len(instruments) + 4 + len(config)
             + sum(5 + len(b) for b in blobs))
    return {'bytes': total, 'pattern_bytes': sum(map(len, patterns)),
            'unique_patterns': len(patterns), 'pattern_references': len(flow.bundles) * len(channels),
            'conductor_bytes': sum(map(len, conductors)), 'order_bytes': len(orders),
            'instrument_bytes': len(instruments), 'config_bytes': len(config),
            'directory_bytes': 5 * len(blobs), 'header_bytes': 20,
            'round_trip': True}


def analyze_song(manifest, item, region):
    class ObservedPacker(compiler.Packer):
        instance = None

        def __init__(self):
            super().__init__()
            self.allocations = []
            ObservedPacker.instance = self

        def add(self, data, alignment=1, bounded=True):
            before = len(self.data)
            address = super().add(data, alignment, bounded)
            if len(self.data) != before:
                self.allocations.append({'offset': address, 'bytes': len(data),
                                         'padding': address - before, 'alignment': alignment})
            return address

    isolated = dict(item, source=str((manifest.parent / item['source']).resolve()))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'bank.json'
        path.write_text(json.dumps({'version': 1, 'songs': [isolated]}))
        with patch.object(compiler, 'Packer', ObservedPacker):
            bank, report, _ = compiler.compile_bank(path, region)
    pack = ObservedPacker.instance
    descriptor = int.from_bytes(bank[10:13], 'little')
    count = bank[descriptor + 2] + 1
    table = int.from_bytes(bank[descriptor + 4:descriptor + 7], 'little')
    addresses = [int.from_bytes(bank[table + 3*i:table + 3*i + 3], 'little') for i in range(count)]
    records=list(dict.fromkeys(addresses))
    if bank[4]>=12:
        voice_count=bank[descriptor+11]&31
        addresses=[int.from_bytes(bank[a+i*3:a+i*3+3],'little') for a in records for i in range(voice_count+1)]
        directory_bytes=len(records)*(voice_count+1)*3+count*3
    else:directory_bytes=count*3
    streams = list(dict.fromkeys(tokens(bank, a) for a in addresses))
    commands = Counter()
    counts = Counter()
    for stream in streams:
        for token in stream:
            commands[f'{token[0]:02x}'] += len(token)
            counts[f'{token[0]:02x}'] += 1
    baseline = sum(sum(map(len, s)) for s in streams) + directory_bytes
    merged = list(dict.fromkeys(merge_waits(s) for s in streams))
    wait_bytes = sum(sum(map(len, s)) for s in merged) + directory_bytes
    compressed, dictionary = phrase_compress(merged)
    phrase_bytes = (sum(sum(map(len, s)) for s in compressed) + directory_bytes
                    + sum(sum(map(len, p)) + 1 for p in dictionary))
    sequence_allocations = [a for a in pack.allocations if a['alignment'] in (16,256)]
    padding = sum(a['padding'] for a in sequence_allocations)
    module = parse(isolated['source'])
    if 'tick_rate' in item:
        module.subsongs[item.get('subsong', 0)].hz = item['tick_rate']
    source = source_patterns(module, isolated)
    # Lookup: original sample ID u16, selected rate u32, gain f32, descriptor u24,
    # trigger note u8. Samples/descriptors themselves remain in the unchanged bank.
    source['sample_lookup_bytes'] = 14 * len(report['music_samples'])
    source['bytes'] += source['sample_lookup_bytes']
    return {'name': item['name'], 'source': item['source'], 'bank_bytes': len(bank),
            'orders': count, 'unique_streams': len(streams), 'reachable_rows': len(report['rows']),
            'sequence_bytes': baseline, 'sequence_alignment_padding': padding,
            'pcm_bytes': sum(dict((s['offset'], s['bytes']) for s in report['music_samples']).values()),
            'source_sha256': hashlib.sha256(Path(isolated['source']).read_bytes()).hexdigest(),
            'bank_sha256': report['sha256'],
            'opcode_bytes': dict(sorted(commands.items())), 'opcode_counts': dict(sorted(counts.items())),
            'wait_merged_bytes': wait_bytes, 'phrase_bytes': phrase_bytes,
            'phrase_count': len(dictionary),
            'phrase_calls': sum(t[0] == CALL for s in compressed for t in s),
            'command_round_trip': True, 'source_patterns': source}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', nargs='?', default='examples/bank.json')
    parser.add_argument('--region', choices=('ntsc', 'pal'), default='ntsc')
    parser.add_argument('--output', default='out/sequencer-study.json')
    args = parser.parse_args()
    manifest = Path(args.manifest).resolve()
    songs = compiler.load_manifest(manifest)['songs']
    results = []
    for song in songs:
        result = analyze_song(manifest, song, args.region)
        results.append(result)
        print(f"{song['name']}: stream {result['sequence_bytes']:,}; waits {result['wait_merged_bytes']:,}; "
              f"phrases {result['phrase_bytes']:,}; source patterns {result['source_patterns']['bytes']:,}", flush=True)
    data = {'region': args.region, 'pcm_timer': compiler.TIMER_A,
            'compiler_sha256': hashlib.sha256(Path(compiler.__file__).read_bytes()).hexdigest(),
            'scope': 'isolated songs; sequence sizes include order tables; no SFX; current playable patterns plus offline phrase/source estimates',
            'songs': results}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2) + '\n')


if __name__ == '__main__':
    main()
