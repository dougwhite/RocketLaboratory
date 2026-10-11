"""Pinned external sources, verified downloads and bounded safe extraction."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import urllib.request
import zipfile


def lock():
    return json.loads(Path(__file__).with_name('dependency-lock.json').read_text())['dependencies']

def fetch(name, cache):
    items=lock()
    if name not in items:raise ValueError('Unknown dependency')
    item=items[name];cache=Path(cache).expanduser().resolve();cache.mkdir(parents=True,exist_ok=True)
    target=cache/(name+'-'+item['revision'])
    # Cache stamp verifies the installed tree on reuse, rather than trusting a directory name.
    if target.exists():
        marker=json.loads((target/'.rocketlab-dependency.json').read_text())
        if marker['archive_sha256']!=item['sha256']:raise ValueError('Dependency cache mismatch')
        for relative,digest in marker['files'].items():
            path=target/relative
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Dependency cache modified; remove/re-fetch explicitly')
        return str(target)
    with urllib.request.urlopen(item['url'],timeout=30) as response:
        raw=response.read(item['max_bytes']+1)
    if len(raw)>item['max_bytes'] or hashlib.sha256(raw).hexdigest()!=item['sha256']:
        raise ValueError('Dependency download size/checksum mismatch')
    return extract(raw,target,item)

def extract(raw,target,item):
    target=Path(target)
    with tempfile.TemporaryDirectory(prefix='rocketlab-dep-',dir=target.parent) as temp:
        staging=Path(temp);files={};total=0;root=None
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members=archive.infolist()
            if len(members)>20000:raise ValueError('Archive member budget exceeded')
            for member in members:
                parts=PurePosixPath(member.filename).parts
                if not parts or PurePosixPath(member.filename).is_absolute() or any(p in ('..','.') for p in parts) or '\\' in member.filename or ':' in member.filename:
                    raise ValueError('Unsafe archive path')
                if root is None:root=parts[0]
                if parts[0]!=root:raise ValueError('Archive requires one top-level directory')
                if (member.external_attr>>16)&0o170000==0o120000:raise ValueError('Archive symlink rejected')
                total+=member.file_size
                if total>128*1024*1024:raise ValueError('Extracted archive budget exceeded')
                if len(parts)==1 or member.is_dir():continue
                relative=str(PurePosixPath(*parts[1:]));path=staging/relative
                if relative in files:raise ValueError('Duplicate archive file')
                path.parent.mkdir(parents=True,exist_ok=True);data=archive.read(member);path.write_bytes(data)
                files[relative]=hashlib.sha256(data).hexdigest()
        (staging/'.rocketlab-dependency.json').write_text(json.dumps({'archive_sha256':item['sha256'],'files':files},indent=2))
        if target.exists():raise ValueError('Cache destination already exists')
        shutil.move(staging,target)
    return str(target)
