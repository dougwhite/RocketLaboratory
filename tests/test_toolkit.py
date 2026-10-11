import copy
import io
import json
import struct
import zlib
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from rocketlaboratory import demo, dependencies, experiment, mcp, report
from rocketlaboratory.bridge import Bridge, atomic, lease, read_json
from rocketlaboratory.server import WebServer
from rocketlaboratory.service import Service


class ContractTests(unittest.TestCase):
    def test_presets_and_binding_are_independent(self):
        for spec in experiment.presets().values():
            experiment.validate(spec)
        spec = experiment.presets()["two-car-inputs"]
        before = copy.deepcopy(spec)
        wire = experiment.to_tape(spec, {0: 17, 1: 23})
        self.assertEqual(set(wire["actors"]), {"17", "23"})
        self.assertEqual(spec, before)
        with self.assertRaises(ValueError):
            experiment.to_tape(spec, {0: 17})
        with self.assertRaises(ValueError):
            experiment.to_tape(spec, {0: 17, 1: 17})

    def test_unsupported_resets_and_boundaries_fail_closed(self):
        original = experiment.presets()["throttle-step"]
        for key, value in [
            ("duration_steps", 7201),
            ("duration_steps", True),
            ("preparation", "suspension_checkpoint"),
            ("actors", []),
            ("extra", "unknown"),
        ]:
            spec = copy.deepcopy(original)
            spec[key] = value
            with self.assertRaises(ValueError):
                experiment.validate(spec)
        for controls in (
            {"jumped": True},
            {"throttle": float("nan")},
            {"teleport": True},
            {"jump": 1},
        ):
            spec = copy.deepcopy(original)
            spec["actors"][0]["segments"][0]["controls"] = controls
            with self.assertRaises(ValueError):
                experiment.validate(spec)
        spec = copy.deepcopy(original)
        spec["actors"][0]["segments"] *= 2
        with self.assertRaises(ValueError):
            experiment.validate(spec)
        spec = experiment.presets()["two-car-inputs"]
        spec["preparation"] = "fixed_pose_then_settle"
        with self.assertRaises(ValueError):
            experiment.validate(spec)

    def test_dependency_zip_rejects_path_escape_and_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            for name, mode in [("root/../../secret", 0), ("root/link", 0o120777 << 16)]:
                raw = io.BytesIO()
                with zipfile.ZipFile(raw, "w") as archive:
                    member = zipfile.ZipInfo(name)
                    member.external_attr = mode
                    archive.writestr(member, "data")
                with self.assertRaises(ValueError):
                    dependencies.extract(
                        raw.getvalue(), Path(temp) / "dep", {"sha256": "x"}
                    )

    def test_bounded_dependency_extraction_preserves_notice(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("root/LICENSE", "upstream notice")
            archive.writestr("root/src/a.py", "print(1)")
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "dep"
            dependencies.extract(raw.getvalue(), target, {"sha256": "x"})
            self.assertEqual((target / "LICENSE").read_text(), "upstream notice")
            self.assertIn(
                "LICENSE", read_json(target / ".rocketlab-dependency.json")["files"]
            )


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(Path(self.temp.name) / "workspace")

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_demo_job_capture_report_and_compare(self):
        spec = experiment.presets()["throttle-step"]
        jobs = []
        for _ in range(2):
            job = self.service.submit(spec)
            state = self.service.wait(job["id"])
            self.assertEqual(state["state"], "complete")
            jobs.append(job["id"])
        result = self.service.result(jobs[0])
        self.assertEqual(result["source"], "synthetic_demo")
        self.assertTrue(result["integrity_complete"])
        self.assertFalse(result["exact_acceptance_eligible"])
        comparison = report.compare(
            *(self.service.job_path(j) / "capture.nni" for j in jobs)
        )
        self.assertEqual(comparison["position_rms_uu"], 0)
        self.assertEqual(comparison["paired_bodies"], 360)

    def test_cancel_and_concurrent_rejection(self):
        spec = experiment.presets()["throttle-step"]
        spec["duration_steps"] = 7200
        job = self.service.submit(spec)
        with self.assertRaises(RuntimeError):
            self.service.submit(spec)
        self.assertTrue(self.service.cancel(job["id"])["cancel_requested"])
        self.assertEqual(self.service.wait(job["id"])["state"], "cancelled")

    def test_invalid_ids_and_missing_native_config(self):
        for value in ("../secret", "a" * 31, None):
            with self.assertRaises(ValueError):
                self.service.job_path(value)
        with self.assertRaises(ValueError):
            self.service.submit(experiment.presets()["neutral"], "native")

    def test_crash_partial_stream_is_not_complete(self):
        path = Path(self.temp.name) / "bad.nni"
        path.write_bytes(b"NNI2\x01\0\0\0")
        result = report.inspect(path)
        self.assertFalse(result["integrity_complete"])
        self.assertFalse(result["exact_acceptance_eligible"])

    def test_preview_projects_fields_without_changing_raw_records(self):
        from rocketlaboratory.instrument import stream

        path = Path(self.temp.name) / "wide.nni"
        payload = (
            stream.HEADER.pack(3, 2, 0, 1000, 1, 1)
            + struct.pack("<HBB3f", 1, 1, 3, 1.25, 2.5, 3.75)
            + struct.pack("<HBBf", 60000, 1, 1, 99)
        )
        path.write_bytes(
            stream.MAGIC
            + stream.ENVELOPE.pack(len(payload), zlib.crc32(payload))
            + payload
        )
        result = report.inspect(path)
        self.assertTrue(result["preview_fields_projected"])
        self.assertEqual(
            result["preview"][0]["fields"]["location_uu"], [1.25, 2.5, 3.75]
        )
        self.assertNotIn("unknown_60000", result["preview"][0]["fields"])
        self.assertEqual(next(stream.records(path))["fields"]["unknown_60000"], 99)
        with self.assertRaises(ValueError):
            report.inspect(path, preview_limit=1201)

    def test_mcp_message_budget_is_utf8_bytes(self):
        with self.assertRaises(ValueError):
            mcp.serve(self.service, io.StringIO("é" * 600000 + "\n"), io.StringIO())

    def test_mcp_transport_initialization_errors_and_run(self):
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "rocketlab_run",
                    "arguments": {"experiment": experiment.presets()["neutral"]},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "rocketlab_run",
                    "arguments": {"experiment": {}, "shell": "rm"},
                },
            },
        ]
        writer = io.StringIO()
        mcp.serve(
            self.service,
            io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"),
            writer,
        )
        responses = [json.loads(line) for line in writer.getvalue().splitlines()]
        self.assertEqual(len(responses), 4)
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(len(responses[1]["result"]["tools"]), 8)
        self.assertFalse(responses[2]["result"]["isError"])
        self.assertTrue(responses[3]["result"]["isError"])
        job = json.loads(responses[2]["result"]["content"][0]["text"])
        self.service.wait(job["id"])

    def test_http_same_origin_token_and_real_job(self):
        with WebServer(("127.0.0.1", 0), self.service) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                root = server.origin
                session = json.load(urllib.request.urlopen(root + "/api/session"))

                def post(headers):
                    req = urllib.request.Request(
                        root + "/api/run",
                        json.dumps(
                            {"experiment": experiment.presets()["neutral"]}
                        ).encode(),
                        headers={"Content-Type": "application/json", **headers},
                    )
                    return urllib.request.urlopen(req)

                with self.assertRaises(urllib.error.HTTPError) as raised:
                    post({})
                self.assertEqual(raised.exception.code, 403)
                raised.exception.close()
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    post(
                        {
                            "X-RocketLab-Token": session["token"],
                            "Origin": "https://evil.example",
                        }
                    )
                raised.exception.close()
                job = json.load(
                    post({"X-RocketLab-Token": session["token"], "Origin": root})
                )
                self.service.wait(job["id"])
                result = json.load(
                    urllib.request.urlopen(root + f"/api/jobs/{job['id']}/report")
                )
                self.assertEqual(result["source"], "synthetic_demo")
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(root + "/api/jobs/../../etc/passwd")
                raised.exception.close()
            finally:
                server.shutdown()
                thread.join()


