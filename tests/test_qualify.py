import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rocketlaboratory.instrument import qualify


def fixture():
    tape = {
        "schema": "noodle.instrument.tape.v1",
        "duration_steps": 3,
        "actors": {
            "1": [
                {
                    "start_step": 0,
                    "duration_steps": 3,
                    "controls": {"throttle": 1, "steer": 0.2},
                }
            ]
        },
    }
    rows = []

    def row(kind, frame, fields, actor=0, phase=2):
        rows.append(
            dict(
                kind=kind,
                engine_frame=frame,
                fields=fields,
                actor=actor,
                phase=phase,
                sequence=len(rows),
                host_monotonic_ns=frame * 1000000,
            )
        )

    row("event", 9, dict(event_code=10, controller_stage=2))
    for frame in range(10, 13):
        row("world", frame, {})
        values = {key: 0 for key in qualify.AXES}
        values.update(
            throttle=1,
            steer_input=0.20000000298023224,
            input_button_bits=0,
            callback_sequence=frame,
        )
        row("input_requested", frame, values.copy(), 1, 1)
        row("input_consumed", frame, values.copy(), 1)
        row(
            "body",
            frame,
            dict(location_uu=[(frame - 10) * 10, 0, 18], velocity_uu_s=[100, 0, 0]),
            1,
        )
    row("event", 13, dict(event_code=10, controller_stage=3))
    row(
        "quality",
        13,
        dict(
            writer_valid=1,
            drop_count=0,
            experiment_failed=0,
            controller_stage=3,
            timing_qualified=0,
        ),
        phase=4,
    )
    return rows, tape


