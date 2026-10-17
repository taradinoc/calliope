"""Bounded device output; device resampling never changes emulated chip clocks."""

from array import array
import sys
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices


class AudioOutput(QObject):
    problem = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sink = None
        self.pending = bytearray()
        self.samples = []
        self.position = 0
        self.timer = QTimer(self)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self.flush)

    def start(self, source_rate):
        self.stop()
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            raise RuntimeError("No audio output device is available")
        self.source_rate = source_rate
        for rate in dict.fromkeys(
            (source_rate, 48000, 44100, device.preferredFormat().sampleRate())
        ):
            for sample_format in (
                QAudioFormat.SampleFormat.Int16,
                QAudioFormat.SampleFormat.Float,
            ):
                fmt = QAudioFormat()
                fmt.setSampleRate(rate)
                fmt.setChannelCount(2)
                fmt.setSampleFormat(sample_format)
                if device.isFormatSupported(fmt):
                    break
            else:
                continue
            break
        else:
            raise RuntimeError(
                "Audio device does not support stereo PCM16 or float output"
            )
        self.format = fmt
        self.ratio = source_rate / fmt.sampleRate()
        self.float_output = fmt.sampleFormat() == QAudioFormat.SampleFormat.Float
        self.sink = QAudioSink(device, fmt, self)
        self.sink.setBufferSize(fmt.bytesForDuration(100000))
        self.sink.stateChanged.connect(self.state_changed)
        self.io = self.sink.start()
        if self.io is None:
            raise RuntimeError("Audio device could not start")
        self.timer.start()

    @Slot()
    def state_changed(self):
        if self.sink and self.sink.error().value:
            self.problem.emit("Audio output error: " + self.sink.error().name)

    def append(self, data):
        values = array("h")
        values.frombytes(data)
        if sys.byteorder != "little":
            values.byteswap()
        self.samples.extend(values)
        out = array("f" if self.float_output else "h")
        frames = len(self.samples) // 2
        while self.position + 1 < frames:
            index = int(self.position)
            fraction = self.position - index
            for ch in (0, 1):
                value = (
                    self.samples[index * 2 + ch] * (1 - fraction)
                    + self.samples[(index + 1) * 2 + ch] * fraction
                )
                out.append(value / 32768 if self.float_output else round(value))
            self.position += self.ratio
        consumed = int(self.position)
        del self.samples[: consumed * 2]
        self.position -= consumed
        self.pending.extend(out.tobytes())
        limit = self.format.bytesForDuration(300000)
        if len(self.pending) > limit:
            self.pending.clear()
            self.problem.emit(
                "Audio buffer overflow; preview output skipped to stay current"
            )
        self.flush()

    def flush(self):
        if self.sink and self.pending:
            count = min(len(self.pending), self.sink.bytesFree())
            if count:
                written = self.io.write(bytes(self.pending[:count]))
                if written > 0:
                    del self.pending[:written]

    def stop(self):
        self.timer.stop()
        if self.sink:
            self.sink.stop()
            self.sink.deleteLater()
            self.sink = None
        self.pending.clear()
        self.samples = []
        self.position = 0
