"""NicelyNoted's recorder. Captures what your speakers play (the other people on the call,
via WASAPI loopback) and your mic, transcribes both locally with Whisper, and appends
timestamped lines to ~/NicelyNoted/meetings/<start>.txt. No cloud, no summary — that is
the browser extension's job.

Loopback is system-wide, not Edge-only: anything audible through the default speaker
ends up in the transcript. Keep music and other tabs off during a meeting.

Normally started by the Edge extension through host.py; standalone:

    python recorder.py                 # until Ctrl+C
    python recorder.py --seconds 60
    python recorder.py --selfcheck     # chunker/echo asserts + Windows voice -> loopback -> Whisper
"""
import argparse
import os
import queue
import re
import signal
import subprocess
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import soundcard as sc

NATIVE = 48000
RATE = 16000            # what Whisper wants
BLOCK = NATIVE // 2     # 0.5s per read — also the pause-detection resolution
MIN_CHUNK = 10          # seconds: don't cut before this...
MAX_CHUNK = 30          # ...and always cut by this, pause or not
SILENT_RMS = 0.003      # quieter than this is room tone — Whisper hallucinates text from it
ECHO_OVERLAP = 0.35     # share of a mic line's word pairs found in Them => it is an echo
ECHO_MIN_PAIRS = 3      # ...but never on fewer shared pairs than this
HOME = Path.home() / "NicelyNoted"
OUT_DIR = HOME / "meetings"
MODEL = os.environ.get("NICELYNOTED_MODEL")   # override, e.g. "medium.en"

_model = None


def transcribe(samples: np.ndarray) -> str:
    """Mono float32 audio at RATE -> text. large-v3 on an NVIDIA GPU (about 3.8GB VRAM),
    small.en on CPU otherwise or if the GPU runs out of memory."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        import ctranslate2
        try:
            if not ctranslate2.get_cuda_device_count():
                raise RuntimeError("no CUDA device")
            _model = WhisperModel(MODEL or "large-v3", device="cuda", compute_type="float16")
        except Exception as e:
            print(f"[nicelynoted] GPU unavailable ({e}); using small.en on CPU")
            _model = WhisperModel(MODEL or "small.en", device="cpu", compute_type="int8")
    segments, _ = _model.transcribe(samples, language="en", beam_size=5,
                                    condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


def resample(samples: np.ndarray, src: int, dst: int = RATE) -> np.ndarray:
    """Linear resample. Good enough for speech; avoids pulling in scipy."""
    n = int(len(samples) * dst / src)
    return np.interp(np.linspace(0, len(samples) - 1, n),
                     np.arange(len(samples)), samples).astype(np.float32)


class Chunker:
    """Groups 0.5s blocks into chunks, cutting at the first pause after MIN_CHUNK so
    sentences stay whole. A pause is relative to the chunk's own average level, so it
    works for a quiet loopback and a noisy mic alike."""

    def __init__(self):
        self.blocks: list[np.ndarray] = []
        self.levels: list[float] = []

    def feed(self, block: np.ndarray) -> np.ndarray | None:
        self.blocks.append(block)
        rms = float(np.sqrt(np.mean(block ** 2)))
        self.levels.append(rms)
        secs = len(self.blocks) * BLOCK / NATIVE
        quiet = rms < max(SILENT_RMS, 0.3 * float(np.mean(self.levels)))
        if secs >= MAX_CHUNK or (secs >= MIN_CHUNK and quiet):
            return self.flush()
        return None

    def flush(self) -> np.ndarray | None:
        if not self.blocks:
            return None
        audio = np.concatenate(self.blocks)
        self.blocks, self.levels = [], []
        return audio


def is_echo(you: str, them: str) -> bool:
    """True when the mic line is just the speakers leaking into the mic: most of its word
    pairs also appear in what Them said at the same time. Pairs, not single words —
    "the", "to" and "and" overlap between any two sentences, "revised budget" doesn't."""
    def pairs(t: str) -> set:
        w = re.findall(r"[a-z0-9']+", t.lower())
        return set(zip(w, w[1:]))
    y = pairs(you)
    shared = len(y & pairs(them))
    return shared >= ECHO_MIN_PAIRS and shared / len(y) >= ECHO_OVERLAP


