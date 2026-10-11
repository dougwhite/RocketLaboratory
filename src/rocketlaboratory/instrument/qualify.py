"""Bounded native capture diagnostics; scheduler Done never implies input delivery.

This checks observable plumbing, not Rocket League parity or atomic solver phase.
Raw captures/settings are private. The default budget is 50,000 decoded records.
Policies ``planar`` (default), ``vertical`` and ``neutral_baseline`` describe
observable plumbing checks only. They are not physics acceptance criteria.
"""
import argparse
import collections
import hashlib
import json
import math
import struct
import tomllib
from pathlib import Path

try:
    from .stream import CaptureError, records
    from .tape import compile_tape
except ImportError:
    from stream import CaptureError, records
    from tape import compile_tape

AXES = ('throttle', 'steer_input', 'pitch', 'yaw', 'roll', 'dodge_forward', 'dodge_strafe')
BUTTONS = ('handbrake', 'jump', 'activate_boost', 'holding_boost', 'jumped')
MOTION_POLICIES = ('planar', 'vertical', 'neutral_baseline')


def controls(record, prefix=''):
    fields = record['fields']
    return tuple(fields[prefix+k] for k in AXES) + tuple(bool(fields[prefix+'input_button_bits'] & (1 << i)) for i in range(5))


def body_source_summary(rows):
    """Report getter differences without manufacturing a single aligned state."""
    body = [row for row in rows if row['kind'] == 'body']
    output = {}
    steps = {row['fields'].get('configured_fixed_physics_dt_s') for row in rows if row['kind'] == 'world'} - {None}
    fixed_dt = next(iter(steps)) if len(steps) == 1 else None
    pairs = [('rb_state_quaternion_wxyz', 'current_rb_state_quaternion_wxyz'),
             ('rb_state_location_uu', 'current_rb_state_location_uu'),
             ('rb_state_velocity_uu_s', 'current_rb_state_velocity_uu_s'),
             ('rb_state_angular_velocity_rad_s', 'current_rb_state_angular_velocity_rad_s'),
             ('rb_state_time_s', 'current_rb_state_time_s'),
             ('location_uu', 'rb_state_location_uu'),
             ('velocity_uu_s', 'rb_state_velocity_uu_s'),
             ('angular_velocity_rad_s', 'rb_state_angular_velocity_rad_s'),
             ('old_rb_state_quaternion_wxyz', 'rb_state_quaternion_wxyz'),
             ('old_rb_state_location_uu', 'rb_state_location_uu')]
    for actor in sorted({row['actor'] for row in body}):
        actor_rows = [row for row in body if row['actor'] == actor]
        data = [row['fields'] for row in actor_rows]
        result = dict(samples=len(data), source_pairs={}, quaternions={}, times={}, integration={})
        for left, right in pairs:
            differences = []
            for fields in data:
                if left not in fields or right not in fields:
                    continue
                a, b = fields[left], fields[right]
                differences.append(max(abs(x-y) for x, y in zip(a, b)) if isinstance(a, list) else abs(a-b))
            result['source_pairs'][left+' vs '+right] = dict(samples=len(differences),
                different=sum(value != 0 for value in differences), max_abs_component=max(differences, default=None))
        for field in ('rb_state_quaternion_wxyz', 'current_rb_state_quaternion_wxyz', 'old_rb_state_quaternion_wxyz'):
            values = [fields[field] for fields in data if field in fields]
            if any(not isinstance(q, list) or len(q) != 4 for q in values):
                raise CaptureError('Invalid quaternion representation')
            norms = [math.sqrt(sum(value*value for value in q)) for q in values]
            result['quaternions'][field] = dict(samples=len(norms), norm_min=min(norms, default=None), norm_max=max(norms, default=None))
        for field in ('physics_time_s', 'rb_state_time_s', 'current_rb_state_time_s', 'old_rb_state_time_s'):
            values = [fields[field] for fields in data if field in fields]
            result['times'][field] = dict(samples=len(values), minimum=min(values, default=None), maximum=max(values, default=None),
                constant=bool(values and min(values) == max(values)))
        if fixed_dt is not None and fixed_dt > 0:
            for prefix in ('', 'rb_state_', 'current_rb_state_', 'old_rb_state_'):
                position = prefix+'location_uu'; velocity = prefix+'velocity_uu_s'
                consecutive = [(a['fields'], b['fields']) for a, b in zip(actor_rows, actor_rows[1:])
                               if b['engine_frame']-a['engine_frame'] == 1 and all(key in row['fields'] for row in (a, b) for key in (position, velocity))]
                residuals = {}
                for mode in ('previous_velocity', 'next_velocity', 'trapezoid_velocity'):
                    errors = []
                    for before, after in consecutive:
                        values = before[velocity] if mode == 'previous_velocity' else after[velocity] if mode == 'next_velocity' else [(a+b)/2 for a, b in zip(before[velocity], after[velocity])]
                        errors.append(sum((b-a-v*fixed_dt)**2 for a, b, v in zip(before[position], after[position], values)))
                    residuals[mode] = dict(samples=len(errors), rms_vector_uu=math.sqrt(sum(errors)/len(errors)) if errors else None,
                                           max_vector_uu=math.sqrt(max(errors)) if errors else None)
                result['integration'][prefix or 'actor_getters'] = dict(configured_fixed_dt_s=fixed_dt, residuals=residuals)
        alpha = []; interpolation_errors = []
        for fields in data:
            if all(key in fields for key in ('engine_render_alpha', 'old_rb_state_location_uu', 'rb_state_location_uu', 'location_uu')):
                value = fields['engine_render_alpha']; alpha.append(value)
                interpolation_errors.append(sum((observed-((1-value)*old+value*current))**2 for old, current, observed in
                                               zip(fields['old_rb_state_location_uu'], fields['rb_state_location_uu'], fields['location_uu'])))
        result['render_interpolation_hypothesis'] = dict(formula='old*(1-alpha)+stored*alpha', samples=len(alpha),
            alpha_min=min(alpha, default=None), alpha_max=max(alpha, default=None),
            rms_vector_uu=math.sqrt(sum(interpolation_errors)/len(interpolation_errors)) if interpolation_errors else None,
            max_vector_uu=math.sqrt(max(interpolation_errors)) if interpolation_errors else None,
            qualified=False)
        output[str(actor)] = result
    return output


