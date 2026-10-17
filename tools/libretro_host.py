"""Generic libretro host adapted from BonkGen tools/libretro_host.py.
Game-specific title entry removed; see THIRD_PARTY.md.
"""
from toolchain import core_path
import ctypes as C
import re
from pathlib import Path
import numpy as np
from PIL import Image

class Variable(C.Structure):
    _fields_ = [('key', C.c_char_p), ('value', C.c_char_p)]
class GameInfo(C.Structure):
    _fields_ = [('path', C.c_char_p), ('data', C.c_void_p), ('size', C.c_size_t), ('meta', C.c_char_p)]

class Host:
    def __init__(self, core, rom, directory, options=None):
        self.core = C.CDLL(str(core_path(core)))
        self.directory = str(Path(directory).resolve()).encode()
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.variables = {}; self.options = options or {}; self.buttons = set(); self.format = 0; self.image = None
        self.audio_sink = None
        self.rom_path = Path(rom)
        self.raw = None; self.size = None
        ENV = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
        VIDEO = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
        AUDIO = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
        BATCH = C.CFUNCTYPE(C.c_size_t, C.POINTER(C.c_int16), C.c_size_t)
        POLL = C.CFUNCTYPE(None)
        INPUT = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)
        self.callbacks = [
            ('environment', ENV(self.environment)), ('video_refresh', VIDEO(self.video)),
            ('audio_sample', AUDIO(lambda l,r: None)), ('audio_sample_batch', BATCH(self.audio)),
            ('input_poll', POLL(lambda: None)),
            ('input_state', INPUT(lambda p,d,i,b: int(p == 0 and b in self.buttons)))]
        for name, cb in self.callbacks:
            fn = getattr(self.core, 'retro_set_' + name); fn.argtypes = [type(cb)]; fn(cb)
        self.core.retro_init()
        self.core.retro_load_game.argtypes = [C.POINTER(GameInfo)]
        self.core.retro_load_game.restype = C.c_bool
        data = Path(rom).read_bytes(); self.buffer = C.create_string_buffer(data)
        info = GameInfo(str(Path(rom).resolve()).encode(), C.cast(self.buffer,C.c_void_p), len(data), None)
        assert self.core.retro_load_game(C.byref(info))
        # Complete the frontend AV handshake before the first frame. Some cores
        # configure their audio rate when this information is requested.
        self.video_hz, self.sample_rate = self.timing()
        self.core.retro_set_controller_port_device(0,1)
        self.core.retro_serialize_size.restype = C.c_size_t
        self.core.retro_serialize.argtypes = [C.c_void_p,C.c_size_t]; self.core.retro_serialize.restype = C.c_bool
        self.core.retro_unserialize.argtypes = [C.c_void_p,C.c_size_t]; self.core.retro_unserialize.restype = C.c_bool
        self.core.retro_get_memory_data.argtypes = [C.c_uint]; self.core.retro_get_memory_data.restype = C.c_void_p
        self.core.retro_get_memory_size.argtypes = [C.c_uint]; self.core.retro_get_memory_size.restype = C.c_size_t
    def environment(self, command, data):
        if command in (9,30,31): C.cast(data,C.POINTER(C.c_char_p))[0] = self.directory; return True
        if command == 10: self.format = C.cast(data,C.POINTER(C.c_int))[0]; return self.format in (0,1,2)
        if command == 16:
            entries = C.cast(data,C.POINTER(Variable)); i=0
            while entries[i].key:
                opts = entries[i].value.split(b'; ',1)
                if len(opts)==2: self.variables[entries[i].key] = self.options.get(entries[i].key, opts[1].split(b'|')[0])
                i+=1
            return True
        if command == 15:
            entry = C.cast(data,C.POINTER(Variable)).contents
            entry.value = self.variables.get(entry.key); return entry.value is not None
        if command in (3,17): C.cast(data,C.POINTER(C.c_bool))[0] = False; return True
        if command == 18: C.cast(data,C.POINTER(C.c_int))[0] = 0; return True
        return command in (11,12,13,35)
    def video(self,data,width,height,pitch):
        if data:
            self.raw = C.string_at(data,pitch*height); self.size=(width,height,pitch)
    def audio(self,data,frames):
        if self.audio_sink: self.audio_sink(C.string_at(data,frames*4))
        return frames
    def screenshot(self):
        w,h,p = self.size
        if self.format==1:
            return Image.frombytes('RGB',(w,h),self.raw,'raw','BGRX',p)
        a=np.frombuffer(self.raw,dtype='<u2').reshape(h,p//2)[:,:w]
        gb=6 if self.format==2 else 5
        rgb=np.stack((((a>>(5+gb))&31)*255//31,((a>>5)&((1<<gb)-1))*255//((1<<gb)-1),(a&31)*255//31),axis=-1).astype('uint8')
        return Image.fromarray(rgb)
    def run(self,frames=1,buttons=()):
        self.buttons=set(buttons)
        for _ in range(frames): self.core.retro_run()
    def timing(self):
        """Return the core's current video Hz and audio sample rate."""
        class Geometry(C.Structure):
            _fields_ = [('width',C.c_uint),('height',C.c_uint),('max_width',C.c_uint),
                        ('max_height',C.c_uint),('aspect',C.c_float)]
        class Timing(C.Structure):
            _fields_ = [('fps',C.c_double),('sample_rate',C.c_double)]
        class AVInfo(C.Structure):
            _fields_ = [('geometry',Geometry),('timing',Timing)]
        info = AVInfo()
        self.core.retro_get_system_av_info.argtypes = [C.POINTER(AVInfo)]
        self.core.retro_get_system_av_info(C.byref(info))
        return info.timing.fps, info.timing.sample_rate
    def save(self):
        n=self.core.retro_serialize_size(); buf=C.create_string_buffer(n)
        assert self.core.retro_serialize(buf,n); return buf.raw
    def load(self,data):
        buf=C.create_string_buffer(data); assert self.core.retro_unserialize(buf,len(data))
    def memory(self,kind=2):
        n=self.core.retro_get_memory_size(kind); p=self.core.retro_get_memory_data(kind)
        return C.string_at(p,n) if p and n else b''
    def close(self): self.core.retro_unload_game(); self.core.retro_deinit()
