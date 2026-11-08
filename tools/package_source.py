"""Create/verify a code-only ADGS + locally patched Hermes source distribution."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {'config/AI_config.json', 'config/settings_overrides.json',
           'skills/mijia-home/mijia_config.json', 'skills/mijia-home/mijia_session.json'}
ENV_PARTS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache', '.npm-cache'}
BINARY = {'.pyc', '.pyo', '.exe', '.dll', '.so', '.pyd', '.dylib', '.whl', '.sqlite', '.db', '.safetensors', '.gguf', '.onnx'}


def forbidden(relative, native=False):
    p = PurePosixPath(relative)
    if any(part in ENV_PARTS for part in p.parts):return True
    if p.suffix.lower() in BINARY:return True
    if p.name == '.env' or (p.name.startswith('.env.') and not p.name.endswith('.example')):return True
    if p.name.endswith(('.pem', '.pfx', '.key', '-wal', '-shm', '.log')):return True
    if any(part in {'dist', 'build', '.adgs_runtime', '.hermes'} for part in p.parts):return True
    if not native and (relative in PRIVATE or p.parts[0] in {'releases','data','runtime','models','workspace','agent_gene_db'}):return True
    return False


def tracked(root):
    raw = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--stage', '-z'])
    for record in raw.decode('utf-8').split('\0'):
        if not record:continue
        meta, relative = record.split('\t', 1)
        mode, _, stage = meta.split()
        if stage != '0':raise RuntimeError('Unresolved Git index: ' + relative)
        if mode == '160000':raise RuntimeError('Submodule needs an explicit source snapshot: ' + relative)
        yield relative, mode


def verify(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):raise RuntimeError('Duplicate ZIP entries')
        if any(PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name for name in names):
            raise RuntimeError('Unsafe ZIP path')
        manifests = [name for name in names if name.count('/') == 1 and name.endswith('/SOURCE_MANIFEST.json')]
        if len(manifests) != 1:raise RuntimeError('Source manifest missing or ambiguous')
        prefix = manifests[0].rsplit('/', 1)[0] + '/'
        manifest = json.loads(archive.read(manifests[0]))
        expected = {prefix + p for p in manifest['files']} | {manifests[0]}
        if set(names) != expected:raise RuntimeError('ZIP membership differs from manifest')
        for relative, meta in manifest['files'].items():
            p = PurePosixPath(relative)
            if p.is_absolute() or '..' in p.parts or '\\' in relative:raise RuntimeError('Unsafe archive path')
            native = relative.startswith('vendor/hermes-agent/')
            source_path = relative[len('vendor/hermes-agent/'):] if native else relative
            if forbidden(source_path, native):raise RuntimeError('Environment/private file in source archive: ' + relative)
            data = archive.read(prefix + relative)
            if len(data) != meta['bytes'] or hashlib.sha256(data).hexdigest() != meta['sha256']:
                raise RuntimeError('Checksum mismatch: ' + relative)
        required = {'INSTALL.md','README.md','requirements.txt','web_app.py','core/hermes_worker.py',
                    'web/control_api.py','web/control_usage.py','web3/package.json','web3/package-lock.json',
                    'web3/src/main.tsx','tools/run_web3.py','config/AI_config.example.json',
                    'vendor/hermes-agent/pyproject.toml','vendor/hermes-agent/uv.lock',
                    'vendor/hermes-agent/LICENSE','vendor/hermes-agent/cli.py','vendor/hermes-agent/hermes_cli/main.py'}
        if not required <= manifest['files'].keys():raise RuntimeError('Required source files missing: ' + str(required-manifest['files'].keys()))
    print(json.dumps({'verified':str(path),'files':len(manifest['files']),
                      'adgs_commit':manifest['adgs_commit'],'hermes_commit':manifest['hermes_commit']},ensure_ascii=False))
    return manifest


def build(native, output):
    native = native.absolute()
    if not (native/'hermes_cli/main.py').is_file():raise RuntimeError('Not a Hermes source tree')
    commit = subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    native_commit = subprocess.check_output(['git','-C',str(native),'rev-parse','HEAD'],text=True).strip()
    manifest = {'format':1,'kind':'source-only','adgs_commit':commit,'hermes_commit':native_commit,
                'hermes_local_changes':subprocess.check_output(['git','-C',str(native),'diff','--name-only'],text=True).splitlines(),
                'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'files':{},'excluded':[], 'excluded_private_paths':sorted(PRIVATE)}
    # Check for actual configured model keys without printing or shipping them.
    secrets = []
    config = ROOT/'config/AI_config.json'
    if config.is_file():
        for value in json.loads(config.read_text(encoding='utf-8')).values():
            if isinstance(value,dict):
                key=value.get('key')
                if isinstance(key,str) and len(key)>12:secrets.append(key.encode())
    output.mkdir(parents=True,exist_ok=True)
    stem='ADGS-source-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+commit[:8]
    path=output/(stem+'.zip');pending=output/(stem+'.zip.partial')
    if path.exists() or pending.exists():raise RuntimeError('Release path already exists')
    count=0
    with zipfile.ZipFile(pending,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for base, prefix, is_native in ((ROOT,'',False),(native,'vendor/hermes-agent/',True)):
            for relative, mode in tracked(base):
                if forbidden(relative,is_native):manifest['excluded'].append(prefix+relative);continue
                source=base/relative
                if not source.is_file():raise RuntimeError('Tracked source missing: '+str(source))
                if source.is_symlink():raise RuntimeError('Resolve source symlink before distribution: '+str(source))
                payload=source.read_bytes()
                if any(secret in payload for secret in secrets):raise RuntimeError('Configured key found in source: '+relative)
                destination=prefix+relative
                if destination in manifest['files']:raise RuntimeError('Source path collision: '+destination)
                info=zipfile.ZipInfo(stem+'/'+destination)
                info.compress_type=zipfile.ZIP_DEFLATED;info.create_system=3
                info.external_attr=((0o100755 if mode=='100755' else 0o100644)<<16)
                archive.writestr(info,payload)
                manifest['files'][destination]={'sha256':hashlib.sha256(payload).hexdigest(),'bytes':len(payload)}
                count+=1
                if count%3000==0:print('Packaged '+str(count)+' source files',flush=True)
        archive.writestr(stem+'/SOURCE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    try:
        verify(pending)
        pending.replace(path)
    except BaseException:
        # Retain the failed artifact for inspection, never publish it as a ZIP.
        raise
    checksum=hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix('.zip.sha256').write_text(checksum+'  '+path.name+'\n',encoding='ascii')
    print(json.dumps({'archive':str(path),'sha256':checksum,'bytes':path.stat().st_size,'files':count},ensure_ascii=False))
    return path


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-source',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'releases')
    parser.add_argument('--verify',type=Path)
    args=parser.parse_args()
    if args.verify:verify(args.verify)
    elif args.hermes_source:build(args.hermes_source,args.output)
    else:parser.error('Supply --hermes-source PATH to package, or --verify FILE.zip')
