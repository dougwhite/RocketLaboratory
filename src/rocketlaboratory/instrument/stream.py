#!/usr/bin/env python3
"""Strict streaming reader for the original NNI2 numeric capture (not SDK memory)."""

import argparse
import gzip
import json
import math
import struct
import zlib
from pathlib import Path

MAGIC = b"NNI2\x01\x00\x00\x00"
ENVELOPE = struct.Struct("<II")
HEADER = struct.Struct("<HHQQqI")
KINDS = {
    1: "input_requested",
    2: "input_consumed",
    3: "body",
    4: "wheel",
    5: "mechanic",
    6: "camera",
    7: "event",
    8: "quality",
    9: "world",
    10: "pad",
}
FIELDS = {
    1: "location_uu",
    2: "rotation_unreal_integer",
    3: "velocity_uu_s",
    4: "angular_velocity_rad_s",
    5: "physics_time_s",
    6: "rb_state_quaternion_wxyz",
    9: "rb_state_time_s",
    10: "rb_state_location_uu",
    11: "rb_state_velocity_uu_s",
    12: "rb_state_angular_velocity_rad_s",
    13: "current_rb_state_quaternion_wxyz",
    14: "current_rb_state_time_s",
    15: "current_rb_state_location_uu",
    16: "current_rb_state_velocity_uu_s",
    17: "current_rb_state_angular_velocity_rad_s",
    18: "current_rb_state_flags",
    19: "rb_state_flags",
    7: "team",
    8: "boost_fraction",
    20: "vehicle_flags",
    21: "world_wheel_contacts",
    22: "wheel_contacts",
    23: "vehicle_update_tag",
    24: "ground_normal",
    25: "time_on_ground_s",
    26: "time_off_ground_s",
    27: "world_contact_present",
    28: "world_contact_normal",
    29: "world_contact_location",
    30: "wheel_contact_flags",
    31: "wheel_contact_location",
    32: "wheel_contact_normal",
    33: "wheel_velocity_uu_s",
    34: "suspension_distance_uu",
    35: "spin_speed",
    36: "steer",
    37: "contact_change_time_s",
    38: "friction_curve_input",
    39: "wheel_index",
    40: "component_active",
    41: "component_activity_time_s",
    42: "component_active_time_s",
    43: "component_inactive_time_s",
    44: "dodge_direction",
    45: "component_kind",
    50: "fov_deg",
    51: "current_swivel_unreal_integer",
    52: "camera_owner",
    60: "event_code",
    61: "related_actor",
    62: "registry_generation",
    70: "world_gravity_uu_s2",
    71: "configured_physics_fps",
    72: "world_time_dilation",
    73: "rb_gravity_scaling",
    80: "drop_count",
    81: "writer_valid",
    82: "timing_qualified",
    83: "controller_stage",
    84: "callback_sequence",
    85: "physics_actor_count",
    86: "experiment_failed",
    90: "suspension_stiffness",
    91: "suspension_damping_compression",
    92: "suspension_damping_relaxation",
    93: "suspension_travel",
    94: "wheel_radius",
    95: "sticky_ground",
    96: "sticky_wall",
    100: "throttle",
    101: "steer_input",
    102: "pitch",
    103: "yaw",
    104: "roll",
    105: "dodge_forward",
    106: "dodge_strafe",
    107: "input_button_bits",
    120: "camera_setting_fov",
    121: "camera_setting_height",
    122: "camera_setting_pitch",
    123: "camera_setting_distance",
    124: "camera_setting_stiffness",
    125: "camera_setting_swivel_speed",
    126: "camera_setting_transition_speed",
    130: "capture_local_player_alias",
    131: "native_match_player_id",
    132: "is_bot",
    140: "pad_boost_amount_native",
    141: "pad_boost_type_native",
    142: "pad_picked_up",
    143: "pad_no_pickup",
    144: "pad_respawn_delay_config_s",
    145: "pad_observation_event",
    146: "pad_observation_physics_time_s",
    147: "pad_remaining_timer_available",
    148: "discovered_pad_count",
    149: "pad_inventory_complete",
    74: "configured_fixed_physics_dt_s",
}
FIELDS.update(
    {
        150: "old_rb_state_quaternion_wxyz",
        151: "old_rb_state_time_s",
        152: "old_rb_state_location_uu",
        153: "old_rb_state_velocity_uu_s",
        154: "old_rb_state_angular_velocity_rad_s",
        155: "old_rb_state_flags",
        156: "engine_render_alpha",
    }
)
for first, prefix in ((160, "post_parameter_"), (170, "vehicle_base_")):
    FIELDS.update(
        {
            first + i: prefix + name
            for i, name in enumerate(
                (
                    "throttle",
                    "steer_input",
                    "pitch",
                    "yaw",
                    "roll",
                    "dodge_forward",
                    "dodge_strafe",
                    "input_button_bits",
                )
            )
        }
    )