class FakePlugin:
    """Contract fixture. This does not simulate SDK timing, Rocket League or a real mod loader."""

    def __init__(self, folder, spec):
        self.folder = folder
        self.spec = spec
        self.active = False
        self.stage = 0
        self.commands = []
        self.done = threading.Event()
        self.capture = "12345.nni"
        self.thread = threading.Thread(target=self.loop)
        self.thread.start()

    def loop(self):
        while not self.done.is_set():
            path = self.folder / "automation-request.txt"
            if path.exists():
                try:
                    parts = path.read_text().split()
                    nonce, op = parts[1], parts[3]
                    self.commands.append(op)
                    if op == "start":
                        self.active = True
                    if op == "tape":
                        self.tape = (self.folder / parts[4]).read_text()
                        demo.run(self.spec, self.folder / self.capture, lambda: False)
                        self.stage = 3
                    if op == "stop":
                        self.active = False
                    atomic(
                        self.folder / "automation-result.json",
                        json.dumps(
                            {
                                "nonce": nonce,
                                "operation": op,
                                "success": True,
                                "capture_active": self.active,
                                "controller_stage": self.stage,
                                "capture_file": self.capture,
                                "game_context": {"offlineGateSnapshotAllowed": True},
                                "actors": [{"car_index": 0, "actor": 7}],
                            }
                        ),
                    )
                    path.unlink(missing_ok=True)
                except Exception as error:
                    self.error = error
                    return
            time.sleep(0.005)

    def close(self):
        self.done.set()
        self.thread.join()


