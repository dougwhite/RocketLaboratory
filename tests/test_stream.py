import io
import gzip
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from rocketlaboratory.instrument import stream


def encoded(sequence=0, payload=b"", kind=3, phase=2, actor=7):
    body = stream.HEADER.pack(kind, phase, sequence, 1234, 99, actor) + payload
    return struct.pack("<II", len(body), zlib.crc32(body)) + body


def camera_target_fields(present=True, engine=True):
    fields = {
        52: (2, [11 if present else 0]),
        200: (2, [int(present)]),
        201: (2, [11 if present else 0]),
        206: (2, [int(engine)]),
    }
    if present:
        for key, (scalar, count) in stream.CAMERA_TARGET_LAYOUTS.items():
            if key not in (200, 201, 206, 240):
                fields[key] = (scalar, [0] * count)
        fields[202] = (1, [20, 30, 40])
        fields[210] = (1, [1, 0, 0, 0])
        fields[220] = (1, [0.5, 0.5, -0.5, 0.5])
        fields[230] = (1, [0, 1, 0, 0])
    if engine:
        fields[240] = (1, [0.125])
    return fields


def encode_fields(fields):
    return b"".join(
        struct.pack("<HBB", key, scalar, len(values))
        + struct.pack("<" + {1: "f", 2: "I", 3: "i"}[scalar] * len(values), *values)
        for key, (scalar, values) in fields.items()
    )


def body_configuration_fields(mask=7):
    fields = {
        250: (2, [int(mask == 7)]),
        251: (2, [7]),
        255: (2, [mask]),
        256: (2, [7]),
        257: (2, [1]),
        84: (2, [9]),
    }
    if mask & 1:
        fields[252] = (3, [123])
    if mask & 2:
        fields[253] = (1, [60, 40, 15])
    if mask & 4:
        fields[254] = (1, [-5, 0, 20])
    return fields


def jump_configuration_fields(mask=511, present=True):
    fields = {
        260: (2, [int(present)]),
        261: (2, [int(mask == 511)]),
        262: (2, [7]),
        263: (2, [511 if present else 0]),
        264: (2, [mask]),
        265: (2, [1]),
        84: (2, [9]),
    }
    for i in range(9):
        if mask & (1 << i):
            fields[270 + i] = (1, [(-1 if i % 2 else 1) * (0.125 + i)])
    return fields


