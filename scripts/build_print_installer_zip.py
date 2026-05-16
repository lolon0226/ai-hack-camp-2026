# -*- coding: utf-8 -*-
"""본선 Demo Print Setup ZIP 생성."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER_SRC = ROOT / "installer" / "RedRibbon_Demo_Print_Setup"
PRINT_RECEIVER_SRC = ROOT / "print_receiver"
DEST_PKG_RECEIVER = INSTALLER_SRC / "print_receiver"
ZIP_PATH = ROOT / "static" / "downloads" / "RedRibbon_Demo_Print_Setup.zip"

DEMO_OUTPUT_SUBDIRS = ("incoming", "uploading", "uploaded", "failed", "logs")


def _write_utf8_no_bom(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def _normalize_json_file(path: Path) -> None:
    if not path.is_file():
        return
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    data = json.loads(raw.decode("utf-8"))
    _write_utf8_no_bom(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def sync_receiver_sources() -> None:
    DEST_PKG_RECEIVER.mkdir(parents=True, exist_ok=True)
    for name in ("receiver_engine.py", "check_receiver_ready.ps1"):
        src = PRINT_RECEIVER_SRC / name
        if src.is_file():
            shutil.copy2(src, DEST_PKG_RECEIVER / name)
    config_src = PRINT_RECEIVER_SRC / "config.json"
    if config_src.is_file():
        dest_config = DEST_PKG_RECEIVER / "config.json"
        shutil.copy2(config_src, dest_config)
        _normalize_json_file(dest_config)
    for sub in DEMO_OUTPUT_SUBDIRS:
        folder = INSTALLER_SRC / sub
        folder.mkdir(parents=True, exist_ok=True)
        keep = folder / ".gitkeep"
        if not keep.is_file():
            keep.write_text("", encoding="utf-8")


def build_zip() -> Path:
    sync_receiver_sources()
    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.is_file():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(INSTALLER_SRC.rglob("*")):
            if path.is_dir():
                continue
            arcname = path.relative_to(INSTALLER_SRC.parent).as_posix()
            zf.write(path, arcname)
    return ZIP_PATH


def main() -> None:
    out = build_zip()
    print(f"created: {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
