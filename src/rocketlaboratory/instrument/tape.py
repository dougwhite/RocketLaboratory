#!/usr/bin/env python3
"""Compile staged, dense per-actor numeric inputs for the SDK qualification adapter."""
import argparse
import json
import math
from pathlib import Path
AXES = ('throttle','steer','pitch','yaw','roll','dodge_forward','dodge_strafe')
BUTTONS = ('handbrake','jump','activate_boost','holding_boost','jumped')

def integer(value, low, high):
    if isinstance(value,bool) or not isinstance(value,int) or not low <= value <= high: raise ValueError('integer out of bounds')
    return value

def compile_tape(spec):
    if set(spec) != {'schema','duration_steps','actors'} or spec['schema'] != 'noodle.instrument.tape.v1': raise ValueError('unknown tape schema/fields')
    duration=integer(spec['duration_steps'],1,72000);actors=spec['actors']
    if not isinstance(actors,dict) or not 1 <= len(actors) <= 64:raise ValueError('actor count out of bounds')
    output=[f'NNIT1 {duration} {len(actors)}']
    for name,segments in actors.items():
        if not isinstance(name,str) or not name.isdecimal() or str(int(name))!=name: raise ValueError('canonical numeric actor ids required')
        actor=integer(int(name),1,2**32-1)
        if not isinstance(segments,list) or len(segments)>duration:raise ValueError('bad segment list')
        rows=[[0]*12 for _ in range(duration)];occupied=set()
        for segment in segments:
            if set(segment) != {'start_step','duration_steps','controls'}:raise ValueError('unknown segment fields')
            start=integer(segment['start_step'],0,duration-1);count=integer(segment['duration_steps'],1,duration-start)
            controls=segment['controls']
            if not isinstance(controls,dict) or set(controls)-set(AXES+BUTTONS):raise ValueError('unknown control')
            row=[]
            for axis in AXES:
                value=controls.get(axis,0)
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not -1 <= value <= 1: raise ValueError('invalid axis')
                row.append(value)
            for button in BUTTONS:
                value=controls.get(button,False)
                if not isinstance(value,bool):raise ValueError('boolean button required')
                if button=='jumped' and value:raise ValueError('jumped is engine-derived; not an authored history reset')
                row.append(int(value))
            for tick in range(start,start+count):
                if tick in occupied:raise ValueError('overlapping actor segments')
                occupied.add(tick);rows[tick]=row.copy()
        output.append(str(actor));output.extend(' '.join(format(value,'.9g') for value in row) for row in rows)
    return '\n'.join(output)+'\n'

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('spec',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    result=compile_tape(json.loads(a.spec.read_text()));
    with a.output.open('x') as f:f.write(result)
    print(json.dumps({'output':str(a.output),'instrument_qualified':False,'preparation':'120 consecutive candidate frames: four world wheel contacts, low speed/spin, jump unused'}))
if __name__=='__main__':main()
