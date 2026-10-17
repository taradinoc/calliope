"""Verified ROM assembly and minimal libretro host; no GUI or image dependency."""

import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile


def make_rom(bank, assets, region, mode):
    stem = Path(assets) / f"{mode}-{region}"
    meta = json.loads(stem.with_suffix(".json").read_text())
    template = stem.with_suffix(".bin").read_bytes()
    if hashlib.sha256(template).hexdigest() != meta["sha256"]:
        raise ValueError("Player template hash mismatch; regenerate player assets")
    if (
        meta["protocol"] != 1
        or meta["mode"] != mode
        or meta["region"] != region
        or meta["bank_revision"] != 12
    ):
        raise ValueError("Unsupported player template identity")
    if (
        len(bank) < 32
        or bank[:6] != b"BGM1\x0c\x00"
        or int.from_bytes(bank[19:21], "little") != meta["pcm_timer"]
        or bank[21] != int(region == "pal")
    ):
        raise ValueError("Bank revision, region, or PCM timer does not match player")
    address = meta["bank_address"]
    length_address = meta["bank_length_address"]
    if (
        address % 32768
        or address < len(template)
        or address + len(bank) > 0x400000
        or not 0 <= length_address <= len(template) - 4
    ):
        raise ValueError("Player bank placement exceeds Genesis ROM limits")
    raw = bytearray(template)
    raw[length_address : length_address + 4] = len(bank).to_bytes(4, "big")
    raw.extend(bytes(address - len(raw)))
    raw.extend(bank)
    # Cartridge ROMs are padded to a power of two for consistent mapper behavior.
    raw.extend(bytes((1 << (len(raw) - 1).bit_length()) - len(raw)))
    raw[0x1A4:0x1A8] = (len(raw) - 1).to_bytes(4, "big")
    raw[0x18E:0x190] = (
        sum(struct.unpack(">" + str((len(raw) - 0x200) // 2) + "H", raw[0x200:]))
        & 65535
    ).to_bytes(2, "big")
    return bytes(raw), meta


def core_path(assets):
    suffix = (
        ".dll"
        if sys.platform == "win32"
        else ".dylib" if sys.platform == "darwin" else ".so"
    )
    path = Path(assets) / ("genesis_plus_gx" + suffix)
    manifest = json.loads((Path(assets) / "core.json").read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("Emulator core hash mismatch")
    return path


class Variable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


class Game(C.Structure):
    _fields_ = [
        ("path", C.c_char_p),
        ("data", C.c_void_p),
        ("size", C.c_size_t),
        ("meta", C.c_char_p),
    ]


class Player:
    def __init__(self, core, rom, metadata):
        self.core = C.CDLL(str(Path(core).resolve()))
        self.meta = metadata
        self.audio = []
        self.variables = {}
        self.sequence = 0
        ENV = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
        VIDEO = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
        AUDIO = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
        BATCH = C.CFUNCTYPE(C.c_size_t, C.POINTER(C.c_int16), C.c_size_t)
        POLL = C.CFUNCTYPE(None)
        INPUT = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)
        self.callbacks = [
            ("environment", ENV(self.environment)),
            ("video_refresh", VIDEO(lambda *a: None)),
            (
                "audio_sample",
                AUDIO(lambda l, r: self.audio.append(struct.pack("<hh", l, r))),
            ),
            ("audio_sample_batch", BATCH(self.batch)),
            ("input_poll", POLL(lambda: None)),
            ("input_state", INPUT(lambda *a: 0)),
        ]
        for name, callback in self.callbacks:
            function = getattr(self.core, "retro_set_" + name)
            function.argtypes = [type(callback)]
            function(callback)
        self.core.retro_init()
        self.buffer = C.create_string_buffer(rom)
        self.directory = tempfile.TemporaryDirectory(prefix="calliope-preview-")
        rom_path = Path(self.directory.name) / "player.bin"
        rom_path.write_bytes(rom)
        self.directory_bytes = self.directory.name.encode("utf-8")
        game = Game(
            str(rom_path).encode("utf-8"),
            C.cast(self.buffer, C.c_void_p),
            len(rom),
            None,
        )
        self.core.retro_load_game.argtypes = [C.POINTER(Game)]
        self.core.retro_load_game.restype = C.c_bool
        if not self.core.retro_load_game(C.byref(game)):
            raise RuntimeError("Emulator rejected the player ROM")

        class Geometry(C.Structure):
            _fields_ = [
                ("width", C.c_uint),
                ("height", C.c_uint),
                ("max_width", C.c_uint),
                ("max_height", C.c_uint),
                ("aspect", C.c_float),
            ]

        class Timing(C.Structure):
            _fields_ = [("fps", C.c_double), ("sample_rate", C.c_double)]

        class AV(C.Structure):
            _fields_ = [("geometry", Geometry), ("timing", Timing)]

        info = AV()
        self.core.retro_get_system_av_info.argtypes = [C.POINTER(AV)]
        self.core.retro_get_system_av_info(C.byref(info))
        self.fps = info.timing.fps
        self.sample_rate = int(round(info.timing.sample_rate))
        self.core.retro_get_memory_data.argtypes = [C.c_uint]
        self.core.retro_get_memory_data.restype = C.c_void_p
        self.core.retro_get_memory_size.argtypes = [C.c_uint]
        self.core.retro_get_memory_size.restype = C.c_size_t
        self.ram = self.core.retro_get_memory_data(2)
        if not self.ram or self.core.retro_get_memory_size(2) < 65536:
            raise RuntimeError("Core does not expose Genesis work RAM")
        self.offset = metadata["mailbox_address"] & 65535
        self.swap = 0
        for _ in range(180):
            self.core.retro_run()
            if self.read(0, 4) == b"CLPE":
                break
            self.swap = 1
            if self.read(0, 4) == b"CLPE":
                break
            self.swap = 0
        else:
            raise RuntimeError("Player mailbox did not initialize")
        if self.read(4, 2) != b"\x00\x01":
            raise RuntimeError("Unsupported player protocol")
        self.audio = []
        self.core.clp_editor_zram.restype = C.POINTER(C.c_ubyte)
        self.zram = self.core.clp_editor_zram()

    def environment(self, command, data):
        if command in (9, 30, 31) and hasattr(self, "directory_bytes"):
            C.cast(data, C.POINTER(C.c_char_p))[0] = self.directory_bytes
            return True
        if command == 10:
            return C.cast(data, C.POINTER(C.c_int))[0] in (0, 1, 2)
        if command == 16:
            entries = C.cast(data, C.POINTER(Variable))
            i = 0
            while entries[i].key:
                options = entries[i].value.split(b"; ", 1)
                if len(options) == 2:
                    self.variables[entries[i].key] = options[1].split(b"|")[0]
                i += 1
            self.variables[b"genesis_plus_gx_region_detect"] = self.meta[
                "region"
            ].encode()
            self.variables[b"genesis_plus_gx_bios"] = b"disabled"
            return True
        if command == 15:
            entry = C.cast(data, C.POINTER(Variable)).contents
            entry.value = self.variables.get(entry.key)
            return entry.value is not None
        if command in (3, 17):
            C.cast(data, C.POINTER(C.c_bool))[0] = False
            return True
        if command == 18:
            C.cast(data, C.POINTER(C.c_int))[0] = 0
            return True
        return command in (11, 12, 13, 35)

    def batch(self, data, frames):
        self.audio.append(C.string_at(data, frames * 4))
        return frames

    def read(self, at, size):
        return bytes(
            C.c_ubyte.from_address(
                self.ram + ((self.offset + at + i) ^ self.swap)
            ).value
            for i in range(size)
        )

    def write(self, at, value):
        for i, b in enumerate(value):
            C.c_ubyte.from_address(
                self.ram + ((self.offset + at + i) ^ self.swap)
            ).value = b

    def command(self, opcode, a=0, b=0, c=0, d=0):
        if self.read(8, 2) != self.read(10, 2):
            raise RuntimeError("Player command is still pending")
        self.sequence = (self.sequence + 1) & 65535
        self.write(12, struct.pack(">5H", opcode, a, b, c, d))
        self.write(8, self.sequence.to_bytes(2, "big"))
        return self.sequence

    def frame(self):
        self.audio = []
        self.core.retro_run()
        return b"".join(self.audio)

    def status(self):
        data = self.read(0, 116)
        result = {
            "playing": bool(data[41]),
            "fault": data[42],
            "underruns": data[43],
            "completed": bool(data[44]),
            "owners": list(data[48:57]),
            "pcm_owner": data[57],
            "pcm_token": int.from_bytes(data[58:60], "big"),
            "tick": int.from_bytes(data[96:100], "big"),
            "ack": int.from_bytes(data[10:12], "big"),
            "queued": bool(data[22]),
            "effects": [
                dict(
                    active=bool(data[p]),
                    priority=data[p + 1],
                    token=int.from_bytes(data[p + 2 : p + 4], "big"),
                    mask=int.from_bytes(data[p + 4 : p + 6], "big"),
                )
                for p in range(64, 96, 8)
            ],
            "event_id": int.from_bytes(data[104:106], "big"),
            "event_parameter": int.from_bytes(data[106:108], "big"),
            "event_count": int.from_bytes(data[112:114], "big"),
        }
        if self.meta["mode"] == "z80":
            z = self.zram
            result.update(
                owners=list(z[0x1F20:0x1F29]),
                tick=int.from_bytes(bytes(z[0x1D0A:0x1D0E]), "little"),
                pcm_owner=2 if z[0x1EC0] else 1 if z[0x1EAC] else 0,
                pcm_token=int.from_bytes(bytes(z[0x1EAE:0x1EB0]), "little"),
                effects=[
                    dict(
                        active=bool(z[p]),
                        priority=z[p + 2],
                        token=z[p + 4] | (z[p + 5] << 8),
                        mask=sum(1 << ch for ch in range(9) if z[0x1F20 + ch] == i + 1),
                    )
                    for i, p in enumerate(range(0x1800, 0x1840, 16))
                ],
            )
        return result

    def close(self):
        self.core.retro_unload_game()
        self.core.retro_deinit()
        self.directory.cleanup()
