"""Record immutable source identity, actual dependencies and packaged file hashes."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ['pandas','matplotlib','openpyxl','websocket-client','lunardate']


def runtime_dependencies() -> dict[str, str]:
    result: dict[str, str] = {}
    pending = RUNTIME.copy()
    while pending:
        name = pending.pop().lower().replace('_','-')
        if name in result:
            continue
        dist = importlib.metadata.distribution(name)
        result[name] = dist.version
        for value in dist.requires or []:
            requirement = Requirement(value)
            if requirement.marker is None or requirement.marker.evaluate():
                pending.append(requirement.name)
    return dict(sorted(result.items()))


def git(*args: str) -> str:
    return subprocess.check_output(['git','-C',str(ROOT),*args], text=True, encoding='utf-8').strip()


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--app-dir', type=Path)
    parser.add_argument('--requirements-out', type=Path)
    args=parser.parse_args()
    deps=runtime_dependencies()
    if args.requirements_out:
        args.requirements_out.write_text(''.join(f'{k}=={v}\n' for k,v in deps.items()), encoding='utf-8')
    if not args.app_dir:
        print(json.dumps(deps, indent=2))
        return
    app=args.app_dir.resolve()
    info={'version':(ROOT/'VERSION').read_text(encoding='utf-8').strip(),
          'source_commit':git('rev-parse','HEAD'),'source_tree':git('rev-parse','HEAD^{tree}'),
          'source_dirty':bool(git('status','--porcelain')),
          'built_at_utc':datetime.now(timezone.utc).isoformat(),
          'python':platform.python_version(),'platform':platform.platform(),
          'pyinstaller':importlib.metadata.version('pyinstaller'),'runtime_dependencies':deps}
    if info['source_dirty']:
        raise SystemExit('Refusing to label an uncommitted source tree as a release; commit reviewed source first.')
    (app/'build-info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest={}
    for path in sorted(app.rglob('*')):
        if path.is_file() and path.name!='release-manifest.json':
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream,'sha256').hexdigest()
            manifest[path.relative_to(app).as_posix()]={'size':path.stat().st_size,
                                                       'sha256':digest}
    (app/'release-manifest.json').write_text(json.dumps({'version':info['version'],'source_commit':info['source_commit'],
        'source_tree':info['source_tree'],'files':manifest},ensure_ascii=False,indent=2),encoding='utf-8')
    print('RELEASE_MANIFEST_OK',len(manifest),info['source_commit'])


if __name__=='__main__': main()
