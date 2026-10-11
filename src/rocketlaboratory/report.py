"""Bounded capture views; preserve original tuples and observation clock."""

import math
from .instrument import stream

MAX_DECODED = 128 * 1024 * 1024
MAX_RECORDS = 1000000
PREVIEW_FIELDS = frozenset(
    (
        "location_uu",
        "velocity_uu_s",
        "rotation_unreal_integer",
        "physics_time_s",
        "fov_deg",
        "throttle",
        "steer_input",
        "pitch",
        "yaw",
        "roll",
        "input_button_bits",
    )
)


def inspect(path, *, source="external", preview_limit=1200):
    if (
        isinstance(preview_limit, bool)
        or not isinstance(preview_limit, int)
        or not 1 <= preview_limit <= 1200
    ):
        raise ValueError("Preview record limit must be 1..1200")
    counts = {}
    first = last = quality = None
    selected = []
    count = 0
    for row in stream.records(path, max_payload_bytes=MAX_DECODED):
        count += 1
        if count > MAX_RECORDS:
            raise ValueError("Record budget exceeded")
        kind = row["kind"]
        counts[kind] = counts.get(kind, 0) + 1
        first = first or row
        last = row
        if kind == "quality":
            quality = row["fields"]
        if kind in ("body", "camera", "input_requested", "input_consumed"):
            # Reservoir-like deterministic thinning, never used for scientific comparison.
            selected.append(
                {
                    **row,
                    "fields": {
                        key: value
                        for key, value in row["fields"].items()
                        if key in PREVIEW_FIELDS
                    },
                }
            )
            if len(selected) > preview_limit * 2:
                selected = selected[::2]
    if len(selected) > preview_limit:
        step = math.ceil(len(selected) / preview_limit)
        selected = selected[::step]
    complete = bool(
        last
        and last["kind"] == "quality"
        and quality
        and quality.get("writer_valid") == 1
        and quality.get("drop_count") == 0
    )
    return {
        "schema": "rocketlaboratory.report.v1",
        "source": source,
        "record_count": count,
        "counts": counts,
        "terminal_quality": quality,
        "integrity_complete": complete,
        "timing_qualified": False,
        "exact_acceptance_eligible": False,
        "clock": "candidate engine frame + hook phase + monotonic timestamp; no solver alignment assumed",
        "preview_decimated": True,
        "preview_fields_projected": True,
        "preview": selected,
        "limitations": [
            "Preview is for visualization only; raw NNI2 tuples retain original float32 precision.",
            "Native hook ordering, camera/body synchronization and hidden-state restore remain unqualified.",
        ],
    }


def compare(left, right):
    """Diagnostic common-frame RMS; no interpolation, acceptance or inferred synchronization."""

    def bodies(path):
        result = {}
        for row in stream.records(path, max_payload_bytes=MAX_DECODED):
            if row["kind"] == "body" and "location_uu" in row["fields"]:
                key = (row["actor"], row["engine_frame"], row["phase"])
                if key in result:
                    raise ValueError(
                        "Ambiguous body pairing; duplicate actor/frame/phase"
                    )
                result[key] = row["fields"]["location_uu"]
                if len(result) > 100000:
                    raise ValueError("Comparison budget exceeded")
        return result

    a, b = bodies(left), bodies(right)
    common = a.keys() & b.keys()
    errors = [sum((x - y) ** 2 for x, y in zip(a[k], b[k])) for k in common]
    return {
        "pairing": "identical raw actor/frame/phase; user must establish matching initialization",
        "paired_bodies": len(common),
        "unpaired_left": len(a) - len(common),
        "unpaired_right": len(b) - len(common),
        "position_rms_uu": math.sqrt(sum(errors) / len(errors)) if errors else None,
        "timing_qualified": False,
        "exact_acceptance_eligible": False,
    }
