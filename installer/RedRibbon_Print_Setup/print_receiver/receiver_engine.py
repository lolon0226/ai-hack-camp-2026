# -*- coding: utf-8 -*-
"""RedRibbon Print Receiver — incoming PDF 감시 및 서버 업로드."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import requests

DEFAULT_CONFIG: dict[str, Any] = {
    "server_url": "http://127.0.0.1:8000",
    "upload_endpoint": "/api/print-receiver/upload",
    "watch_dir": r"C:\RedRibbonPrint\RedRibbon_Printer_Output\incoming",
    "uploading_dir": r"C:\RedRibbonPrint\RedRibbon_Printer_Output\uploading",
    "uploaded_dir": r"C:\RedRibbonPrint\RedRibbon_Printer_Output\uploaded",
    "failed_dir": r"C:\RedRibbonPrint\RedRibbon_Printer_Output\failed",
    "logs_dir": r"C:\RedRibbonPrint\RedRibbon_Printer_Output\logs",
    "hospital_name": "TEST_HOSPITAL",
    "printer_name": "RedRibbon Printer",
    "poll_interval_sec": 2,
    "stable_wait_sec": 2,
    "stable_timeout_sec": 120,
    "min_file_bytes": 512,
    "request_timeout_sec": 120,
    "state_file": r"C:\RedRibbonPrint\RedRibbon_Printer_Output\logs\uploaded_hashes.json",
}


def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_config_path() -> Path:
    return _script_dir() / "config.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or _default_config_path()
    merged = dict(DEFAULT_CONFIG)
    if cfg_path.is_file():
        with cfg_path.open(encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            merged.update(loaded)
    base = Path(str(merged.get("watch_dir") or DEFAULT_CONFIG["watch_dir"])).parent
    merged.setdefault("uploading_dir", str(base / "uploading"))
    merged.setdefault("uploaded_dir", str(base / "uploaded"))
    merged.setdefault("failed_dir", str(base / "failed"))
    merged.setdefault("logs_dir", str(base / "logs"))
    merged.setdefault(
        "state_file", str(Path(str(merged["logs_dir"])) / "uploaded_hashes.json")
    )
    return merged


def setup_logging(logs_dir: Path) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("redribbon.receiver")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = RotatingFileHandler(
        logs_dir / "receiver.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    return logger


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_uploaded_hashes(state_file: Path) -> set[str]:
    if not state_file.is_file():
        return set()
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    if isinstance(data, list):
        return {str(x).lower() for x in data}
    return set()


def save_uploaded_hashes(state_file: Path, hashes: set[str]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(sorted(hashes), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def wait_until_stable(
    path: Path,
    *,
    min_bytes: int,
    stable_wait_sec: float,
    stable_timeout_sec: float,
    logger: logging.Logger,
) -> bool:
    deadline = time.time() + stable_timeout_sec
    last_size = -1
    stable_since: float | None = None
    while time.time() < deadline:
        if not path.is_file():
            return False
        size = path.stat().st_size
        if size >= min_bytes and size == last_size:
            if stable_since is None:
                stable_since = time.time()
            if time.time() - stable_since >= stable_wait_sec:
                return True
        else:
            stable_since = None
        last_size = size
        time.sleep(0.5)
    logger.warning("PDF 안정화 대기 시간 초과: %s", path.name)
    return False


def upload_pdf(config: dict[str, Any], path: Path, logger: logging.Logger) -> dict[str, Any]:
    base = str(config.get("server_url") or "").rstrip("/")
    endpoint = str(config.get("upload_endpoint") or "/api/print-receiver/upload")
    url = f"{base}{endpoint}"
    timeout = int(config.get("request_timeout_sec") or 120)
    with path.open("rb") as fh:
        files = {"file": (path.name, fh, "application/pdf")}
        data = {
            "hospital_name": str(config.get("hospital_name") or ""),
            "printer_name": str(config.get("printer_name") or ""),
        }
        response = requests.post(url, files=files, data=data, timeout=timeout)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:500]}
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {payload}")
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected response: {payload!r}")
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "upload_failed"))
    return payload


def process_one_file(
    path: Path,
    config: dict[str, Any],
    uploaded_hashes: set[str],
    logger: logging.Logger,
) -> None:
    incoming = Path(str(config["watch_dir"]))
    uploading = Path(str(config["uploading_dir"]))
    uploaded = Path(str(config["uploaded_dir"]))
    failed = Path(str(config["failed_dir"]))
    for folder in (uploading, uploaded, failed):
        folder.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() != ".pdf":
        logger.info("PDF가 아니어서 건너뜀: %s", path.name)
        return

    digest = sha256_file(path)
    if digest in uploaded_hashes:
        logger.info("이미 업로드한 파일(sha256) — uploaded로 이동: %s", path.name)
        dest = uploaded / f"{digest[:12]}_{path.name}"
        shutil.move(str(path), str(dest))
        return

    if not wait_until_stable(
        path,
        min_bytes=int(config.get("min_file_bytes") or 512),
        stable_wait_sec=float(config.get("stable_wait_sec") or 2),
        stable_timeout_sec=float(config.get("stable_timeout_sec") or 120),
        logger=logger,
    ):
        dest = failed / path.name
        shutil.move(str(path), str(dest))
        return

    working = uploading / path.name
    shutil.move(str(path), str(working))
    try:
        result = upload_pdf(config, working, logger)
        digest = str(result.get("sha256") or digest)
        uploaded_hashes.add(digest)
        save_uploaded_hashes(Path(str(config["state_file"])), uploaded_hashes)
        suffix = "duplicate" if result.get("duplicate") else "ok"
        target = uploaded / f"{suffix}_{digest[:12]}_{working.name}"
        shutil.move(str(working), str(target))
        logger.info(
            "업로드 성공 doc_id=%s duplicate=%s file=%s",
            result.get("document_id"),
            result.get("duplicate"),
            working.name,
        )
    except Exception as exc:
        logger.exception("업로드 실패 %s: %s", working.name, exc)
        dest = failed / working.name
        if working.is_file():
            shutil.move(str(working), str(dest))


def run_loop(config: dict[str, Any], logger: logging.Logger) -> None:
    incoming = Path(str(config["watch_dir"]))
    incoming.mkdir(parents=True, exist_ok=True)
    state_file = Path(str(config["state_file"]))
    uploaded_hashes = load_uploaded_hashes(state_file)
    poll = float(config.get("poll_interval_sec") or 2)
    logger.info("Print Receiver 시작 — 감시: %s", incoming)
    while True:
        for path in sorted(incoming.glob("*.pdf")):
            try:
                process_one_file(path, config, uploaded_hashes, logger)
            except Exception:
                logger.exception("파일 처리 오류: %s", path)
        time.sleep(poll)


def main(argv: list[str] | None = None) -> int:
    cfg_arg = argv[1] if argv and len(argv) > 1 else None
    config = load_config(Path(cfg_arg) if cfg_arg else None)
    logger = setup_logging(Path(str(config["logs_dir"])))
    try:
        run_loop(config, logger)
    except KeyboardInterrupt:
        logger.info("Print Receiver 종료")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
