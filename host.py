"""Edge native-messaging host. The extension spawns this when a Google Meet call opens
and talks to it over stdio: {"cmd": "start"} / {"cmd": "stop"}. Registered by install.py.

stdout belongs to the protocol, so every print in the process goes to host.log instead.
"""
import json
import struct
import sys
from pathlib import Path

_in, _out = sys.stdin.buffer, sys.stdout.buffer
LOG = Path.home() / "NicelyNoted" / "host.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
sys.stdout = sys.stderr = open(LOG, "a", buffering=1, encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
import recorder


def read() -> dict | None:
    head = _in.read(4)
    if len(head) < 4:
        return None     # browser closed the port
    return json.loads(_in.read(struct.unpack("<I", head)[0]))


def send(obj: dict) -> None:
    data = json.dumps(obj).encode()   # ponytail: Edge caps host->browser messages at 1MB (~3h of transcript)
    _out.write(struct.pack("<I", len(data)) + data)
    _out.flush()


def main() -> None:
    session = None
    while (msg := read()) is not None:
        if msg.get("cmd") == "start":
            if session is None:
                session = recorder.Session()
                session.start()
            send({"ok": True, "path": str(session.path)})
        elif msg.get("cmd") == "stop" and session is not None:
            path = session.stop()
            session = None
            send({"ok": True, "path": str(path),
                  "transcript": path.read_text("utf-8") if path.exists() else ""})
    if session is not None:
        session.stop()   # tab/browser died mid-meeting: still flush to disk


if __name__ == "__main__":
    main()
