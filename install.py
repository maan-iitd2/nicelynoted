"""Register host.py with Edge so the extension can launch it. Run once, with the venv
python, from a NORMAL terminal window (the launcher bakes in sys.executable):

    .venv\\Scripts\\python.exe install.py

A terminal inside a packaged (MSIX/Store) app can write the registry entry into a
private copy that Edge never sees. If the extension says "native messaging host not
found", re-run this from a plain Windows PowerShell.
"""
import base64
import hashlib
import json
import sys
import winreg
from pathlib import Path

NAME = "com.nicelynoted.host"
ROOT = Path(__file__).resolve().parent
OUT = Path.home() / "NicelyNoted" / "edge_host"


def extension_id() -> str:
    """Chromium derives an extension's ID from its manifest "key": sha256 of the DER
    public key, first 16 bytes, hex digits remapped 0-f -> a-p."""
    key = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    bat = OUT / "host.bat"
    bat.write_text(f'@echo off\r\n"{sys.executable}" "{ROOT / "host.py"}"\r\n')
    manifest = OUT / f"{NAME}.json"
    manifest.write_text(json.dumps({
        "name": NAME,
        "description": "NicelyNoted meeting recorder",
        "path": str(bat),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id()}/"],
    }, indent=2))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          rf"Software\Microsoft\Edge\NativeMessagingHosts\{NAME}") as k:
        winreg.SetValue(k, "", winreg.REG_SZ, str(manifest))
    print(f"registered {NAME} for extension {extension_id()}")
    print(f"next: edge://extensions -> Developer mode -> Load unpacked -> {ROOT / 'extension'}")


if __name__ == "__main__":
    main()