FIELDS.update(
    {
        178: "replicated_throttle_byte",
        179: "replicated_steer_byte",
        180: "post_parameter_available",
    }
)
FIELDS.update(
    {
        200: "camera_target_present",
        201: "camera_target_actor",
        202: "camera_target_actor_location_uu",
        203: "camera_target_actor_velocity_uu_s",
        204: "camera_target_actor_angular_velocity_rad_s",
        205: "camera_target_actor_rotation_unreal_integer",
        206: "camera_target_engine_present",
        207: "camera_target_physics_time_s",
        240: "camera_target_engine_render_alpha",
    }
)
for first, prefix in (
    (210, "camera_target_rb_state_"),
    (220, "camera_target_current_rb_state_"),
    (230, "camera_target_old_rb_state_"),
):
    FIELDS.update(
        {
            first + i: prefix + name
            for i, name in enumerate(
                (
                    "quaternion_wxyz",
                    "time_s",
                    "location_uu",
                    "velocity_uu_s",
                    "angular_velocity_rad_s",
                    "flags",
                )
            )
        }
    )
CAMERA_TARGET_LAYOUTS = {
    200: (2, 1),
    201: (2, 1),
    202: (1, 3),
    203: (1, 3),
    204: (1, 3),
    205: (3, 3),
    206: (2, 1),
    207: (1, 1),
    240: (1, 1),
}
FIELDS.update(
    {
        250: "body_configuration_present",
        251: "body_configuration_actor",
        252: "loadout_body_native_id",
        253: "local_collision_extent_uu",
        254: "local_collision_offset_uu",
        255: "body_configuration_source_mask",
        256: "body_configuration_attempted_mask",
        257: "body_configuration_registry_generation",
    }
)
BODY_CONFIGURATION_LAYOUTS = {
    250: (2, 1),
    251: (2, 1),
    252: (3, 1),
    253: (1, 3),
    254: (1, 3),
    255: (2, 1),
    256: (2, 1),
    257: (2, 1),
}
JUMP_CONFIGURATION_NAMES = (
    "min_jump_time",
    "jump_impulse",
    "jump_force",
    "jump_force_time",
    "podium_jump_force_time",
    "jump_impulse_speed",
    "jump_accel",
    "max_jump_height",
    "max_jump_height_time",
)
FIELDS.update(
    {
        260: "jump_configuration_component_present",
        261: "jump_configuration_complete_present",
        262: "jump_configuration_actor",
        263: "jump_configuration_attempted_mask",
        264: "jump_configuration_valid_mask",
        265: "jump_configuration_registry_generation",
    }
)
FIELDS.update(
    {
        270 + i: "jump_configuration_" + name + "_native"
        for i, name in enumerate(JUMP_CONFIGURATION_NAMES)
    }
)
JUMP_CONFIGURATION_LAYOUTS = {key: (2, 1) for key in range(260, 266)}
JUMP_CONFIGURATION_LAYOUTS.update({key: (1, 1) for key in range(270, 279)})
for first in (210, 220, 230):
    CAMERA_TARGET_LAYOUTS.update(
        {
            first + i: layout
            for i, layout in enumerate(((1, 4), (1, 1), (1, 3), (1, 3), (1, 3), (2, 1)))
        }
    )


class CaptureError(ValueError):
    pass


