#!/usr/bin/env python3
"""Exercise a Windows clone with no bundled launcher or developer tools on PATH."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def request(url, path, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Session-Token'] = token
    req = urllib.request.Request(url + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def run():
    if sys.platform != 'win32':
        raise SystemExit('Run this smoke check on Windows.')
    # The default Windows browser can retain its temporary profile until it exits.
    with tempfile.TemporaryDirectory(prefix='job-finder-windows-', ignore_cleanup_errors=True) as temporary:
        base = Path(temporary)
        clone = base / 'Job Finder Δ clone'
        clone.mkdir()
        for name in ('Open Job Finder.cmd', 'Open Job Finder.ps1'):
            shutil.copy2(ROOT / name, clone / name)
        app = clone / 'app'
        app.mkdir()
        for name in ('pyproject.toml', 'uv.lock', '.python-version', 'run.py'):
            shutil.copy2(ROOT / 'app' / name, app / name)
        for name in ('src', 'static'):
            shutil.copytree(ROOT / 'app' / name, app / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (app / 'launcher').mkdir()
        shutil.copy2(ROOT / 'app/launcher/windows-bootstrap.json', app / 'launcher/windows-bootstrap.json')
        files_before = {p.relative_to(clone) for p in clone.rglob('*') if p.is_file()}
        helper = base / 'browser.py'
        helper.write_text("import os,sys\nfrom pathlib import Path\nPath(os.environ['TEST_BROWSER_LOG']).write_text(sys.argv[1])\n")
        env = {key: value for key, value in os.environ.items() if not key.startswith(('UV_', 'GOOSNAV_', 'JOB_FINDER_'))}
        env.update(LOCALAPPDATA=str(base / 'local'), PATH=str(Path(os.environ['WINDIR']) / 'System32'),
                   BROWSER=f'"{sys.executable}" "{helper}" %s', TEST_BROWSER_LOG=str(base / 'browser-url.txt'))
        powershell = Path(os.environ['WINDIR']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        state = base / 'local/JobFinder/runtime/current-instance.json'
        url = None
        child = None
        try:
            for attempt in range(2):
                if attempt:
                    env['UV_OFFLINE'] = '1'
                with (base / f'launch-{attempt}.txt').open('w+') as log:
                    if attempt:
                        child = subprocess.Popen([str(clone / 'Open Job Finder.cmd')], shell=True, cwd=base, env=env, stdout=log, stderr=subprocess.STDOUT)
                    else:
                        child = subprocess.Popen([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(clone / 'Open Job Finder.ps1')], cwd=base, env=env, stdout=log, stderr=subprocess.STDOUT)
                    deadline = time.monotonic() + 300
                    while time.monotonic() < deadline:
                        try:
                            url = json.loads(state.read_text())['url']
                            if request(url, '/health/ready')['ready'] and (base / 'browser-url.txt').exists():
                                break
                        except (OSError, ValueError, KeyError):
                            pass
                        if child.poll() is not None:
                            log.seek(0)
                            raise AssertionError(log.read())
                        time.sleep(.5)
                    else:
                        log.seek(0)
                        raise AssertionError('Startup timed out:\n' + log.read())
                    assert (base / 'browser-url.txt').read_text() == url
                    session = request(url, '/api/session')
                    if not attempt:
                        saved = request(url, '/api/searches', {'name': 'Windows launch persistence'}, session['token'])
                        # A second opener must return without starting another server.
                        reopened = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(clone / 'Open Job Finder.ps1')], cwd=base, env=env, capture_output=True, text=True, timeout=20)
                        assert reopened.returncode == 0, reopened.stdout + reopened.stderr
                        assert json.loads(state.read_text())['url'] == url
                    else:
                        assert saved['id'] in [s['id'] for s in request(url, '/api/searches')['searches']]
                    request(url, '/api/quit', {}, session['token'])
                    child.wait(timeout=30)
                    assert child.returncode == 0
                    url = None
                    (base / 'browser-url.txt').unlink()
                    if attempt:
                        log.seek(0)
                        assert 'Downloading the private startup tool' not in log.read()
            assert not (app / '.venv').exists()
            assert files_before == {p.relative_to(clone) for p in clone.rglob('*') if p.is_file()}
        finally:
            if url:
                try:
                    request(url, '/api/quit', {}, request(url, '/api/session')['token'])
                except OSError:
                    pass
            if child and child.poll() is None:
                subprocess.run(['taskkill.exe', '/PID', str(child.pid), '/T', '/F'], capture_output=True)
                child.wait(timeout=10)
    print('PASS: fresh Windows clone, automatic managed runtime, browser request, reopen, offline second launch, persistence, Quit; no generated files in clone.')


if __name__ == '__main__':
    run()
