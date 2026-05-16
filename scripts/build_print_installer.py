# -*- coding: utf-8 -*-
"""ZIP + Inno Setup EXE 빌드(본선 Demo Print Receiver)."""
from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER_SRC = ROOT / "installer" / "RedRibbon_Demo_Print_Setup"
PRINT_RECEIVER_SRC = ROOT / "print_receiver"
SCRIPTS_SRC = ROOT / "scripts"
DEST_PKG_RECEIVER = INSTALLER_SRC / "print_receiver"
ZIP_PATH = ROOT / "static" / "downloads" / "RedRibbon_Demo_Print_Setup.zip"
EXE_PATH = ROOT / "static" / "downloads" / "RedRibbon_Demo_Print_Setup.exe"
ISS_PATH = INSTALLER_SRC / "setup.iss"

DEMO_OUTPUT_SUBDIRS = ("incoming", "uploading", "uploaded", "failed", "logs")

ISCC_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
)

INSTALLER_SCRIPT_FILES = (
    "install_redribbon_demo.ps1",
    "register_receiver_task.ps1",
    "check_receiver_ready.ps1",
    "run_redribbon_receiver.ps1",
    "run_redribbon_receiver.bat",
    "README.txt",
    "SETUP_VIRTUAL_PRINTER.md",
)


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


def _copy_utf8_file(src: Path, dest: Path) -> None:
    if not src.is_file():
        return
    text = src.read_text(encoding="utf-8-sig")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _write_utf8_no_bom(dest, text)


def sync_receiver_sources() -> None:
    """설치 패키지 소스 동기화(print_receiver + 설치 스크립트)."""
    DEST_PKG_RECEIVER.mkdir(parents=True, exist_ok=True)

    for name in ("receiver_engine.py",):
        src = PRINT_RECEIVER_SRC / name
        if src.is_file():
            shutil.copy2(src, DEST_PKG_RECEIVER / name)

    check_src = INSTALLER_SRC / "check_receiver_ready.ps1"
    if check_src.is_file():
        _copy_utf8_file(check_src, DEST_PKG_RECEIVER / "check_receiver_ready.ps1")
    else:
        alt = PRINT_RECEIVER_SRC / "check_receiver_ready.ps1"
        if alt.is_file():
            _copy_utf8_file(alt, DEST_PKG_RECEIVER / "check_receiver_ready.ps1")

    config_src = PRINT_RECEIVER_SRC / "config.json"
    if config_src.is_file():
        dest_config = DEST_PKG_RECEIVER / "config.json"
        shutil.copy2(config_src, dest_config)
        _normalize_json_file(dest_config)

    reg_src = SCRIPTS_SRC / "register_receiver_task.ps1"
    if reg_src.is_file():
        _copy_utf8_file(reg_src, INSTALLER_SRC / "register_receiver_task.ps1")

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
            if path.name == ".gitkeep":
                continue
            arcname = path.relative_to(INSTALLER_SRC.parent).as_posix()
            zf.write(path, arcname)
    return ZIP_PATH


def find_iscc() -> Path | None:
    for candidate in ISCC_CANDIDATES:
        if candidate.is_file():
            return candidate
    which = shutil.which("ISCC")
    if which:
        return Path(which)
    return None


def build_exe() -> Path | None:
    sync_receiver_sources()
    if not ISS_PATH.is_file():
        print(f"setup.iss not found: {ISS_PATH}")
        return None
    iscc = find_iscc()
    if not iscc:
        print("Inno Setup(ISCC.exe) not found - skip EXE build")
        return None
    EXE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [str(iscc), str(ISS_PATH)],
            cwd=str(INSTALLER_SRC),
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"EXE build failed: {exc}")
        return None
    if EXE_PATH.is_file():
        return EXE_PATH
    print(f"EXE build finished but missing: {EXE_PATH}")
    return None


def main() -> None:
    print("1/3 sync installer sources...")
    sync_receiver_sources()
    print("2/3 build ZIP...")
    zip_out = build_zip()
    print(f"zip: {zip_out} ({zip_out.stat().st_size} bytes)")
    print("3/3 build EXE (optional)...")
    exe_out = build_exe()
    if exe_out:
        print(f"exe: {exe_out} ({exe_out.stat().st_size} bytes)")
    else:
        print("exe: EXE 미생성, ZIP 사용 가능 (Inno Setup 6 설치 후 재실행)")


if __name__ == "__main__":
    main()
