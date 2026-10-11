"""One command surface over the shared experiment service."""
import argparse
import os
import json
from pathlib import Path
import sys
from . import dependencies,experiment,mcp,report
from .bridge import atomic
from .instrument import stream,tape
from .service import Service


def output(value):print(json.dumps(value,indent=2,allow_nan=False))

def main(argv=None):
    if os.name != 'nt': os.umask(0o077)
    parser=argparse.ArgumentParser(prog='rocketlab',description='Offline Rocket League investigation toolkit; demo output is synthetic.')
    parser.add_argument('--workspace',type=Path,help='Private result directory outside the source checkout')
    parser.add_argument('--bridge-dir',type=Path,help='Existing BakkesMod data/noodle-instrument directory; opt in to native attachment')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('doctor');commands.add_parser('presets');commands.add_parser('jobs');commands.add_parser('mcp')
    p=commands.add_parser('example');p.add_argument('name');p.add_argument('output',type=Path)
    for name in ('validate','run'):
        p=commands.add_parser(name);p.add_argument('experiment',type=Path)
        if name=='run':p.add_argument('--backend',choices=('demo','native'),default='demo')
    p=commands.add_parser('compile');p.add_argument('experiment',type=Path);p.add_argument('output',type=Path);p.add_argument('--bindings',required=True,help='Explicit car-index to native actor-ID JSON object')
    p=commands.add_parser('campaign');p.add_argument('campaign',type=Path);p.add_argument('--backend',choices=('demo','native'),default='demo')
    for name in ('status','result'):
        p=commands.add_parser(name);p.add_argument('job_id')
    p=commands.add_parser('inspect');p.add_argument('capture',type=Path)
    p=commands.add_parser('export');p.add_argument('capture',type=Path);p.add_argument('output',type=Path)
    p=commands.add_parser('compare');p.add_argument('left',type=Path);p.add_argument('right',type=Path)
    p=commands.add_parser('serve');p.add_argument('--port',type=int,default=8890)
    p=commands.add_parser('arm');p.add_argument('--seconds',type=int,default=300)
    p=commands.add_parser('native');p.add_argument('operation',choices=('actors','stop'))
    p=commands.add_parser('deps');p.add_argument('operation',choices=('list','fetch'));p.add_argument('name',nargs='?');p.add_argument('--cache',type=Path,default=Path.home()/'.cache/RocketLaboratory/dependencies')
    args=parser.parse_args(argv);service=None
    try:
        if args.command=='deps':
            output(dependencies.lock() if args.operation=='list' else {'path':dependencies.fetch(args.name,args.cache)});return 0
        if args.command=='example':
            examples=experiment.presets()
            if args.name not in examples:raise ValueError('Unknown preset')
            with args.output.open('x') as file:file.write(json.dumps(examples[args.name],indent=2)+'\n')
            output({'written':str(args.output)});return 0
        if args.command=='presets':output(experiment.presets());return 0
        if args.command=='validate':output({'valid':True,'experiment':experiment.load(args.experiment)});return 0
        if args.command=='compile':
            spec=experiment.load(args.experiment);bindings=json.loads(args.bindings)
            if not isinstance(bindings,dict):raise ValueError('Bindings object required')
            data=tape.compile_tape(experiment.to_tape(spec,{int(k):v for k,v in bindings.items()}))
            with args.output.open('x') as file:file.write(data)
            output({'written':str(args.output),'timing_qualified':False});return 0
        if args.command=='inspect':output(report.inspect(args.capture));return 0
        if args.command=='compare':output(report.compare(args.left,args.right));return 0
        if args.command=='export':
            with args.output.open('x') as out:
                for row in stream.records(args.capture,max_payload_bytes=report.MAX_DECODED):out.write(json.dumps(row,allow_nan=False,separators=(',',':'))+'\n')
            output({'written':str(args.output),'interpolated':False});return 0
        service=Service(args.workspace,args.bridge_dir)
        if args.command=='doctor':output(service.doctor())
        elif args.command=='jobs':output(service.list())
        elif args.command=='status':output(service.status(args.job_id))
        elif args.command=='result':output(service.result(args.job_id))
        elif args.command=='run':
            job=service.submit(experiment.load(args.experiment),args.backend);state=service.wait(job['id']);output(state)
            return 0 if state['state']=='complete' else 1
        elif args.command=='campaign':
            from .bridge import read_json
            campaign=read_json(args.campaign)
            if set(campaign)!={'schema','cases'} or campaign['schema']!='rocketlaboratory.campaign.v1' or not isinstance(campaign['cases'],list) or not 1<=len(campaign['cases'])<=100:raise ValueError('Invalid campaign')
            total=0;cases=[]
            for case in campaign['cases']:
                if not isinstance(case,dict) or set(case)!={'experiment','repeat'}:raise ValueError('Invalid campaign case')
                spec=experiment.validate(case['experiment']);repeat=tape.integer(case['repeat'],1,20);total+=repeat
                if total>100:raise ValueError('Campaign exceeds 100 runs')
                cases.extend([spec]*repeat)
            results=[]
            for spec in cases:
                state=service.wait(service.submit(spec,args.backend)['id']);results.append(state)
                if state['state']!='complete':break
            output({'runs':results,'planned':len(cases),'completed':sum(s['state']=='complete' for s in results),'exact_acceptance_eligible':False})
            return 0 if len(results)==len(cases) and all(s['state']=='complete' for s in results) else 1
        elif args.command=='arm':
            if not service.bridge:raise ValueError('--bridge-dir required')
            output(service.bridge.arm(args.seconds))
        elif args.command=='native':
            if not service.bridge:raise ValueError('--bridge-dir required')
            from .bridge import lease
            with lease(service.bridge.folder):output(service.bridge.command(args.operation))
        elif args.command=='mcp':mcp.serve(service)
        elif args.command=='serve':
            from .server import WebServer
            with WebServer(('127.0.0.1',args.port),service) as server:
                print(server.origin,flush=True);server.serve_forever()
        return 0
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as error:
        print(json.dumps({'error':str(error)}),file=sys.stderr);return 1
    except KeyboardInterrupt:
        print('Stopping; cancelling owned run.',file=sys.stderr);return 130
    finally:
        if service:service.close()

if __name__=='__main__':raise SystemExit(main())