def experiment_motion(rows, requested, dense, starts, duration, policy):
    """Keep historical planar criteria; opt-in policies require scheduled bodies.

    Vertical displacement is an observation, not proof of a jump law or causal
    input delivery. A neutral baseline still requires state samples, despite
    having no required displacement.
    """
    movement = {}
    active_actors = {r['actor'] for r in requested if any(controls(r)[:11])}
    actors = active_actors if policy == 'planar' else set(dense)
    for actor in sorted(actors):
        if policy == 'planar':
            frames = [r['engine_frame'] for r in requested if r['actor'] == actor and any(controls(r)[:11])]
            first, last = min(frames), max(frames)+2
        elif len(starts) == 1 and duration:
            first, last = starts[0], starts[0]+duration-1
        else:
            first, last = 0, -1
        body = [r for r in rows if r['kind'] == 'body' and r['actor'] == actor and first <= r['engine_frame'] <= last]
        displacement = speed = vertical_displacement = vertical_speed = 0.0
        valid_vectors = all(all(isinstance(r['fields'].get(name), list) and len(r['fields'][name]) == 3
                                and all(isinstance(v, (int, float)) and math.isfinite(v) for v in r['fields'][name])
                                for name in ('location_uu', 'velocity_uu_s')) for r in body)
        if body and valid_vectors:
            origin = body[0]['fields']['location_uu']
            displacement = max(math.hypot(r['fields']['location_uu'][0]-origin[0], r['fields']['location_uu'][1]-origin[1]) for r in body)
            speed = max(math.hypot(*r['fields']['velocity_uu_s'][:2]) for r in body)
            vertical_displacement = max(abs(r['fields']['location_uu'][2]-origin[2]) for r in body)
            vertical_speed = max(abs(r['fields']['velocity_uu_s'][2]) for r in body)
        coverage = bool(len(body) >= 2 and valid_vectors)
        if policy != 'planar':
            frames = [r['engine_frame'] for r in body]
            coverage = coverage and len(frames) == duration and len(set(frames)) == duration
        observed = coverage and displacement > 1 and speed > 1
        if policy == 'vertical':
            observed = coverage and vertical_displacement > 1 and vertical_speed > 1
        elif policy == 'neutral_baseline':
            observed = coverage
        entry = dict(status='observed' if observed else 'not_observed', body_samples=len(body),
                     max_planar_displacement_uu=displacement, max_planar_speed_uu_s=speed)
        if policy != 'planar':
            entry.update(body_coverage_complete=coverage, max_vertical_displacement_uu=vertical_displacement,
                         max_vertical_speed_uu_s=vertical_speed, motion_required=policy != 'neutral_baseline')
        movement[str(actor)] = entry
    return movement, bool(movement and all(v['status'] == 'observed' for v in movement.values()))