class NativeContractTests(unittest.TestCase):
    def test_native_attach_protocol_capture_ownership_and_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "bridge"
            folder.mkdir()
            Bridge(folder).arm()
            spec = experiment.presets()["throttle-step"]
            fake = FakePlugin(folder, spec)
            service = Service(Path(temp) / "work", folder)
            try:
                state = service.wait(service.submit(spec, "native")["id"])
                self.assertEqual(state["state"], "complete", state)
                self.assertEqual(fake.commands[:3], ["actors", "start", "tape"])
                self.assertEqual(fake.commands[-1], "stop")
                self.assertEqual(fake.tape.splitlines()[1], "7")
                self.assertFalse(fake.active)
                self.assertFalse((folder / fake.capture).exists())
                self.assertFalse(
                    service.result(state["id"])["exact_acceptance_eligible"]
                )
            finally:
                service.close()
                fake.close()

    def test_rearm_does_not_stop_an_existing_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            bridge = Bridge(folder)
            bridge.arm()
            before = (folder / "automation-enable.txt").read_text()
            fake = FakePlugin(folder, experiment.presets()["neutral"])
            fake.active = True
            try:
                with self.assertRaises(RuntimeError):
                    bridge.arm()
                self.assertEqual((folder / "automation-enable.txt").read_text(), before)
                self.assertEqual(fake.commands, ["actors"])
                self.assertTrue(fake.active)
            finally:
                fake.close()

    def test_existing_native_capture_is_not_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "bridge"
            folder.mkdir()
            Bridge(folder).arm()
            spec = experiment.presets()["neutral"]
            fake = FakePlugin(folder, spec)
            fake.active = True
            service = Service(Path(temp) / "work", folder)
            try:
                state = service.wait(service.submit(spec, "native")["id"])
                self.assertEqual(state["state"], "failed")
                self.assertIn("Another native capture", state["error"])
                self.assertEqual(fake.commands, ["actors"])
                self.assertTrue(fake.active)
            finally:
                service.close()
                fake.close()

    def test_session_expiry_file_bounds_and_cross_workspace_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge = Bridge(temp)
            bridge.arm(10)
            self.assertGreater(bridge.check(), 0)
            with lease(temp):
                with self.assertRaises(RuntimeError):
                    with lease(temp):
                        pass
            atomic(Path(temp) / "automation-enable.txt", "NNIA1 a 1\n")
            with self.assertRaises(RuntimeError):
                bridge.check()
            with self.assertRaises(ValueError):
                bridge.command("tape", "../x")
            with self.assertRaises(ValueError):
                bridge.command("console")

    def test_timeout_is_failure_not_ack(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge = Bridge(temp, timeout=0.05)
            bridge.arm()
            with self.assertRaises(TimeoutError):
                bridge.command("actors")