class StreamTests(unittest.TestCase):
    def read(self, raw):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.nni"
            p.write_bytes(raw)
            return list(stream.records(p))

    def test_original_float_integer_precision(self):
        payload = struct.pack("<HBB3f", 1, 1, 3, 1.25, -2, 3) + struct.pack(
            "<HBB3i", 2, 3, 3, 65535, -32768, 123
        )
        a = self.read(stream.MAGIC + encoded(payload=payload))[0]
        self.assertEqual(a["fields"]["rotation_unreal_integer"], [65535, -32768, 123])
        self.assertEqual(a["fields"]["location_uu"], [1.25, -2, 3])

    def test_body_configuration_sources_identity_and_native_precision(self):
        row = self.read(
            stream.MAGIC + encoded(payload=encode_fields(body_configuration_fields()))
        )[0]
        self.assertEqual(row["fields"]["loadout_body_native_id"], 123)
        self.assertEqual(row["fields"]["body_configuration_actor"], row["actor"])
        self.assertEqual(row["fields"]["body_configuration_registry_generation"], 1)
        self.assertEqual(row["fields"]["local_collision_extent_uu"], [60, 40, 15])
        self.assertEqual(row["fields"]["local_collision_offset_uu"], [-5, 0, 20])
        self.assertEqual(row["fields"]["body_configuration_source_mask"], 7)
        self.assertEqual(row["fields"]["callback_sequence"], 9)

    def test_jump_configuration_complete_separate_sources_and_native_values(self):
        fields = body_configuration_fields()
        fields.update(jump_configuration_fields())
        row = self.read(stream.MAGIC + encoded(payload=encode_fields(fields)))[0]
        f = row["fields"]
        self.assertEqual(f["jump_configuration_actor"], row["actor"])
        self.assertEqual(f["jump_configuration_component_present"], 1)
        self.assertEqual(f["jump_configuration_complete_present"], 1)
        self.assertEqual(f["jump_configuration_attempted_mask"], 511)
        self.assertEqual(f["jump_configuration_valid_mask"], 511)
        self.assertEqual(
            f["jump_configuration_registry_generation"],
            f["body_configuration_registry_generation"],
        )
        self.assertEqual(f["jump_configuration_jump_impulse_native"], -1.125)
        self.assertEqual(f["jump_configuration_max_jump_height_time_native"], 8.125)

    def test_jump_configuration_null_and_every_partial_mask_are_not_defaults(self):
        for mask in range(512):
            f = self.read(
                stream.MAGIC
                + encoded(payload=encode_fields(jump_configuration_fields(mask)))
            )[0]["fields"]
            self.assertEqual(f["jump_configuration_complete_present"], int(mask == 511))
            self.assertEqual(f["jump_configuration_component_present"], 1)
            for i, name in enumerate(stream.JUMP_CONFIGURATION_NAMES):
                self.assertEqual(
                    "jump_configuration_" + name + "_native" in f, bool(mask & (1 << i))
                )
        f = self.read(
            stream.MAGIC
            + encoded(payload=encode_fields(jump_configuration_fields(0, False)))
        )[0]["fields"]
        self.assertEqual(f["jump_configuration_component_present"], 0)
        self.assertEqual(f["jump_configuration_attempted_mask"], 0)
        self.assertNotIn("jump_configuration_jump_force_native", f)
        old = self.read(stream.MAGIC + encoded())[0]["fields"]
        self.assertNotIn("jump_configuration_component_present", old)

    def test_jump_configuration_layout_presence_provenance_nonfinite_rejects(self):
        corrupt = []
        for key in (*range(260, 266), *range(270, 279), 84):
            f = jump_configuration_fields()
            del f[key]
            corrupt.append(f)
        for key, val in (
            (260, (2, [0])),
            (260, (2, [2])),
            (261, (2, [0])),
            (262, (2, [8])),
            (263, (2, [255])),
            (264, (2, [512])),
            (265, (2, [0])),
            (270, (1, [float("nan")])),
            (271, (1, [float("inf")])),
            (270, (1, [1, 2])),
            (270, (2, [1])),
            (260, (1, [1])),
            (84, (1, [9])),
        ):
            f = jump_configuration_fields()
            f[key] = val
            corrupt.append(f)
        f = jump_configuration_fields(0, False)
        f[270] = (1, [0])
        corrupt.append(f)
        f = jump_configuration_fields()
        f.update(body_configuration_fields())
        f[265] = (2, [2])
        corrupt.append(f)
        for f in corrupt:
            with self.subTest(fields=f), self.assertRaises(stream.CaptureError):
                self.read(stream.MAGIC + encoded(payload=encode_fields(f)))
        for kind, phase, actor in ((5, 2, 7), (7, 1, 7), (3, 3, 7), (3, 2, 0)):
            with self.assertRaises(stream.CaptureError):
                self.read(
                    stream.MAGIC
                    + encoded(
                        payload=encode_fields(jump_configuration_fields()),
                        kind=kind,
                        phase=phase,
                        actor=actor,
                    )
                )

    def test_absent_and_partial_body_configuration_never_default_geometry(self):
        for mask in range(8):
            row = self.read(
                stream.MAGIC
                + encoded(payload=encode_fields(body_configuration_fields(mask)))
            )[0]
            f = row["fields"]
            self.assertEqual(f["body_configuration_present"], int(mask == 7))
            self.assertEqual("loadout_body_native_id" in f, bool(mask & 1))
            self.assertEqual("local_collision_extent_uu" in f, bool(mask & 2))
            self.assertEqual("local_collision_offset_uu" in f, bool(mask & 4))
        old = self.read(stream.MAGIC + encoded())[0]
        self.assertNotIn("body_configuration_present", old["fields"])

    def test_body_configuration_malformed_missing_false_presence_and_identity_reject(
        self,
    ):
        corrupt = []
        for key in (250, 251, 255, 256, 257, 84, 252, 253, 254):
            f = body_configuration_fields()
            del f[key]
            corrupt.append(f)
        for key, value in (
            (250, (2, [0])),
            (251, (2, [8])),
            (252, (3, [0])),
            (252, (3, [-1])),
            (253, (1, [60, 0, 15])),
            (253, (1, [60, -1, 15])),
            (253, (1, [float("nan"), 40, 15])),
            (254, (1, [0, float("inf"), 0])),
            (255, (2, [8])),
            (256, (2, [3])),
            (257, (2, [0])),
            (84, (1, [9])),
            (252, (2, [123])),
            (253, (1, [60, 40])),
        ):
            f = body_configuration_fields()
            f[key] = value
            corrupt.append(f)
        f = body_configuration_fields(0)
        f[253] = (1, [60, 40, 15])
        corrupt.append(f)
        for fields in corrupt:
            with self.subTest(fields=fields), self.assertRaises(stream.CaptureError):
                self.read(stream.MAGIC + encoded(payload=encode_fields(fields)))
        for kind, phase, actor in ((6, 2, 7), (7, 1, 7), (3, 3, 7), (3, 2, 0)):
            with self.assertRaises(stream.CaptureError):
                self.read(
                    stream.MAGIC
                    + encoded(
                        payload=encode_fields(body_configuration_fields()),
                        kind=kind,
                        phase=phase,
                        actor=actor,
                    )
                )

    def test_rb_quaternion_order_float_precision_and_source_separation(self):
        q = [0.7071067690849304, 0.0, 0.0, 0.7071067690849304]
        current = [0.5, 0.5, -0.5, 0.5]
        payload = struct.pack("<HBB4f", 6, 1, 4, *q) + struct.pack(
            "<HBB4f", 13, 1, 4, *current
        )
        payload += struct.pack("<HBBf", 9, 1, 1, 1.25) + struct.pack(
            "<HBBf", 14, 1, 1, 1.5
        )
        record = self.read(stream.MAGIC + encoded(payload=payload))[0]
        self.assertEqual(record["fields"]["rb_state_quaternion_wxyz"], q)
        self.assertEqual(record["fields"]["current_rb_state_quaternion_wxyz"], current)
        self.assertEqual(record["fields"]["rb_state_time_s"], 1.25)
        self.assertEqual(record["fields"]["current_rb_state_time_s"], 1.5)

    def test_truncation_corruption_gaps_and_duplicate_fields(self):
        valid = stream.MAGIC + encoded()
        for raw in [
            valid[:-1],
            valid + b"x",
            stream.MAGIC + encoded(1),
            valid[:-1] + bytes([valid[-1] ^ 1]),
            stream.MAGIC + encoded(payload=struct.pack("<HBBff", 1, 1, 1, 1, 2)),
            stream.MAGIC + encoded(payload=struct.pack("<HBBf", 1, 1, 1, 1) * 2),
        ]:
            with self.subTest(raw=raw), self.assertRaises(stream.CaptureError):
                self.read(raw)

    def test_old_state_render_alpha_preserve_source_fields_without_clamping(self):
        payload = struct.pack("<HBB4f", 150, 1, 4, 1, 0, 0, 0) + struct.pack(
            "<HBBf", 151, 1, 1, 0
        )
        payload += struct.pack("<HBB3f", 152, 1, 3, 1, 2, 3) + struct.pack(
            "<HBBI", 155, 2, 1, 3
        )
        payload += struct.pack("<HBBf", 156, 1, 1, 1.25)
        record = self.read(stream.MAGIC + encoded(payload=payload))[0]
        self.assertEqual(record["fields"]["old_rb_state_quaternion_wxyz"], [1, 0, 0, 0])
        self.assertEqual(record["fields"]["old_rb_state_time_s"], 0)
        self.assertEqual(record["fields"]["old_rb_state_location_uu"], [1, 2, 3])
        self.assertEqual(record["fields"]["old_rb_state_flags"], 3)
        self.assertEqual(record["fields"]["engine_render_alpha"], 1.25)
        self.assertNotIn("rb_state_location_uu", record["fields"])

    def test_input_parameter_base_car_and_replica_sources_do_not_alias(self):
        payload = struct.pack("<HBBf", 101, 1, 1, 64 / 127) + struct.pack(
            "<HBBf", 161, 1, 1, 0.5
        )
        payload += struct.pack("<HBBf", 171, 1, 1, 0.25) + struct.pack(
            "<HBBI", 167, 2, 1, 16
        )
        payload += struct.pack("<HBBI", 177, 2, 1, 2) + struct.pack(
            "<HBBI", 178, 2, 1, 255
        )
        payload += struct.pack("<HBBI", 179, 2, 1, 192) + struct.pack(
            "<HBBI", 180, 2, 1, 1
        )
        fields = self.read(stream.MAGIC + encoded(payload=payload, kind=2))[0]["fields"]
        self.assertNotEqual(fields["steer_input"], fields["post_parameter_steer_input"])
        self.assertEqual(fields["post_parameter_steer_input"], 0.5)
        self.assertEqual(fields["vehicle_base_steer_input"], 0.25)
        self.assertEqual(fields["post_parameter_input_button_bits"], 16)
        self.assertEqual(fields["vehicle_base_input_button_bits"], 2)
        self.assertEqual(fields["replicated_throttle_byte"], 255)
        self.assertEqual(fields["replicated_steer_byte"], 192)
        self.assertEqual(fields["post_parameter_available"], 1)

    def test_camera_targets_keep_pov_header_identity_and_three_quaternion_sources(self):
        fields = camera_target_fields()
        fields[1] = (1, [900, 100, 80])
        fields[2] = (3, [7, 8, 9])
        fields[50] = (1, [107])
        row = self.read(
            stream.MAGIC
            + encoded(payload=encode_fields(fields), kind=6, phase=3, actor=0)
        )[0]
        self.assertEqual(row["kind"], "camera")
        self.assertEqual(row["actor"], 0)
        self.assertEqual(row["phase"], 3)
        self.assertEqual(row["fields"]["camera_target_actor"], 11)
        self.assertEqual(row["fields"]["location_uu"], [900, 100, 80])
        self.assertEqual(row["fields"]["camera_target_actor_location_uu"], [20, 30, 40])
        self.assertEqual(
            row["fields"]["camera_target_rb_state_quaternion_wxyz"], [1, 0, 0, 0]
        )
        self.assertEqual(
            row["fields"]["camera_target_current_rb_state_quaternion_wxyz"],
            [0.5, 0.5, -0.5, 0.5],
        )
        self.assertEqual(
            row["fields"]["camera_target_old_rb_state_quaternion_wxyz"], [0, 1, 0, 0]
        )
        self.assertEqual(row["fields"]["camera_target_engine_render_alpha"], 0.125)

    def test_absent_camera_target_does_not_manufacture_state(self):
        for engine in (True, False):
            row = self.read(
                stream.MAGIC
                + encoded(
                    payload=encode_fields(camera_target_fields(False, engine)),
                    kind=6,
                    phase=3,
                    actor=0,
                )
            )[0]
            self.assertEqual(row["fields"]["camera_target_present"], 0)
            self.assertEqual(row["fields"]["camera_target_actor"], 0)
            self.assertNotIn("camera_target_actor_location_uu", row["fields"])
            self.assertEqual(
                "camera_target_engine_render_alpha" in row["fields"], engine
            )

    def test_camera_target_bad_layout_incomplete_or_false_presence_rejects(self):
        corrupt = []
        fields = camera_target_fields()
        fields[210] = (1, [1, 0, 0])
        corrupt.append(fields)
        fields = camera_target_fields()
        fields[201] = (1, [11])
        corrupt.append(fields)
        fields = camera_target_fields()
        del fields[233]
        corrupt.append(fields)
        fields = camera_target_fields()
        fields[52] = (2, [12])
        corrupt.append(fields)
        fields = camera_target_fields()
        fields[200] = (2, [0])
        corrupt.append(fields)
        fields = camera_target_fields(False)
        fields[202] = (1, [0, 0, 0])
        corrupt.append(fields)
        fields = camera_target_fields()
        del fields[240]
        corrupt.append(fields)
        fields = camera_target_fields()
        del fields[200]
        corrupt.append(fields)
        fields = camera_target_fields()
        del fields[206]
        corrupt.append(fields)
        for fields in corrupt:
            with self.subTest(fields=fields), self.assertRaises(stream.CaptureError):
                self.read(
                    stream.MAGIC
                    + encoded(payload=encode_fields(fields), kind=6, phase=3, actor=0)
                )
        for phase, actor in ((2, 0), (3, 11)):
            with self.assertRaises(stream.CaptureError):
                self.read(
                    stream.MAGIC
                    + encoded(
                        payload=encode_fields(camera_target_fields()),
                        kind=6,
                        phase=phase,
                        actor=actor,
                    )
                )
        with self.assertRaises(stream.CaptureError):
            self.read(
                stream.MAGIC
                + encoded(
                    payload=encode_fields(camera_target_fields()),
                    kind=2,
                    phase=3,
                    actor=0,
                )
            )

    def test_nonfinite_and_oversize(self):
        for raw in [
            stream.MAGIC + encoded(payload=struct.pack("<HBBf", 1, 1, 1, float("nan"))),
            stream.MAGIC + struct.pack("<II", 2**31, 0),
        ]:
            with self.assertRaises(stream.CaptureError):
                self.read(raw)

    def test_total_payload_budget_also_bounds_compressed_input(self):
        raw = stream.MAGIC + encoded() + encoded(1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bounded.nni.gz"
            with gzip.open(path, "wb") as out:
                out.write(raw)
            with self.assertRaisesRegex(stream.CaptureError, "byte budget exceeded"):
                list(stream.records(path, max_payload_bytes=stream.HEADER.size))
            self.assertEqual(
                len(
                    list(stream.records(path, max_payload_bytes=2 * stream.HEADER.size))
                ),
                2,
            )

    def test_pad_native_precision_and_unknown_timer(self):
        payload = (
            struct.pack("<HBBf", 140, 1, 1, 0.125)
            + struct.pack("<HBBI", 142, 2, 1, 1)
            + struct.pack("<HBBf", 144, 1, 1, 10.0)
            + struct.pack("<HBBI", 147, 2, 1, 0)
        )
        record = self.read(stream.MAGIC + encoded(payload=payload, kind=10))[0]
        self.assertEqual(record["kind"], "pad")
        self.assertEqual(record["fields"]["pad_boost_amount_native"], 0.125)
        self.assertEqual(record["fields"]["pad_respawn_delay_config_s"], 10.0)
        self.assertEqual(record["fields"]["pad_remaining_timer_available"], 0)
        self.assertNotIn("pad_remaining_timer_s", record["fields"])

    def test_local_identity_and_inventory_completeness(self):
        payload = struct.pack("<HBBI", 130, 2, 1, 17) + struct.pack(
            "<HBBI", 149, 2, 1, 0
        )
        record = self.read(stream.MAGIC + encoded(payload=payload))[0]
        self.assertEqual(record["fields"]["capture_local_player_alias"], 17)
        self.assertEqual(record["fields"]["pad_inventory_complete"], 0)
        self.assertNotIn("account_id", record["fields"])

    def test_terminal_quality_required(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.nni"
            p.write_bytes(stream.MAGIC + encoded())
            self.assertFalse(stream.inspect(p)["exact_acceptance_eligible"])
            fields = b"".join(
                struct.pack("<HBBI", key, 2, 1, value)
                for key, value in [(80, 0), (81, 1), (82, 0)]
            )
            p.write_bytes(stream.MAGIC + encoded() + encoded(1, fields, 8))
            self.assertFalse(stream.inspect(p)["exact_acceptance_eligible"])
            fields = b"".join(
                struct.pack("<HBBI", key, 2, 1, value)
                for key, value in [(80, 0), (81, 1), (82, 1)]
            )
            p.write_bytes(stream.MAGIC + encoded() + encoded(1, fields, 8))
            self.assertTrue(stream.inspect(p)["exact_acceptance_eligible"])


if __name__ == "__main__":
    unittest.main()
