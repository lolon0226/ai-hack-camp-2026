# -*- coding: utf-8 -*-
"""RedRibbon Demo Print Receiver — C:\\RedRibbonDemo 전용 PDF 감시·업로드."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import requests

_DEMO_ROOT = r"C:\RedRibbonDemo"

DEFAULT_CONFIG: dict[str, Any] = {
    "server_url": "http://127.0.0.1:8010",
    "upload_endpoint": "/api/print-receiver/upload",
    "watch_dir": rf"{_DEMO_ROOT}\incoming",
    "uploading_dir": rf"{_DEMO_ROOT}\uploading",
    "uploaded_dir": rf"{_DEMO_ROOT}\uploaded",
    "failed_dir": rf"{_DEMO_ROOT}\failed",
    "logs_dir": rf"{_DEMO_ROOT}\logs",
    "hospital_name": "TEST_HOSPITAL",
    "printer_name": "RedRibbon Printer",
    "poll_interval_sec": 2,
    "stable_wait_sec": 2,
    "stable_timeout_sec": 120,
    "min_file_bytes": 512,
    "request_timeout_sec": 900,
    "state_file": rf"{_DEMO_ROOT}\logs\uploaded_hashes.json",
}


def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_config_path() -> Path:
    return _script_dir() / "config.json"


def _coerce_config_keys(raw: dict[str, Any]) -> dict[str, Any]:
    """config.json 별칭 키를 엔진 내부 키로 통일."""
    out = dict(raw)
    if "log_dir" in out and "logs_dir" not in out:
        out["logs_dir"] = out["log_dir"]
    if "poll_interval_seconds" in out and "poll_interval_sec" not in out:
        out["poll_interval_sec"] = out["poll_interval_seconds"]
    if "stable_wait_seconds" in out and "stable_wait_sec" not in out:
        out["stable_wait_sec"] = out["stable_wait_seconds"]
    if "upload_timeout_seconds" in out and "request_timeout_sec" not in out:
        out["request_timeout_sec"] = out["upload_timeout_seconds"]
    if "stable_timeout_seconds" in out and "stable_timeout_sec" not in out:
        out["stable_timeout_sec"] = out["stable_timeout_seconds"]
    return out


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or _default_config_path()
    merged = dict(DEFAULT_CONFIG)
    if cfg_path.is_file():
        with cfg_path.open(encoding="utf-8-sig") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            merged.update(_coerce_config_keys(loaded))
    else:
        merged = _coerce_config_keys(merged)

    watch = Path(str(merged.get("watch_dir") or DEFAULT_CONFIG["watch_dir"]))
    base = watch.parent
    merged.setdefault("uploading_dir", str(base / "uploading"))
    merged.setdefault("uploaded_dir", str(base / "uploaded"))
    merged.setdefault("failed_dir", str(base / "failed"))
    logs_dir = str(merged.get("logs_dir") or merged.get("log_dir") or base / "logs")
    merged["logs_dir"] = logs_dir
    merged.setdefault("state_file", str(Path(logs_dir) / "uploaded_hashes.json"))
    merged["poll_interval_sec"] = float(
        merged.get("poll_interval_sec") or merged.get("poll_interval_seconds") or 2
    )
    merged["stable_wait_sec"] = float(
        merged.get("stable_wait_sec") or merged.get("stable_wait_seconds") or 2
    )
    merged["stable_timeout_sec"] = float(
        merged.get("stable_timeout_sec") or merged.get("stable_timeout_seconds") or 120
    )
    merged["request_timeout_sec"] = int(
        merged.get("request_timeout_sec") or merged.get("upload_timeout_seconds") or 900
    )
    return merged


def setup_logging(logs_dir: Path) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("redribbon.demo.receiver")
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
        data = json.loads(state_file.read_text(encoding="utf-8-sig"))
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
    timeout = int(config.get("request_timeout_sec") or 900)
    with path.open("rb") as fh:
        files = {"file": (path.name, fh, "application/pdf")}
        data = {
            "hospital_name": str(config.get("hospital_name") or ""),
            "printer_name": str(config.get("printer_name") or "RedRibbon Printer"),
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
        os.utime(dest, None)
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
        os.utime(target, None)
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
    logger.info(
        "RedRibbon Demo Print Receiver 시작 — 감시: %s (printer=%s)",
        incoming,
        config.get("printer_name"),
    )
    while True:
        for path in sorted(incoming.glob("*.pdf")):
            try:
                process_one_file(path, config, uploaded_hashes, logger)
            except Exception:
                logger.exception("파일 처리 오류: %s", path)
        time.sleep(poll)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RedRibbon Demo Print Receiver")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="config.json 경로 (예: C:\\RedRibbonDemo\\print_receiver\\config.json)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config_path = args.config or _default_config_path()
    config = load_config(config_path)
    logger = setup_logging(Path(str(config["logs_dir"])))
    logger.info("설정 파일: %s", config_path)
    try:
        run_loop(config, logger)
    except KeyboardInterrupt:
        logger.info("Print Receiver 종료")
        return 0


if __name__ == "__main__":
    sys.exit(main())
