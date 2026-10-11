"""Synthetic I/O fixture, deliberately NOT a Rocket League physics model."""

import struct
import time
import zlib
from .instrument import stream, tape


def run(spec, path, cancelled):
    seq = 0
    with path.open("xb") as output:
        output.write(stream.MAGIC)

        def write(kind, frame, actor, fields):
            nonlocal seq
            raw = stream.HEADER.pack(
                kind, 2, seq, 1000000000 + frame * 8333333, frame, actor
            )
            for key, scalar, values in fields:
                raw += struct.pack("<HBB", key, scalar, len(values)) + struct.pack(
                    "<" + {1: "f", 2: "I", 3: "i"}[scalar] * len(values), *values
                )
            output.write(stream.ENVELOPE.pack(len(raw), zlib.crc32(raw)) + raw)
            seq += 1

        from .experiment import to_tape

        specwire = to_tape(
            spec, {a["car_index"]: a["car_index"] + 1 for a in spec["actors"]}
        )
        lines = tape.compile_tape(specwire).splitlines()[1:]
        duration = spec["duration_steps"]
        rows = {}
        positions = {}
        velocities = {}
        for i in range(len(spec["actors"])):
            offset = i * (duration + 1)
            actor = int(lines[offset])
            rows[actor] = lines[offset + 1 : offset + 1 + duration]
            positions[actor] = [i * 300.0, 0.0, 50.0]
            velocities[actor] = [0.0, 0.0, 0.0]
        for frame in range(duration):
            if cancelled():
                raise InterruptedError("Cancelled synthetic run")
            for actor, controls in rows.items():
                values = list(map(float, controls[frame].split()))
                bits = sum(int(values[7 + i]) << i for i in range(5))
                write(
                    1,
                    frame,
                    actor,
                    [(100 + i, 1, [v]) for i, v in enumerate(values[:7])]
                    + [(107, 2, [bits])],
                )
                # An arbitrary toy signal for verifying chart and transport behavior only.
                v = velocities[actor]
                p = positions[actor]
                v[0] = (v[0] + values[0] * 4 + values[9] * 2) * 0.99
                v[1] = values[1] * 70
                p[0] += v[0] / 120
                p[1] += v[1] / 120
                p[2] = 50 + values[8] * 30
                write(3, frame, actor, [(1, 1, p), (3, 1, v), (5, 1, [frame / 120])])
                write(
                    6,
                    frame,
                    actor,
                    [(1, 1, [p[0] - 250, p[1], p[2] + 100]), (50, 1, [100])],
                )
            if frame % 60 == 0:
                time.sleep(0.002)
        write(
            8,
            duration,
            0,
            [(80, 2, [0]), (81, 2, [1]), (82, 2, [0]), (83, 2, [3]), (86, 2, [0])],
        )
