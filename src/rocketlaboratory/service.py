"""Shared bounded execution layer for CLI, browser and MCP."""

import concurrent.futures
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
from . import __version__, demo, experiment, report
from .bridge import Bridge, MAX_CAPTURE, atomic, lease, read_json
from .instrument.tape import compile_tape

MAX_WORKSPACE = 1024 * 1024 * 1024
JOB_ID = re.compile(r"^[a-f0-9]{32}$")


def default_workspace():
    base = (
        os.environ.get("XDG_DATA_HOME")
        or os.environ.get("LOCALAPPDATA")
        or str(Path.home() / ".local/share")
    )
    return Path(base) / "RocketLaboratory"


class Service:
    def __init__(self, workspace=None, bridge_dir=None):
        self.workspace = Path(workspace or default_workspace()).expanduser().resolve()
        self.jobs = self.workspace / "jobs"
        self.jobs.mkdir(parents=True, exist_ok=True)
        self.bridge = Bridge(bridge_dir) if bridge_dir else None
        self.pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rocketlab"
        )
        self.running = {}
        self.lock = threading.Lock()

    def doctor(self):
        result = {
            "version": __version__,
            "workspace": str(self.workspace),
            "python": os.sys.version.split()[0],
            "native_configured": bool(self.bridge),
            "native_launch_supported": False,
            "timing_qualified": False,
            "hidden_state_restore_qualified": False,
            "backends": ["demo"] + (["native"] if self.bridge else []),
            "capture_limit_mib": 128,
            "workspace_limit_mib": 1024,
            "max_concurrent_runs": 1,
            "game_installation": "User-provided Rocket League and official offline-compatible mod installation required",
        }
        if self.bridge:
            try:
                result["session_remaining_s"] = self.bridge.check()
            except (OSError, ValueError, RuntimeError) as error:
                result["native_session_error"] = str(error)
        return result

    def job_path(self, job):
        if not isinstance(job, str) or not JOB_ID.fullmatch(job):
            raise ValueError("Invalid job ID")
        path = self.jobs / job
        if path.is_symlink():
            raise ValueError("Symlink job not supported")
        if path.resolve().parent != self.jobs.resolve():
            raise ValueError("Job path escapes workspace")
        return path

    def status(self, job):
        return read_json(self.job_path(job) / "status.json")

    def list(self):
        result = []
        for path in sorted(self.jobs.iterdir(), key=lambda p: p.name):
            if path.is_dir() and not path.is_symlink() and JOB_ID.fullmatch(path.name):
                try:
                    result.append(self.status(path.name))
                except (OSError, ValueError):
                    pass
        return sorted(result, key=lambda s: s["created"], reverse=True)[:100]

    def submit(self, spec, backend="demo"):
        spec = experiment.validate(spec)
        if backend not in ("demo", "native"):
            raise ValueError("Unknown backend")
        if backend == "native" and self.bridge is None:
            raise ValueError("Native bridge directory is not configured")
        with self.lock:
            if any(not value[0].done() for value in self.running.values()):
                raise RuntimeError("A run is already active; wait or cancel")
            if shutil.disk_usage(self.workspace).free < 256 * 1024 * 1024:
                raise RuntimeError("Insufficient disk headroom")
            usage = sum(
                p.stat().st_size
                for p in self.workspace.rglob("*")
                if p.is_file() and not p.is_symlink()
            )
            if usage > MAX_WORKSPACE - MAX_CAPTURE:
                raise RuntimeError(
                    "Workspace capture budget exhausted; archive selected jobs manually"
                )
            job = uuid.uuid4().hex
            path = self.job_path(job)
            path.mkdir()
            atomic(path / "experiment.json", json.dumps(spec, indent=2) + "\n")
            state = {
                "id": job,
                "name": spec["name"],
                "backend": backend,
                "state": "queued",
                "created": time.time(),
                "timing_qualified": False,
                "exact_acceptance_eligible": False,
            }
            atomic(path / "status.json", json.dumps(state))
            cancel = threading.Event()
            future = self.pool.submit(self._run, job, spec, backend, cancel)
            self.running[job] = (future, cancel)
        return self.status(job)

    def cancel(self, job):
        self.job_path(job)
        with self.lock:
            active = self.running.get(job)
            if not active or active[0].done():
                return {
                    "id": job,
                    "cancel_requested": False,
                    "reason": "No active run in this service",
                }
            active[1].set()
        return {"id": job, "cancel_requested": True}

    def wait(self, job):
        self.running[job][0].result()
        return self.status(job)

    def _run(self, job, spec, backend, cancel):
        path = self.job_path(job)
        state = read_json(path / "status.json")
        state["state"] = "running"
        atomic(path / "status.json", json.dumps(state))
        try:
            if backend == "demo":
                demo.run(spec, path / "capture.nni", cancel.is_set)
            else:
                self._native(path, spec, cancel)
            data = report.inspect(
                path / "capture.nni",
                source="synthetic_demo"
                if backend == "demo"
                else "native_hook_observations",
            )
            atomic(path / "report.json", json.dumps(data, allow_nan=False))
            if backend == "native" and (
                not data["integrity_complete"]
                or data["terminal_quality"].get("experiment_failed", 0) != 0
                or data["terminal_quality"].get("controller_stage") != 3
            ):
                raise RuntimeError(
                    "Native terminal quality did not establish a completed intact capture"
                )
            state["state"] = "complete"
        except Exception as error:
            state["state"] = (
                "cancelled" if isinstance(error, InterruptedError) else "failed"
            )
            state["error"] = str(error)
        state["finished"] = time.time()
        atomic(path / "status.json", json.dumps(state))

    def _native(self, path, spec, cancel):
        bridge = self.bridge
        with lease(bridge.folder):
            bridge.check(minimum_seconds=125)
            retained = sum(
                p.stat().st_size for p in bridge.folder.glob("*.nni") if p.is_file()
            )
            if retained > 768 * 1024 * 1024:
                raise RuntimeError(
                    "Bridge capture retention budget reached; archive private evidence before another run"
                )
            audit = bridge.folder / "automation-operations.jsonl"
            if audit.exists() and audit.stat().st_size > 32 * 1024 * 1024:
                raise RuntimeError(
                    "Bridge audit retention budget reached; archive it before another run"
                )
            started = False
            capture = None
            tape_file = None
            try:
                before = bridge.command("actors")
                if before.get("capture_active"):
                    raise RuntimeError(
                        "Another native capture is active; refusing to interrupt it"
                    )
                if not before.get("game_context", {}).get("offlineGateSnapshotAllowed"):
                    raise RuntimeError("Plugin did not verify offline local server")
                current = bridge.command("start")
                started = True
                filename = current.get("capture_file", "")
                if not re.fullmatch(r"[0-9]+\.nni", filename):
                    raise RuntimeError("Unexpected native capture filename")
                capture = bridge.folder / filename
                bindings = {
                    a["car_index"]: a["actor"] for a in current.get("actors", [])
                }
                wire = experiment.to_tape(spec, bindings)
                atomic(path / "tape.json", json.dumps(wire, indent=2))
                atomic(path / "start-ack.json", json.dumps(current))
                if spec["preparation"] == "fixed_pose_then_settle":
                    bridge.command("prepare_fixed")
                    deadline = time.monotonic() + 4
                    while True:
                        if cancel.is_set():
                            raise InterruptedError("Cancelled native preparation")
                        status = bridge.command("actors")
                        phase = status.get("native_preparation", {}).get("state")
                        if phase == "applied_setters_unverified":
                            break
                        if (
                            phase in ("failed", "cancelled")
                            or time.monotonic() > deadline
                        ):
                            raise RuntimeError(
                                "Fixed preparation failed/unverified; no tape armed"
                            )
                        time.sleep(0.05)
                tape_file = bridge.folder / ("rocketlab-" + path.name + ".tape")
                atomic(tape_file, compile_tape(wire))
                bridge.command("tape", tape_file.name)
                deadline = time.monotonic() + 110
                while True:
                    if cancel.is_set():
                        raise InterruptedError("Cancelled native experiment")
                    if capture.exists() and capture.stat().st_size > MAX_CAPTURE:
                        raise RuntimeError("Native capture budget exceeded")
                    status = bridge.command("actors")
                    stage = status.get("controller_stage")
                    if stage == 3:
                        break
                    if stage == 4:
                        raise RuntimeError("Native scheduler failed")
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            "Native experiment wall-clock limit exceeded"
                        )
                    time.sleep(0.2)
                final = bridge.command("stop")
                started = False
                atomic(path / "stop-ack.json", json.dumps(final))
                if (
                    not capture.is_file()
                    or capture.is_symlink()
                    or capture.stat().st_size > MAX_CAPTURE
                ):
                    raise RuntimeError("Missing/oversized native capture")
                shutil.copyfile(capture, path / "capture.nni")
                for suffix in (".manifest.json", ".status.json"):
                    meta = capture.with_name(capture.name + suffix)
                    if meta.exists():
                        atomic(path / ("capture" + suffix), json.dumps(read_json(meta)))
                # Copy succeeded; remove only files this run created, retaining the workspace copy.
                for suffix in ("", ".manifest.json", ".status.json"):
                    capture.with_name(capture.name + suffix).unlink(missing_ok=True)
            finally:
                if started:
                    try:
                        bridge.command("stop")
                    except Exception as error:
                        atomic(
                            path / "stop-error.json",
                            json.dumps(
                                {"error": str(error), "watchdog_fallback_s": 120}
                            ),
                        )
                if tape_file:
                    tape_file.unlink(missing_ok=True)

    def result(self, job):
        return read_json(self.job_path(job) / "report.json", 8 * 1024 * 1024)

    def close(self):
        for future, cancel in list(self.running.values()):
            if not future.done():
                cancel.set()
        self.pool.shutdown(wait=True, cancel_futures=False)
