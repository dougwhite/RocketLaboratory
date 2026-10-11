"""Public experiment contract. Scheduling steps are candidate engine frames, not solver ticks."""

import copy
import json
from pathlib import Path
from .instrument.tape import compile_tape, integer

SCHEMA = "rocketlaboratory.experiment.v1"
MAX_JSON = 1024 * 1024
MAX_STEPS = 7200
MAX_ACTOR_STEPS = 115200


def load(path):
    with Path(path).open("rb") as source:
        raw = source.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("Experiment exceeds 1 MiB")
    return validate(json.loads(raw))


def validate(spec):
    if not isinstance(spec, dict) or set(spec) - {
        "schema",
        "name",
        "description",
        "duration_steps",
        "preparation",
        "actors",
        "motion_policy",
    }:
        raise ValueError("Unknown experiment fields")
    if spec.get("schema") != SCHEMA:
        raise ValueError("Unsupported experiment schema")
    name = spec.get("name")
    if not isinstance(name, str) or not 1 <= len(name) <= 120:
        raise ValueError("Name must contain 1..120 characters")
    if (
        not isinstance(spec.get("description", ""), str)
        or len(spec.get("description", "")) > 4000
    ):
        raise ValueError("Description exceeds 4000 characters")
    duration = integer(spec.get("duration_steps"), 1, MAX_STEPS)
    if spec.get("preparation") not in ("settled_floor", "fixed_pose_then_settle"):
        raise ValueError(
            "Supported preparation: settled_floor or fixed_pose_then_settle; no hidden-state restore"
        )
    if spec.get("motion_policy", "planar") not in (
        "planar",
        "vertical",
        "neutral_baseline",
    ):
        raise ValueError("Unknown observable motion policy")
    actors = spec.get("actors")
    if (
        not isinstance(actors, list)
        or not 1 <= len(actors) <= 16
        or duration * len(actors) > MAX_ACTOR_STEPS
    ):
        raise ValueError("Actor or dense tape budget exceeded")
    indices = set()
    for actor in actors:
        if not isinstance(actor, dict) or set(actor) != {"car_index", "segments"}:
            raise ValueError("Actor requires car_index and segments")
        index = integer(actor["car_index"], 0, 63)
        if index in indices:
            raise ValueError("Duplicate car index")
        indices.add(index)
    if spec["preparation"] == "fixed_pose_then_settle" and indices != {0}:
        raise ValueError(
            "Fixed pose preparation supports only one local car at index 0"
        )
    result = copy.deepcopy(spec)
    # Run the strict wire compiler as a second check, with placeholder capture-local IDs.
    compile_tape(to_tape(result, {i: i + 1 for i in indices}, checked=True))
    return result


def to_tape(spec, bindings, *, checked=False):
    if not checked:
        spec = validate(spec)
    rows = {}
    for actor in spec["actors"]:
        index = actor["car_index"]
        if index not in bindings:
            raise ValueError(f"Car index {index} absent from native registry")
        ident = integer(bindings[index], 1, 2**32 - 1)
        if str(ident) in rows:
            raise ValueError("Duplicate native actor binding")
        rows[str(ident)] = actor["segments"]
    return {
        "schema": "noodle.instrument.tape.v1",
        "duration_steps": spec["duration_steps"],
        "actors": rows,
    }


def presets():
    return json.loads(Path(__file__).with_name("presets.json").read_text())