class QualificationTests(unittest.TestCase):
    def assess(
        self, rows=None, tape=None, observed="Stadium_P", motion_policy="planar"
    ):
        default_rows, default_tape = fixture()
        return qualify.assess(
            default_rows if rows is None else rows,
            default_tape if tape is None else tape,
            "Stadium_P",
            observed,
            motion_policy,
        )

    def neutral_fixture(self):
        rows, tape = fixture()
        tape["actors"]["1"][0]["controls"] = {}
        for row in rows:
            if row["kind"] in ("input_requested", "input_consumed"):
                row["fields"].update(throttle=0, steer_input=0)
            if row["kind"] == "body":
                row["fields"].update(location_uu=[0, 0, 18], velocity_uu_s=[0, 0, 0])
        return rows, tape

    def test_neutral_baseline_is_observation_never_nonzero_delivery(self):
        rows, tape = self.neutral_fixture()
        default = self.assess(rows, tape)
        self.assertEqual(default["status"], "observable_delivery_checks_failed")
        self.assertEqual(default["input_echo"]["status"], "not_tested")
        report = self.assess(rows, tape, motion_policy="neutral_baseline")
        self.assertEqual(report["status"], "observable_neutral_baseline_checks_pass")
        self.assertTrue(report["motion_policy"]["neutral_baseline_observed"])
        self.assertEqual(report["authored_echo"]["status"], "exact_matched")
        self.assertFalse(report["authored_echo"]["verified_nonzero_delivery"])
        self.assertFalse(report["motion_policy"]["physics_acceptance_qualified"])
        self.assertFalse(report["motion_policy"]["solver_authority_qualified"])
        self.assertFalse(report["exact_acceptance_eligible"])
        self.assertFalse(report["timing"]["independently_validated"])
        self.assertEqual(
            self.assess(motion_policy="neutral_baseline")["status"],
            "observable_neutral_baseline_checks_failed",
        )

    def test_vertical_jump_echo_motion_is_distinct_from_default_planar(self):
        rows, tape = self.neutral_fixture()
        tape["actors"]["1"][0]["controls"] = {"jump": True}
        for row in rows:
            if row["kind"] in ("input_requested", "input_consumed"):
                row["fields"]["input_button_bits"] = 1 << 1
            if row["kind"] == "body":
                row["fields"].update(
                    location_uu=[0, 0, 18 + 10 * (row["engine_frame"] - 10)],
                    velocity_uu_s=[0, 0, 100],
                )
        default = self.assess(rows, tape)
        self.assertEqual(default["input_echo"]["status"], "nonzero_matched")
        self.assertEqual(default["status"], "observable_delivery_checks_failed")
        report = self.assess(rows, tape, motion_policy="vertical")
        self.assertEqual(report["status"], "observable_delivery_checks_pass")
        self.assertEqual(report["movement"]["1"]["max_planar_displacement_uu"], 0)
        self.assertEqual(report["movement"]["1"]["max_vertical_displacement_uu"], 20)
        self.assertEqual(report["authored_echo"]["status"], "exact_matched")
        self.assertFalse(report["motion_policy"]["physics_acceptance_qualified"])
        self.assertFalse(report["exact_acceptance_eligible"])

    def test_explicit_policies_fail_closed_on_body_coverage_identity_and_input(self):
        for defect in (
            "missing_body",
            "wrong_body_actor",
            "duplicate_body",
            "malformed_body",
            "missing_consumed",
            "mismatched_consumed",
            "missing_world",
            "missing_requested",
            "bad_terminal",
        ):
            rows, tape = self.neutral_fixture()
            body = next(row for row in rows if row["kind"] == "body")
            if defect == "missing_body":
                rows.remove(body)
            elif defect == "wrong_body_actor":
                body["actor"] = 2
            elif defect == "duplicate_body":
                rows.insert(-1, copy.deepcopy(body))
            elif defect == "malformed_body":
                body["fields"]["velocity_uu_s"] = [0, 0]
            elif defect == "missing_consumed":
                rows.remove(
                    next(row for row in rows if row["kind"] == "input_consumed")
                )
            elif defect == "mismatched_consumed":
                next(row for row in rows if row["kind"] == "input_consumed")["fields"][
                    "throttle"
                ] = 1
            elif defect == "missing_world":
                rows.remove(next(row for row in rows if row["kind"] == "world"))
            elif defect == "missing_requested":
                rows.remove(
                    next(row for row in rows if row["kind"] == "input_requested")
                )
            elif defect == "bad_terminal":
                rows[-1]["fields"]["drop_count"] = 1
            with self.subTest(defect=defect):
                report = self.assess(rows, tape, motion_policy="neutral_baseline")
                self.assertEqual(
                    report["status"], "observable_neutral_baseline_checks_failed"
                )
                self.assertFalse(report["motion_policy"]["neutral_baseline_observed"])
                self.assertFalse(report["authored_echo"]["verified_nonzero_delivery"])

    def test_neutral_policy_does_not_require_stationary_state_or_hide_map_failure(self):
        rows, tape = self.neutral_fixture()
        for row in rows:
            if row["kind"] == "body":
                row["fields"].update(
                    location_uu=[0, 0, 100 - 10 * (row["engine_frame"] - 10)],
                    velocity_uu_s=[0, 0, -100],
                )
        self.assertEqual(
            self.assess(rows, tape, motion_policy="neutral_baseline")["status"],
            "observable_neutral_baseline_checks_pass",
        )
        report = self.assess(
            rows, tape, observed="ARC_Darc_P", motion_policy="neutral_baseline"
        )
        self.assertEqual(report["authored_echo"]["status"], "exact_matched")
        self.assertEqual(report["capture_checks"]["status"], "failed")
        self.assertEqual(report["status"], "observable_neutral_baseline_checks_failed")

    def test_unknown_policy_is_rejected_without_relaxing_default(self):
        with self.assertRaisesRegex(ValueError, "Unknown motion policy"):
            self.assess(motion_policy="anything")

    def test_vertical_requires_displacement_speed_and_complete_body_coverage(self):
        for defect in (
            "planar_only",
            "vertical_velocity_only",
            "vertical_displacement_only",
            "missing_body",
        ):
            rows, tape = fixture()
            for row in rows:
                if row["kind"] == "body" and defect != "planar_only":
                    row["fields"].update(
                        location_uu=[0, 0, 18 + 10 * (row["engine_frame"] - 10)],
                        velocity_uu_s=[0, 0, 100],
                    )
                    if defect == "vertical_velocity_only":
                        row["fields"]["location_uu"][2] = 18
                    if defect == "vertical_displacement_only":
                        row["fields"]["velocity_uu_s"][2] = 0
            if defect == "missing_body":
                rows.remove(next(row for row in rows if row["kind"] == "body"))
            with self.subTest(defect=defect):
                report = self.assess(rows, tape, motion_policy="vertical")
                self.assertEqual(report["authored_echo"]["status"], "exact_matched")
                self.assertEqual(report["status"], "observable_delivery_checks_failed")
                self.assertFalse(report["authored_echo"]["verified_nonzero_delivery"])

    def test_policy_cannot_ignore_a_scheduled_neutral_actor(self):
        rows, tape = fixture()
        tape["actors"]["2"] = []
        for row in list(rows):
            if row["kind"] in ("input_requested", "input_consumed"):
                other = copy.deepcopy(row)
                other["actor"] = 2
                other["fields"].update(throttle=0, steer_input=0)
                rows.insert(-1, other)
        report = self.assess(rows, tape, motion_policy="vertical")
        self.assertEqual(
            report["schedule"]["status"], "completed_and_requested_matches"
        )
        self.assertEqual(report["authored_echo"]["status"], "exact_matched")
        self.assertEqual(report["movement"]["2"]["body_samples"], 0)
        self.assertEqual(report["status"], "observable_delivery_checks_failed")

    def test_delivery_can_pass_but_atomic_exact_acceptance_stays_false(self):
        report = self.assess()
        self.assertEqual(report["status"], "observable_delivery_checks_pass")
        self.assertEqual(report["input_echo"]["nonzero_matched"], 3)
        self.assertEqual(report["timing"]["status"], "unqualified")
        self.assertFalse(report["timing"]["independently_validated"])
        self.assertFalse(report["exact_acceptance_eligible"])

    def test_done_does_not_override_missing_delivery_or_stationary_actor(self):
        rows, _ = fixture()
        for row in rows:
            if row["kind"] == "input_consumed":
                row["fields"].update(throttle=0, steer_input=0)
            if row["kind"] == "body":
                row["fields"].update(location_uu=[0, 0, 18], velocity_uu_s=[0, 0, 0])
        report = self.assess(rows)
        self.assertTrue(report["schedule"]["scheduler_done"])
        self.assertEqual(report["input_echo"]["mismatched_pairs"], 3)
        self.assertEqual(
            report["input_echo"]["mismatches_by_control"],
            {"throttle": 3, "steer_input": 3},
        )
        self.assertEqual(report["movement"]["1"]["status"], "not_observed")
        self.assertEqual(report["status"], "observable_delivery_checks_failed")

    def test_engine_jumped_history_change_does_not_reject_authored_pulse(self):
        rows, tape = fixture()
        tape["actors"]["1"][0]["controls"]["jump"] = True
        for row in rows:
            if row["kind"] == "input_requested":
                row["fields"]["input_button_bits"] = 1 << 1
            if row["kind"] == "input_consumed":
                row["fields"]["input_button_bits"] = (1 << 1) | (1 << 4)
        report = self.assess(rows, tape)
        self.assertEqual(report["status"], "observable_delivery_checks_pass")
        self.assertEqual(report["input_echo"]["nonzero_matched"], 3)
        self.assertEqual(report["input_echo"]["engine_jumped_changed_pairs"], 3)
        self.assertEqual(report["input_echo"]["twelve_channel_changed_pairs"], 3)
        self.assertFalse(report["exact_acceptance_eligible"])

    def test_missing_tick_and_missing_consumed_have_separate_failures(self):
        rows, _ = fixture()
        rows = [
            row
            for row in rows
            if not (
                row["engine_frame"] == 11 and row["kind"] in ("world", "input_consumed")
            )
        ]
        report = self.assess(rows)
        self.assertEqual(report["frames"]["status"], "missing_or_duplicate")
        self.assertEqual(report["input_echo"]["missing_consumed"], 1)
        self.assertEqual(
            report["schedule"]["status"], "completed_and_requested_matches"
        )

    def test_no_retiming_of_callback_or_frame_to_manufacture_matches(self):
        rows, _ = fixture()
        for row in rows:
            if row["kind"] == "input_consumed":
                row["fields"]["callback_sequence"] += 1
        self.assertEqual(self.assess(rows)["input_echo"]["missing_consumed"], 3)

    def test_map_mismatch_unknown_and_declared_timing_are_independent(self):
        report = self.assess(observed="ARC_Darc_P")
        self.assertEqual(report["map"]["status"], "mismatched")
        self.assertEqual(report["input_echo"]["status"], "nonzero_matched")
        self.assertEqual(self.assess(observed=None)["map"]["status"], "unknown")
        rows, _ = fixture()
        rows[-1]["fields"]["timing_qualified"] = 1
        report = self.assess(rows)
        self.assertEqual(report["timing"]["status"], "source_declares_qualified")
        self.assertFalse(report["exact_acceptance_eligible"])

    def test_schedule_missing_or_wrong_requested_step(self):
        rows, _ = fixture()
        rows = [
            row
            for row in rows
            if not (row["kind"] == "input_requested" and row["engine_frame"] == 11)
        ]
        self.assertEqual(self.assess(rows)["schedule"]["missing_requested_steps"], 1)
        rows, _ = fixture()
        next(row for row in rows if row["kind"] == "input_requested")["fields"][
            "throttle"
        ] = 0.5
        self.assertEqual(self.assess(rows)["schedule"]["mismatch_count"], 1)

    def test_quaternion_sources_and_constant_state_time_remain_separate(self):
        rows, _ = fixture()
        for row in rows:
            if row["kind"] == "body":
                row["fields"].update(
                    rb_state_quaternion_wxyz=[1, 0, 0, 0],
                    current_rb_state_quaternion_wxyz=[1, 0, 0, 0],
                    rb_state_time_s=0,
                    current_rb_state_time_s=0,
                    physics_time_s=row["engine_frame"] / 120,
                    rb_state_location_uu=[100, 0, 18],
                    current_rb_state_location_uu=[100, 0, 18],
                )
        source = self.assess(rows)["body_sources"]["1"]
        self.assertEqual(
            source["quaternions"]["rb_state_quaternion_wxyz"]["norm_min"], 1
        )
        self.assertTrue(source["times"]["rb_state_time_s"]["constant"])
        self.assertFalse(source["times"]["physics_time_s"]["constant"])
        self.assertEqual(
            source["source_pairs"][
                "rb_state_location_uu vs current_rb_state_location_uu"
            ]["different"],
            0,
        )
        self.assertEqual(
            source["source_pairs"]["location_uu vs rb_state_location_uu"]["different"],
            3,
        )
        self.assertEqual(
            self.assess()["body_sources"]["1"]["quaternions"][
                "rb_state_quaternion_wxyz"
            ]["samples"],
            0,
        )

    def test_same_tuple_integrator_diagnostic_uses_explicit_native_dt(self):
        rows, _ = fixture()
        dt = 1 / 120
        for row in rows:
            if row["kind"] == "world":
                row["fields"]["configured_fixed_physics_dt_s"] = dt
            if row["kind"] == "body":
                n = row["engine_frame"] - 10
                row["fields"].update(
                    rb_state_location_uu=[n * (n + 1) * dt, 0, 18],
                    rb_state_velocity_uu_s=[2 * n, 0, 0],
                )
        source = self.assess(rows)["body_sources"]["1"]["integration"]["rb_state_"][
            "residuals"
        ]
        self.assertAlmostEqual(source["next_velocity"]["rms_vector_uu"], 0)
        self.assertGreater(source["previous_velocity"]["rms_vector_uu"], 0.01)
        self.assertEqual(source["next_velocity"]["samples"], 2)
        rows = [row for row in rows if row["engine_frame"] != 11]
        source = self.assess(rows)["body_sources"]["1"]["integration"]["rb_state_"][
            "residuals"
        ]
        self.assertEqual(source["next_velocity"]["samples"], 0)
        self.assertFalse(self.assess()["body_sources"]["1"]["integration"])

    def test_render_interpolation_formula_is_an_explicit_untested_hypothesis(self):
        rows, _ = fixture()
        for row in rows:
            if row["kind"] == "body":
                row["fields"].update(
                    old_rb_state_location_uu=[0, 0, 18],
                    rb_state_location_uu=[40, 0, 18],
                    location_uu=[10, 0, 18],
                    engine_render_alpha=0.25,
                )
        hypothesis = self.assess(rows)["body_sources"]["1"][
            "render_interpolation_hypothesis"
        ]
        self.assertEqual(hypothesis["rms_vector_uu"], 0)
        self.assertEqual(hypothesis["samples"], 3)
        self.assertFalse(hypothesis["qualified"])
        self.assertEqual(
            self.assess()["body_sources"]["1"]["render_interpolation_hypothesis"][
                "samples"
            ],
            0,
        )

    def test_other_input_sources_do_not_replace_primary_readback(self):
        rows, _ = fixture()
        for row in rows:
            if row["kind"] == "input_consumed":
                original = row["fields"].copy()
                row["fields"].update(
                    {
                        "post_parameter_" + key: original[key]
                        for key in qualify.AXES + ("input_button_bits",)
                    }
                )
                row["fields"]["steer_input"] = 0.25
        report = self.assess(rows)
        self.assertEqual(report["input_echo"]["mismatched_pairs"], 3)
        source = report["other_input_readbacks"]["post_parameter_"]
        self.assertEqual(source["paired_requests"], 3)
        self.assertEqual(source["mismatches_by_authored_control"], {})
        self.assertFalse(source["solver_authority_qualified"])
        self.assertEqual(report["status"], "observable_delivery_checks_failed")

    def test_duplicate_request_or_consumption_and_writer_fail_closed(self):
        for kind in ("input_requested", "input_consumed"):
            rows, _ = fixture()
            duplicate = copy.deepcopy(next(row for row in rows if row["kind"] == kind))
            rows.insert(-1, duplicate)
            report = self.assess(rows)
            self.assertEqual(report["status"], "observable_delivery_checks_failed")
        rows, _ = fixture()
        rows[-1]["fields"]["drop_count"] = 1
        self.assertEqual(self.assess(rows)["integrity"]["status"], "invalid_terminal")
        self.assertEqual(
            self.assess(rows[:-1])["integrity"]["status"], "invalid_terminal"
        )

    def test_file_contract_honors_real_rlbot_map_key_and_record_budget(self):
        rows, tape = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "capture.nni"
            capture.write_bytes(b"private fixture")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"reference": {"map": "Stadium_P"}}))
            definition = root / "tape.json"
            definition.write_text(json.dumps(tape))
            match_path = root / "match.toml"
            match_path.write_text('[match]\ngame_map_upk="Stadium_P"\nmap="WRONG"\n')
            with patch.object(qualify, "records", return_value=iter(rows)):
                report = qualify.diagnose(capture, manifest, definition, match_path)
            self.assertEqual(report["map"]["status"], "matched")
            self.assertEqual(len(report["source_sha256"]), 4)
            with patch.object(qualify, "records", return_value=iter(rows)):
                report = qualify.diagnose(
                    capture, manifest, definition, match_path, max_records=2
                )
            self.assertEqual(report["status"], "rejected")
            self.assertIn("budget exceeded", report["error"])
            capture.write_bytes(b"bad format")
            self.assertEqual(
                qualify.diagnose(capture, manifest, definition, match_path)["status"],
                "rejected",
            )


if __name__ == "__main__":
    unittest.main()