def assess(rows, tape_spec, requested_map, observed_map, motion_policy='planar'):
    """Separate integrity, frame, schedule, echo, motion, map and timing outcomes."""
    if motion_policy not in MOTION_POLICIES:
        raise ValueError('Unknown motion policy')
    counts = collections.Counter(r['kind'] for r in rows)
    world = [r for r in rows if r['kind'] == 'world']
    increments = [b['engine_frame']-a['engine_frame'] for a, b in zip(world, world[1:])]
    continuous = len(world) >= 2 and all(d == 1 for d in increments)
    quality = rows[-1]['fields'] if rows and rows[-1]['kind'] == 'quality' else None
    stages = [r for r in rows if r['kind'] == 'event' and r['fields'].get('event_code') == 10]
    starts = [r['engine_frame']+1 for r in stages if r['fields'].get('controller_stage') == 2]
    schedule_done = bool(quality and quality.get('controller_stage') == 3 and quality.get('experiment_failed') == 0)
    requested = [r for r in rows if r['kind'] == 'input_requested']
    consumed = [r for r in rows if r['kind'] == 'input_consumed']
    key = lambda r: (r['engine_frame'], r['actor'], r['fields']['callback_sequence'])
    consumed_index = {key(r): r for r in consumed}
    duplicate_consumed = len(consumed_index) != len(consumed)
    duplicate_requested = len({key(r) for r in requested}) != len(requested)
    source_echo = {}
    for prefix in ('post_parameter_', 'vehicle_base_'):
        observations = {}; absent = 0
        for row in consumed:
            if all(prefix+name in row['fields'] for name in AXES+('input_button_bits',)):
                observations[key(row)] = row
            else:
                absent += 1
        changed = collections.Counter(); paired = 0
        for row in requested:
            other = observations.get(key(row))
            if other is not None:
                paired += 1
                changed.update(name for name, a, b in zip(AXES+BUTTONS[:4], controls(row)[:11], controls(other, prefix)[:11]) if a != b)
        source_echo[prefix] = dict(consumed_with_source=len(observations), consumed_missing_source=absent,
                                   paired_requests=paired, mismatches_by_authored_control=dict(changed), solver_authority_qualified=False)
    differences = collections.Counter(); missing = 0; bad = 0; nonzero = 0
    engine_jumped_changes = 0; twelve_channel_changes = 0
    nonzero_matched = 0
    for r in requested:
        authored = controls(r); y = consumed_index.get(key(r))
        active = any(authored[:11]); nonzero += active
        if y is None:
            missing += 1
            continue
        observed = controls(y)
        all_changed = [name for name, a, b in zip(AXES+BUTTONS, authored, observed) if a != b]
        # Jumped belongs to engine history, not authored input. Its pre/post
        # change is retained diagnostically but cannot reject a valid jump pulse.
        engine_jumped_changes += authored[11] != observed[11]
        twelve_channel_changes += bool(all_changed)
        changed = [name for name in all_changed if name != 'jumped']
        differences.update(changed); bad += bool(changed)
        nonzero_matched += active and not changed
    authored_echo_ok = bool(requested and not bad and not missing and not duplicate_consumed and not duplicate_requested)
    echo_ok = bool(nonzero and authored_echo_ok)
    # Recompile the original definition, so comparison includes explicit neutral
    # intervals and float32 rounding but never invents an input phase shift.
    dense = {}; duration = 0; schedule_errors = []; missing_scheduled = 0
    if tape_spec and len(starts) == 1:
        if tape_spec['duration_steps']*len(tape_spec['actors']) > 100000:
            raise ValueError('Tape analysis step budget exceeded')
        lines = iter(compile_tape(tape_spec).splitlines()); _, duration, actors = next(lines).split()
        duration = int(duration)
        for _ in range(int(actors)):
            actor = int(next(lines)); dense[actor] = []
            for _ in range(duration):
                values = list(map(float, next(lines).split()))
                dense[actor].append(tuple(struct.unpack('<f', struct.pack('<f', v))[0] for v in values[:7]) + tuple(bool(v) for v in values[7:]))
        actual = {(r['engine_frame'], r['actor']): r for r in requested}
        for actor, wanted in dense.items():
            for tick, expected in enumerate(wanted):
                row = actual.get((starts[0]+tick, actor))
                if row is None:
                    missing_scheduled += 1
                elif controls(row)[:11] != expected[:11]:
                    # Jumped is engine-derived and deliberately not authored.
                    schedule_errors.append(dict(frame=starts[0]+tick, actor=actor))
    schedule_ok = bool(dense and not missing_scheduled and not schedule_errors and schedule_done and not duplicate_requested)
    movement, motion_ok = experiment_motion(rows, requested, dense, starts, duration, motion_policy)
    map_ok = bool(requested_map and observed_map and requested_map == observed_map)
    integrity_ok = bool(quality and quality.get('writer_valid') == 1 and quality.get('drop_count') == 0 and quality.get('experiment_failed') == 0)
    timing_ok = bool(quality and quality.get('timing_qualified') == 1)
    scheduled_world = [r['engine_frame'] for r in world if len(starts) == 1 and starts[0] <= r['engine_frame'] < starts[0]+duration]
    world_coverage = bool(duration and len(scheduled_world) == duration and len(set(scheduled_world)) == duration)
    capture_ok = all((continuous, schedule_ok, map_ok, integrity_ok,
                      world_coverage if motion_policy != 'planar' else True))
    identity_ok = bool(dense and all(r['actor'] in dense for r in requested))
    baseline = motion_policy == 'neutral_baseline'
    policy_echo_ok = authored_echo_ok and not nonzero if baseline else echo_ok
    observations_ok = all((capture_ok, policy_echo_ok, motion_ok, identity_ok if motion_policy != 'planar' else True))
    status_prefix = 'observable_neutral_baseline_checks_' if baseline else 'observable_delivery_checks_'
    return dict(schema='noodle.instrument.assessment.v1', status=status_prefix+('pass' if observations_ok else 'failed'),
                exact_acceptance_eligible=False, # Hidden-state and atomic-scene qualification are outside this tool.
                capture_checks=dict(status='pass' if capture_ok else 'failed'),
                authored_echo=dict(status='exact_matched' if authored_echo_ok else 'mismatched_or_missing',
                                   compared_authored_channels=11, verified_nonzero_delivery=bool(observations_ok and not baseline)),
                motion_policy=dict(name=motion_policy, status='observed' if motion_ok else 'not_observed',
                                   scheduled_actor_identity_matches=identity_ok, physics_acceptance_qualified=False,
                                   scheduled_world_coverage_complete=world_coverage,
                                   solver_authority_qualified=False, neutral_baseline_observed=bool(baseline and observations_ok)),
                counts=dict(counts), phase_counts={kind: dict(collections.Counter(r['phase'] for r in rows if r['kind'] == kind)) for kind in counts}, source_terminal_quality=quality,
                integrity=dict(status='valid_terminal' if integrity_ok else 'invalid_terminal'),
                frames=dict(status='continuous' if continuous else 'missing_or_duplicate', world_increment_histogram=dict(collections.Counter(increments))),
                schedule=dict(status='completed_and_requested_matches' if schedule_ok else 'incomplete_or_mismatched', scheduler_done=schedule_done,
                              start_frames=starts, duration_steps=duration, missing_requested_steps=missing_scheduled, mismatch_count=len(schedule_errors)),
                input_echo=dict(status='nonzero_matched' if echo_ok else 'not_tested' if not nonzero else 'mismatched',
                                paired_requests=len(requested)-missing, missing_consumed=missing, duplicate_consumed_keys=duplicate_consumed,
                                duplicate_requested_keys=duplicate_requested,
                                mismatched_pairs=bad, mismatches_by_control=dict(differences), nonzero_requested=nonzero, nonzero_matched=nonzero_matched,
                                compared_authored_channels=11, twelve_channel_changed_pairs=twelve_channel_changes,
                                engine_jumped_changed_pairs=engine_jumped_changes),
                movement=movement, body_sources=body_source_summary(rows), other_input_readbacks=source_echo,
                map=dict(status='matched' if map_ok else 'mismatched' if requested_map and observed_map else 'unknown', requested=requested_map, observed=observed_map),
                timing=dict(status='source_declares_qualified' if timing_ok else 'unqualified', independently_validated=False),
                limits=['Same frame/actor/callback pairing is a hook diagnostic, not a phase alignment fit.',
                        'Default movement requires planar displacement and speed above 1 uu/uu_s; explicit vertical/neutral policies remain observation diagnostics.',
                        'Input transformations remain explicit mismatches; do not silently reinterpret them as accepted inputs.',
                        'No full hidden-state restore or atomic multi-object scene qualification is established.'])


