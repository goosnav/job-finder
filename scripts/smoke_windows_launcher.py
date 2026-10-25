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
        node = shutil.which('node')
        assert node, 'The Windows CI runner needs Node for npm CLI integration fixtures.'
        npm = base / 'roaming/npm'
        npm.mkdir(parents=True)
        shutil.copy2(node, npm / 'node.exe')
        env['APPDATA'] = str(base / 'roaming')
        env['USERPROFILE'] = str(base / 'home')
        fixture = r'''const fs=require('fs');
const cp=require('child_process');
process.on('uncaughtException',e=>{fs.appendFileSync(process.env.TEST_AGENT_LOG+'.errors',e.message+'\n');process.exit(1);});
const args=process.argv.slice(2);
if(args.includes('--version')) {console.log('1.0.0');process.exit(0);}
const prompt=fs.readFileSync(0,'utf8');
if(!prompt.includes('QA jobs near Δ')) {throw new Error('stdin UTF-8 transport failed');}
const bypass=args[0]==='exec'?'--dangerously-bypass-approvals-and-sandbox':'--dangerously-skip-permissions';
if(!args.includes(bypass)) {throw new Error('permission bypass missing');}
const schema=args[0]==='exec'?JSON.parse(fs.readFileSync(args[args.indexOf('--output-schema')+1],'utf8')):JSON.parse(args[args.indexOf('--json-schema')+1]);
const discovery=!!schema.properties.opportunities;
if(discovery && args[0]==='exec' && !args.includes('web_search="live"')) {throw new Error('live web search missing');}
if(args[0]!=='exec' && JSON.parse(args[args.indexOf('--settings')+1]).sandbox.enabled!==false) {throw new Error('shell sandbox still enabled');}
// Start a tool by name from the recovered PATH and make an actual HTTPS request.
const probe=cp.spawnSync('node',['-e',`
require('https').get('https://example.com',r=>{
let body='';r.setEncoding('utf8');r.on('data',c=>body+=c);
r.on('end',()=>{if(r.statusCode!==200 || !body.includes('Example Domain')) process.exit(2);
console.log('public-https-ok');});
}).on('error',()=>process.exit(3)).setTimeout(15000,function(){this.destroy();});
`],{encoding:'utf8',timeout:20000,windowsHide:true});
if(probe.error || probe.status!==0 || !probe.stdout.includes('public-https-ok')) {
 throw new Error('Network tool failed: '+(probe.error?.code || probe.status+' '+probe.stderr));
}
const profile={summary:'Fictional Windows integration profile',suggested_roles:['QA Engineer'],search_terms:['QA engineer jobs'],strengths:['Testing'],gaps:[],unknowns:[]};
const result=discovery?{opportunities:[],coverage_notes:'Fixture child tool verified public HTTPS'}:profile;
fs.appendFileSync(process.env.TEST_AGENT_LOG,JSON.stringify(args)+'\n');
if(args[0]==='exec') {
 fs.writeFileSync(args[args.indexOf('--output-last-message')+1],JSON.stringify(result));
 console.log(JSON.stringify({type:'turn.completed',usage:{input_tokens:1,output_tokens:1}}));
} else console.log(JSON.stringify({type:'result',subtype:'success',structured_output:result,usage:{input_tokens:1,output_tokens:1},total_cost_usd:0}));
'''
        for provider, entry in [('codex','@openai/codex/bin/codex.js'),('claude','@anthropic-ai/claude-code/cli.js')]:
            for suffix in ('', '.cmd', '.ps1'):
                (npm / (provider + suffix)).write_text('Do not execute this shell shim.')
            target = npm / 'node_modules' / entry
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(fixture, encoding='utf-8')
        env['TEST_AGENT_LOG'] = str(base / 'agent-calls.jsonl')
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
                        child = subprocess.Popen([str(powershell), '-NoProfile', '-ExecutionPolicy', 'RemoteSigned', '-File', str(clone / 'Open Job Finder.ps1'), '-NoPause'], cwd=base, env=env, stdout=log, stderr=subprocess.STDOUT)
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
                        for provider in ('codex', 'claude'):
                            search = request(url, '/api/searches', {'name': provider+' npm integration', 'objective':'Find QA jobs near Δ', 'settings':{'provider':provider,'discovery_queries':1,'target_results':1}}, session['token'])
                            path = '/api/searches/'+search['id']
                            try:
                                request(url, path+'/runs', {'stage':'profile'}, session['token'])
                                raise AssertionError('Unapproved agent tools ran.')
                            except urllib.error.HTTPError as error:
                                assert error.code == 400
                            req = urllib.request.Request(url+path, data=b'{"settings":{"agent_tools_approved":true}}', headers={'Content-Type':'application/json','X-Session-Token':session['token']}, method='PATCH')
                            with urllib.request.urlopen(req,timeout=5):
                                pass
                            for stage in ('profile', 'discover'):
                                job = request(url, path+'/runs', {'stage':stage}, session['token'])['run']
                                for _ in range(300):
                                    run = request(url,'/api/runs/'+job['id'])['run']
                                    if run['status'] not in ('queued','running'):
                                        break
                                    time.sleep(.1)
                                diagnostic = Path(env['TEST_AGENT_LOG']+'.errors')
                                assert run['status']=='completed',(run, diagnostic.read_text() if diagnostic.exists() else '')
                            assert request(url,path)['search']['profile']['summary']=='Fictional Windows integration profile'
                        calls = [json.loads(line) for line in (base/'agent-calls.jsonl').read_text().splitlines()]
                        assert any('--dangerously-bypass-approvals-and-sandbox' in args for args in calls)
                        assert any('--dangerously-skip-permissions' in args for args in calls)
                        assert any('web_search="live"' in args for args in calls)

                        # A second opener must return without starting another server.
                        reopened = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'RemoteSigned', '-File', str(clone / 'Open Job Finder.ps1'), '-NoPause'], cwd=base, env=env, capture_output=True, text=True, timeout=20)
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
            # A failed opener must leave the original failure available after the window closes.
            (app / 'uv.lock').unlink()
            failed = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(clone / 'Open Job Finder.ps1'), '-NoPause'], cwd=base, env=env, capture_output=True, text=True, timeout=20)
            assert failed.returncode == 1
            assert 'LAUNCH-ROOT' in failed.stdout
            transcripts = list((base / 'local/JobFinder/logs').glob('windows-startup-*.log'))
            assert any('LAUNCH-ROOT' in path.read_text(encoding='utf-8-sig') for path in transcripts)
        finally:
            if url:
                try:
                    request(url, '/api/quit', {}, request(url, '/api/session')['token'])
                except OSError:
                    pass
            if child and child.poll() is None:
                subprocess.run(['taskkill.exe', '/PID', str(child.pid), '/T', '/F'], capture_output=True)
                child.wait(timeout=10)
    print('PASS: fresh Windows clone, managed runtime, browser request, reopen, offline second launch, persistence, both npm agent transports with bypass flags, UTF-8 prompts, recovered child tool PATH and public HTTPS, saved startup failure, Quit; no generated files in clone.')


if __name__ == '__main__':
    run()
