"""Allowlisted file bridge to the original SDK plugin; no console execution or injection."""

import contextlib
import json
import os
from pathlib import Path
import secrets
import time

MAX_CAPTURE = 128 * 1024 * 1024


def atomic(path, data):
    path = Path(path)
    temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as out:
            out.write(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path, budget=1024 * 1024):
    with Path(path).open("rb") as source:
        raw = source.read(budget + 1)
    if len(raw) > budget:
        raise ValueError("Metadata budget exceeded")
    return json.loads(raw)


@contextlib.contextmanager
def lease(folder):
    """OS lock shared by all workspaces targeting this instrument directory."""
    path = Path(folder) / ".rocketlab.lock"
    with path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            lock.seek(0)
            lock.write(b"0")
            lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("Instrument already owned by another run") from error
        else:
            import fcntl

            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError("Instrument already owned by another run") from error
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


class Bridge:
    def __init__(self, folder, timeout=5):
        self.folder = Path(folder).expanduser().resolve(strict=True)
        if not self.folder.is_dir():
            raise ValueError("Bridge directory required")
        self.timeout = timeout

    def arm(self, seconds=300):
        if not isinstance(seconds, int) or not 10 <= seconds <= 300:
            raise ValueError("Session expiry must be 10..300 seconds")
        with lease(self.folder):
            # Replacing a loaded session marker stops its capture. Check an existing
            # live bridge before rearming so this does not interrupt somebody's run.
            try:
                self.check()
                existing = True
            except (FileNotFoundError, ValueError, RuntimeError):
                existing = False
            if existing and self.command("actors").get("capture_active"):
                raise RuntimeError(
                    "Existing native capture is active; refusing to replace its session"
                )
            atomic(
                self.folder / "automation-enable.txt",
                f"NNIA1 {secrets.token_hex(16)} {int(time.time() * 1000) + seconds * 1000}\n",
            )
        return {
            "expires_in_s": seconds,
            "next": "Reload plugin in BakkesMod after arming; existing loaded plugins do not discover new markers.",
        }

    def check(self, minimum_seconds=0):
        marker = self.folder / "automation-enable.txt"
        if marker.stat().st_size > 1024:
            raise ValueError("Invalid session marker")
        parts = marker.read_text().split()
        if (
            len(parts) != 3
            or parts[0] != "NNIA1"
            or not parts[1].isascii()
            or not parts[1].replace("_", "").replace("-", "").isalnum()
            or len(parts[1]) > 64
        ):
            raise ValueError("Invalid session marker")
        remaining = (int(parts[2]) - time.time() * 1000) / 1000
        if not minimum_seconds < remaining <= 301:
            raise RuntimeError(
                "Instrument session expired or too short; arm and reload plugin"
            )
        return remaining

    def command(self, operation, filename=None):
        if operation not in ("start", "stop", "actors", "prepare_fixed", "tape"):
            raise ValueError("Command is not allowlisted")
        if operation == "tape":
            if (
                not filename
                or len(filename) > 128
                or not filename.isascii()
                or any(
                    c
                    not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
                    for c in filename
                )
                or ".." in filename
            ):
                raise ValueError("Tape requires a simple local filename")
        elif filename is not None:
            raise ValueError("Unexpected filename")
        self.check()
        nonce = secrets.token_hex(16)
        request = self.folder / "automation-request.txt"
        if request.exists() or (self.folder / "automation-request.processing").exists():
            raise RuntimeError(
                "Previous request still pending; reload/inspect plugin before retrying"
            )
        atomic(
            request,
            f"NNIC1 {nonce} {int(time.time() * 1000)} {operation}"
            + (f" {filename}" if filename else "")
            + "\n",
        )
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                result = read_json(self.folder / "automation-result.json")
            except (FileNotFoundError, json.JSONDecodeError):
                result = {}
            if result.get("nonce") == nonce:
                if result.get("operation") != operation:
                    raise RuntimeError("Acknowledgement operation mismatch")
                if result.get("success") is not True:
                    raise RuntimeError(
                        "Native command rejected: "
                        + str(result.get("error", "unknown"))
                    )
                return result
            time.sleep(0.04)
        raise TimeoutError("Plugin acknowledgement timeout; no native success inferred")
