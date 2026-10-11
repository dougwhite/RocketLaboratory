"""Release allowlist audit. Run before committing and packaging."""
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
allowed_roots={'src','tests','native','docs','examples','scripts','.github'}
allowed_files={'README.md','AGENTS.md','LICENSE','THIRD_PARTY.md','SECURITY.md','CONTRIBUTING.md','CHANGELOG.md','pyproject.toml','.gitignore','MANIFEST.in'}
allowed_suffixes={'.py','.hpp','.cpp','.txt','.md','.json','.toml','.html','.css','.js','.ps1','.yml','.yaml','.in','.png'}
forbidden=[b'/home/' + b'doug/',b'windows11' + b'-dev',b'paul' + b'@',b'gh' + b'p_',b'github' + b'_pat_',b'BEGIN OPENSSH' + b' PRIVATE KEY',b'BEGIN RSA' + b' PRIVATE KEY']
try:
    names=subprocess.check_output(['git','ls-files','-z'],cwd=root,stderr=subprocess.DEVNULL).decode().split('\0')
except (subprocess.CalledProcessError,FileNotFoundError):
    names=[str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and not any(part in {'.git','__pycache__','build','dist','.venv'} or part.endswith('.egg-info') for part in p.parts)]
errors=[]
for name in filter(None,names):
    p=Path(name);path=root/p
    if p.parts[0] not in allowed_roots and name not in allowed_files:errors.append(f'Unexpected public path: {name}')
    if path.is_symlink() or p.suffix not in allowed_suffixes and name not in {'.gitignore','LICENSE','MANIFEST.in'}:errors.append(f'Unexpected file type: {name}')
    if path.stat().st_size>2*1024*1024:errors.append(f'Oversized public file: {name}')
    raw=path.read_bytes()
    if any(value in raw for value in forbidden):errors.append(f'Possible private identifier/secret: {name}')
if errors:print('\n'.join(errors),file=sys.stderr);raise SystemExit(1)
print(f'Public allowlist audit passed: {len(list(filter(None,names)))} source/documentation files; no private capture or dependency binaries.')