def diagnose(capture, manifest, tape, match, max_records=50000, motion_policy='planar'):
    capture = Path(capture); source_paths = [capture, Path(manifest), Path(tape), Path(match)]
    report = dict(schema='noodle.instrument.assessment.v1', status='rejected', exact_acceptance_eligible=False)
    try:
        if motion_policy not in MOTION_POLICIES:
            raise ValueError('Unknown motion policy')
        if not 1 <= max_records <= 100000:
            raise ValueError('Record analysis budget must be 1..100000')
        rows = []
        for row in records(capture, max_payload_bytes=16*1048576):
            if len(rows) >= max_records:
                raise ValueError('Decoded record analysis budget exceeded')
            rows.append(row)
        def bounded_text(path):
            with Path(path).open('rb') as source:
                raw = source.read(1048577)
            if len(raw) > 1048576:
                raise ValueError('Metadata analysis budget exceeded')
            return raw.decode('utf-8')
        meta = json.loads(bounded_text(manifest)); spec = json.loads(bounded_text(tape))
        declared = tomllib.loads(bounded_text(match)).get('match', {})
        report = assess(rows, spec, declared.get('game_map_upk', declared.get('map')), meta.get('reference', {}).get('map'), motion_policy)
    except (CaptureError, ValueError, KeyError, TypeError, OSError) as error:
        report['error'] = str(error)
        report['integrity'] = dict(status='rejected')
    report['source_sha256'] = {}
    for path in source_paths:
        if path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as source:
                while chunk := source.read(1048576):
                    digest.update(chunk)
            report['source_sha256'][str(path)] = digest.hexdigest()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('capture', 'manifest', 'tape', 'match', 'output'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--motion-policy', choices=MOTION_POLICIES, default='planar',
                        help='Observable motion requirement; does not qualify physics or timing')
    args = parser.parse_args()
    result = diagnose(args.capture, args.manifest, args.tape, args.match, motion_policy=args.motion_policy)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k: result.get(k) for k in ('status', 'exact_acceptance_eligible', 'input_echo', 'map', 'timing')}, indent=2))
