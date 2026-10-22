"""Executed only by packaging; records source/build/runtime identity without installing."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import subprocess
import sys


def validate_launcher(stage):
    """Prove startup uses only the package-relative portable interpreter."""
    cmd_path = stage / 'Start RAS-Plot.cmd'
    launcher_path = stage / 'app/launcher.py'
    if not cmd_path.is_file() or not launcher_path.is_file():
        raise ValueError('Packaged launcher files are missing.')
    cmd = cmd_path.read_text(encoding='utf-8-sig')
    launcher = launcher_path.read_text(encoding='utf-8-sig')
    expected = r'"%~dp0runtime\python\python.exe"'
    lines = [line.strip() for line in cmd.splitlines() if line.strip()]
    missing_checks = [line for line in lines
                      if line.lower().startswith(('if not exist ' + expected).lower())]
    invocations = [line for line in lines
                   if line.lower().startswith((expected + ' ').lower())]
    if len(missing_checks) != 1 or len(invocations) != 1:
        raise ValueError('Launcher must check and invoke runtime\\python\\python.exe exactly once using %~dp0.')
    if not re.search(r'(?im)^\s*echo\s+error:.*bundled.*python.*missing', cmd):
        raise ValueError('Launcher must clearly report a missing bundled Python runtime.')
    forbidden = (
        r'(?im)^\s*(?:call\s+)?"?(?:python(?:\.exe)?|py(?:\.exe)?)"?(?:\s|$)',
        r'(?im)^\s*where\s+(?:python|py)(?:\.exe)?(?:\s|$)',
        r'(?i)%path%',
    )
    if any(re.search(pattern, cmd) for pattern in forbidden):
        raise ValueError('Launcher contains a PATH/system-Python lookup or fallback.')
    normalized = re.sub(r'\s+', ' ', launcher)
    portable_assignment = re.search(
        r'python_exe\s*=\s*package_root\s*/\s*["\']runtime["\']\s*/\s*'
        r'["\']python["\']\s*/\s*["\']python\.exe["\']', normalized)
    if not portable_assignment or not re.search(r'command\s*=\s*\[\s*str\(python_exe\)', normalized):
        raise ValueError('Python launcher must derive and execute the interpreter relative to package_root.')
    if re.search(r'\bsys\.executable\b|\bshutil\.which\b|\bwhere\s+python\b', launcher, re.I):
        raise ValueError('Python launcher contains a system-interpreter fallback.')
    return {'cmd': str(cmd_path), 'python_launcher': str(launcher_path),
            'interpreter': r'runtime\python\python.exe'}


def validate_no_node_runtime(stage):
    """Reject actual Node runtime entry points, not unrelated staged files."""
    forbidden = {'node.exe', 'npm.cmd', 'npx.cmd'}
    matches = sorted(
        str(path.relative_to(stage)).replace('\\', '/')
        for path in stage.rglob('*')
        if path.is_file() and path.name.lower() in forbidden
    )
    if matches:
        raise ValueError(
            'Packaged Node/npm runtime dependency is not permitted: '
            + ', '.join(matches)
        )
    return {'forbidden_runtime_files': [], 'status': 'PASS'}


def validate_frontend_assets(root, stage):
    """Prove staged HTML and bundles exactly match the validated local build."""
    built = root / 'backend/app/static'
    static = stage / 'app/backend/app/static'
    html = (static / 'index.html').read_text(encoding='utf-8')
    if 'unpkg.com' in html:
        raise ValueError('External unpkg dependency is not permitted.')
    refs = sorted(set(re.findall(r'(?:src|href)="(/assets/[^"?]+)"', html)))
    if not any(ref.endswith('.js') for ref in refs):
        raise ValueError('missing asset: production JavaScript reference is absent.')
    if not any(ref.endswith('.css') for ref in refs):
        raise ValueError('missing asset: production CSS reference is absent.')
    referenced = {ref.lstrip('/') for ref in refs}
    present = {
        str(path.relative_to(static)).replace('\\', '/')
        for path in (static / 'assets').rglob('*') if path.is_file()
    }
    if referenced != present:
        missing = sorted(referenced - present)
        stale = sorted(present - referenced)
        raise ValueError(
            f'Frontend asset correspondence failed; missing={missing}, stale={stale}.'
        )
    staged_files = {
        str(path.relative_to(static)).replace('\\', '/')
        for path in static.rglob('*') if path.is_file()
    }
    built_files = {
        str(path.relative_to(built)).replace('\\', '/')
        for path in built.rglob('*') if path.is_file()
    }
    if staged_files != built_files:
        raise ValueError('Staged frontend file set differs from the validated build.')
    for relative in sorted(staged_files):
        staged_file = static / relative
        built_file = built / relative
        if not built_file.is_file() or digest(staged_file) != digest(built_file):
            raise ValueError(
                f'Staged frontend asset differs from the validated build: {relative}'
            )
    return {'references': refs, 'assets': sorted(present), 'status': 'PASS'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frontend_identity(root):
    frontend = root / 'frontend'
    paths = list((frontend / 'src').rglob('*'))
    paths += [frontend / n for n in ('index.html', 'package.json', 'package-lock.json', 'vite.config.ts', 'tsconfig.json')]
    return {str(p.relative_to(root)).replace('\\', '/'): digest(p)
            for p in sorted(paths) if p.is_file()}


def source_tree_identity(root):
    paths = list((root / 'backend/app').rglob('*.py'))
    paths += list((root / 'frontend/src').rglob('*'))
    paths += [root / 'backend/requirements.txt', root / 'frontend/index.html',
              root / 'frontend/package.json', root / 'frontend/package-lock.json',
              root / 'frontend/vite.config.ts', root / 'frontend/tsconfig.json']
    files = {str(p.relative_to(root)).replace('\\', '/'): digest(p)
             for p in sorted(set(paths)) if p.is_file()}
    fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    try:
        branch = subprocess.check_output(
            ['git', 'branch', '--show-current'], cwd=root, text=True,
            stderr=subprocess.DEVNULL).strip()
    except Exception:
        branch = 'UNKNOWN'
    return {'fingerprint': fingerprint, 'branch': branch,
            'state': 'UNCOMMITTED_WORKING_TREE', 'files': files}


def build_manifest(root, stage, before, release='RC4', purpose='Engineering Pilot',
                   source_scope='Phase 7C validated working tree'):
    if frontend_identity(root) != json.loads(before.read_text(encoding='utf-8-sig')):
        raise ValueError('Frontend source changed during build; package rejected.')
    validate_frontend_assets(root, stage)
    validate_launcher(stage)
    validate_no_node_runtime(stage)
    files = {str(p.relative_to(stage)).replace('\\', '/'): digest(p)
             for p in sorted(stage.rglob('*')) if p.is_file()}
    build_id = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:20]
    return {'application': 'RAS-Plot Replica', 'version': '0.2.0-dev',
            'release': release, 'purpose': purpose,
            'source_scope': source_scope,
            'build_date': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
            'build_id': build_id,
            'python': sys.version, 'platform': platform.platform(),
            'dependencies': {d.metadata['Name']: d.version for d in importlib.metadata.distributions()},
            'source': source_tree_identity(root),
            'frontend_source': frontend_identity(root), 'files': files,
            'support_document': 'support-matrix.md', 'release_acceptance': 'PENDING'}


if __name__ == '__main__':
    root = Path(sys.argv[2]).resolve()
    if sys.argv[1] == 'source':
        Path(sys.argv[3]).write_text(json.dumps(frontend_identity(root)), encoding='utf-8')
    elif sys.argv[1] == 'launcher':
        print(json.dumps(validate_launcher(Path(sys.argv[3]).resolve()), indent=2))
    else:
        stage = Path(sys.argv[3]).resolve()
        manifest = build_manifest(
            root, stage, Path(sys.argv[4]),
            *(sys.argv[5:8]),
        )
        (stage / 'release-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