class Session:
    def __init__(self, path: Path | None = None, on_line=print):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path or OUT_DIR / f"{datetime.now():%Y-%m-%d_%H%M}.txt"
        self.on_line = on_line
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        spk = sc.default_speaker()
        sources = [("Them", sc.get_microphone(spk.name, include_loopback=True)),
                   ("You", sc.default_microphone())]
        print(f"[nicelynoted] {self.path}\n[nicelynoted] them = {spk.name} | you = {sources[1][1].name}")
        self._t0 = time.time()
        self._threads = [threading.Thread(target=self._record, args=s, daemon=True)
                         for s in sources]
        self._worker = threading.Thread(target=self._work)
        for t in (*self._threads, self._worker):
            t.start()

    def stop(self) -> Path:
        """Stop recording, then transcribe whatever is still queued."""
        self._stop.set()
        self._worker.join()
        return self.path

    def _record(self, label: str, mic) -> None:
        try:
            ck, start = Chunker(), time.time() - self._t0
            with mic.recorder(samplerate=NATIVE, channels=1, blocksize=BLOCK) as rec:
                while not self._stop.is_set():
                    audio = ck.feed(rec.record(numframes=BLOCK)[:, 0])
                    if audio is not None:
                        self._q.put((label, start, audio))
                        start = time.time() - self._t0
            audio = ck.flush()
            if audio is not None:
                self._q.put((label, start, audio))
        except Exception:
            traceback.print_exc()   # one dead source must not take the other down
        finally:
            self._q.put(None)

    def _work(self) -> None:
        ended = 0
        them: list[tuple[float, float, str]] = []   # (start, end, text) of every Them line
        held: list[tuple[float, float, str]] = []   # You lines waiting to be checked for echo
        covered = 0.0                               # Them has been transcribed up to here

        def release(final: bool) -> None:
            for h in held[:]:
                if final or h[1] <= covered:
                    held.remove(h)
                    near = " ".join(t for s, e, t in them if s < h[1] + 5 and e > h[0] - 5)
                    if not is_echo(h[2], near):
                        self._emit("You", h[0], h[2])

        while ended < len(self._threads):
            item = self._q.get()
            if item is None:
                ended += 1
                continue
            label, start, audio = item
            end = start + len(audio) / NATIVE
            text = ""
            if np.sqrt(np.mean(audio ** 2)) >= SILENT_RMS:
                try:
                    text = transcribe(resample(audio, NATIVE))
                except Exception:
                    traceback.print_exc()   # lose this chunk, keep the meeting going
            if label == "Them":
                covered = max(covered, end)   # silent chunks count: Them has still moved on
                if text:
                    them.append((start, end, text))
                    self._emit(label, start, text)
            elif text:
                held.append((start, end, text))
            release(final=False)
        release(final=True)

    def _emit(self, label: str, start: float, text: str) -> None:
        m, s = divmod(int(start), 60)
        line = f"[{m // 60:02d}:{m % 60:02d}:{s:02d}] {label}: {text}"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        self.on_line(line)


def run(seconds: float | None = None) -> Path:
    s = Session()
    s.start()
    try:
        t0 = time.time()
        while seconds is None or time.time() - t0 < seconds:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    signal.signal(signal.SIGINT, signal.SIG_IGN)   # a 2nd Ctrl+C mid-flush would lose the last chunk
    print("[nicelynoted] finishing the last chunks, one moment...")
    return s.stop()


def chunker_check() -> None:
    loud, quiet = np.full(BLOCK, 0.1, np.float32), np.zeros(BLOCK, np.float32)
    ck = Chunker()
    assert all(ck.feed(loud) is None for _ in range(19))           # 9.5s of speech
    out = ck.feed(quiet)
    assert out is not None and len(out) == 20 * BLOCK              # first pause at 10.0s -> cut
    assert all(ck.feed(quiet) is None for _ in range(19))          # silence before MIN is not cut early
    ck = Chunker()
    assert all(ck.feed(loud) is None for _ in range(59))           # unbroken speech...
    assert len(ck.feed(loud)) == 60 * BLOCK                        # ...is cut at exactly MAX_CHUNK
    print("chunker ok")


def echo_check() -> None:
    them = "Action item, Priya will send the revised budget to the finance team by Friday."
    assert is_echo("For an item, Priya will send the revised budget to the finance team by March 1, 2021.", them)
    assert not is_echo("Sounds good, I will send it to the team and to the client by Monday.", them)
    assert not is_echo("Yes", them)
    # a real miss: Whisper garbled the echo ("3-0" for Priya) but 6 of its 14 word pairs still match
    assert is_echo("Action Item 3-0 will send a revised budget to the finance team on Friday.", them)
    print("echo ok")


def selfcheck() -> None:
    """Windows' built-in voice speaks through the default speaker; loopback must hear it."""
    chunker_check()
    echo_check()
    spk = sc.default_speaker()
    done, got = threading.Event(), []

    def listen():
        with sc.get_microphone(spk.name, include_loopback=True).recorder(
                samplerate=NATIVE, channels=1) as rec:
            while not done.is_set():
                got.append(rec.record(numframes=NATIVE // 2)[:, 0])

    t = threading.Thread(target=listen)
    t.start()
    time.sleep(1)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Add-Type -AssemblyName System.Speech; "
                    "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak("
                    "'The quarterly budget review is scheduled for Thursday afternoon.')"],
                   check=True)
    time.sleep(1)
    done.set()
    t.join()
    text = transcribe(resample(np.concatenate(got), NATIVE)).lower()
    print("heard:", text)
    assert "budget" in text and "thursday" in text, "loopback did not carry the speech"
    print("ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    selfcheck() if a.selfcheck else print("saved:", run(a.seconds))