def records(path, *, max_payload_bytes=None):
    """Bounded record allocation, CRC, truncation, TLV and sequence validation."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as source:
        if source.read(8) != MAGIC:
            raise CaptureError("unsupported stream header")
        expected = 0
        payload_bytes = 0
        while True:
            envelope = source.read(8)
            if not envelope:
                break
            if len(envelope) != 8:
                raise CaptureError("truncated envelope")
            size, crc = ENVELOPE.unpack(envelope)
            if not HEADER.size <= size <= 65536:
                raise CaptureError("record length out of bounds")
            payload_bytes += size
            if max_payload_bytes is not None and payload_bytes > max_payload_bytes:
                raise CaptureError("Decoded payload byte budget exceeded")
            raw = source.read(size)
            if len(raw) != size:
                raise CaptureError("truncated record")
            if zlib.crc32(raw) != crc:
                raise CaptureError("CRC mismatch")
            kind, phase, sequence, host_ns, engine_frame, actor = HEADER.unpack_from(
                raw
            )
            if kind not in KINDS:
                raise CaptureError("unknown kind")
            if sequence != expected:
                raise CaptureError("missing or reordered sequence")
            expected += 1
            values = {}
            offset = HEADER.size
            while offset < size:
                if offset + 4 > size:
                    raise CaptureError("truncated field header")
                key, scalar, count = struct.unpack_from("<HBB", raw, offset)
                offset += 4
                if scalar not in (1, 2, 3) or not 1 <= count <= 16:
                    raise CaptureError("bad scalar/count")
                if (
                    key in CAMERA_TARGET_LAYOUTS
                    and (scalar, count) != CAMERA_TARGET_LAYOUTS[key]
                ):
                    raise CaptureError("Invalid camera target field representation")
                if (
                    key in BODY_CONFIGURATION_LAYOUTS
                    and (scalar, count) != BODY_CONFIGURATION_LAYOUTS[key]
                ):
                    raise CaptureError(
                        "Invalid body configuration field representation"
                    )
                if (
                    key in JUMP_CONFIGURATION_LAYOUTS
                    and (scalar, count) != JUMP_CONFIGURATION_LAYOUTS[key]
                ):
                    raise CaptureError(
                        "Invalid jump configuration field representation"
                    )
                if offset + 4 * count > size:
                    raise CaptureError("truncated field data")
                field = struct.unpack_from(
                    "<" + {1: "f", 2: "I", 3: "i"}[scalar] * count, raw, offset
                )
                offset += 4 * count
                name = FIELDS.get(key, f"unknown_{key}")
                if name in values:
                    raise CaptureError("duplicate field")
                if scalar == 1 and any(not math.isfinite(x) for x in field):
                    raise CaptureError("nonfinite observation")
                values[name] = field[0] if count == 1 else list(field)
            if any(FIELDS[key] in values for key in JUMP_CONFIGURATION_LAYOUTS):
                required = [FIELDS[key] for key in range(260, 266)] + [
                    "callback_sequence"
                ]
                if any(name not in values for name in required):
                    raise CaptureError("Incomplete jump configuration provenance")
                if (
                    kind != 3
                    or phase != 2
                    or not actor
                    or values["jump_configuration_actor"] != actor
                ):
                    raise CaptureError(
                        "Jump configuration requires car BODY callback identity"
                    )
                present = values["jump_configuration_component_present"]
                complete = values["jump_configuration_complete_present"]
                mask = values["jump_configuration_valid_mask"]
                attempted = values["jump_configuration_attempted_mask"]
                if (
                    present not in (0, 1)
                    or attempted != (511 if present else 0)
                    or mask not in range(512)
                    or mask & ~attempted
                    or complete != int(mask == 511)
                ):
                    raise CaptureError(
                        "Invalid jump configuration component/attempt/valid presence"
                    )
                if (
                    not values["jump_configuration_registry_generation"]
                    or type(values["callback_sequence"]) is not int
                    or values["callback_sequence"] < 0
                ):
                    raise CaptureError("Invalid jump configuration generation/callback")
                for i in range(9):
                    if (FIELDS[270 + i] in values) != bool(mask & (1 << i)):
                        raise CaptureError(
                            "Jump configuration value/source mask disagrees"
                        )
                if "body_configuration_actor" in values and (
                    values["jump_configuration_actor"]
                    != values["body_configuration_actor"]
                    or values["jump_configuration_registry_generation"]
                    != values["body_configuration_registry_generation"]
                ):
                    raise CaptureError("Body/jump configuration identities disagree")
            if any(FIELDS[key] in values for key in BODY_CONFIGURATION_LAYOUTS):
                required = [FIELDS[key] for key in (250, 251, 255, 256, 257)] + [
                    "callback_sequence"
                ]
                if any(name not in values for name in required):
                    raise CaptureError("Incomplete body configuration provenance")
                if (
                    kind != 3
                    or phase != 2
                    or not actor
                    or values["body_configuration_actor"] != actor
                ):
                    raise CaptureError(
                        "Body configuration requires car BODY callback identity"
                    )
                mask = values["body_configuration_source_mask"]
                attempted = values["body_configuration_attempted_mask"]
                if (
                    mask not in range(8)
                    or attempted != 7
                    or values["body_configuration_present"] != int(mask == 7)
                ):
                    raise CaptureError("Invalid body configuration source presence")
                if (
                    not values["body_configuration_registry_generation"]
                    or type(values["callback_sequence"]) is not int
                    or values["callback_sequence"] < 0
                ):
                    raise CaptureError("Invalid body configuration generation/callback")
                for key, bit in ((252, 1), (253, 2), (254, 4)):
                    if (FIELDS[key] in values) != bool(mask & bit):
                        raise CaptureError(
                            "Body configuration value/source mask disagrees"
                        )
                if mask & 1 and values["loadout_body_native_id"] <= 0:
                    raise CaptureError("Invalid native body ID")
                if mask & 2 and any(
                    x <= 0 for x in values["local_collision_extent_uu"]
                ):
                    raise CaptureError("Invalid collision extents")
            if any(FIELDS[key] in values for key in CAMERA_TARGET_LAYOUTS):
                if kind != 6 or phase != 3 or actor != 0:
                    raise CaptureError(
                        "Camera target observations require camera callback header"
                    )
                if (
                    "camera_target_present" not in values
                    or "camera_target_engine_present" not in values
                ):
                    raise CaptureError(
                        "Camera target source presence flags are required"
                    )
            if kind == 6 and "camera_target_present" in values:
                if phase != 3 or actor != 0:
                    raise CaptureError(
                        "Camera target observations require camera callback header"
                    )
                present = values["camera_target_present"]
                target_actor = values.get("camera_target_actor")
                if (
                    present not in (0, 1)
                    or target_actor is None
                    or bool(target_actor) != bool(present)
                ):
                    raise CaptureError("Invalid camera target presence/identity")
                target_fields = [
                    FIELDS[key]
                    for key in CAMERA_TARGET_LAYOUTS
                    if key not in (200, 201, 206, 240)
                ]
                if present and any(name not in values for name in target_fields):
                    raise CaptureError("Incomplete camera target tuple")
                if not present and any(name in values for name in target_fields):
                    raise CaptureError("Absent camera target has synthetic state")
                if values.get("camera_owner") != target_actor:
                    raise CaptureError("Camera target local identity disagrees")
            if kind == 6 and "camera_target_engine_present" in values:
                present = values["camera_target_engine_present"]
                alpha = "camera_target_engine_render_alpha" in values
                if present not in (0, 1) or alpha != bool(present):
                    raise CaptureError("Invalid camera target engine presence")
            yield {
                "kind": KINDS[kind],
                "phase": phase,
                "sequence": sequence,
                "host_monotonic_ns": host_ns,
                "engine_frame": engine_frame,
                "actor": actor,
                "fields": values,
            }


def inspect(path):
    counts = {}
    first = None
    last = None
    quality = None
    for record in records(path):
        counts[record["kind"]] = counts.get(record["kind"], 0) + 1
        first = first or record
        last = record
        if record["kind"] == "quality":
            quality = record["fields"]
    # A well-formed data file is not automatically a completed/qualified experiment.
    return {
        "counts": counts,
        "first_sequence": first["sequence"] if first else None,
        "last_sequence": last["sequence"] if last else None,
        "terminal_quality": quality,
        "exact_acceptance_eligible": bool(
            last
            and last["kind"] == "quality"
            and quality.get("writer_valid") == 1
            and quality.get("drop_count") == 0
            and quality.get("timing_qualified") == 1
            and quality.get("experiment_failed", 0) == 0
        ),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("capture", type=Path)
    p.add_argument("--export", type=Path)
    p.add_argument("--compress", type=Path)
    a = p.parse_args()
    result = inspect(a.capture)
    if a.export:
        with a.export.open("x") as out:
            for record in records(a.capture):
                out.write(
                    json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n"
                )
    if a.compress:
        if a.compress.exists():
            p.error("compression target already exists")
        with (
            a.capture.open("rb") as inp,
            gzip.open(a.compress, "wb", compresslevel=6) as out,
        ):
            while chunk := inp.read(1048576):
                out.write(chunk)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
