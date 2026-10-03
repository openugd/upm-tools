#!/usr/bin/env python3
"""IL2CPP smoke: build one IL2CPP player of the package family at Medium stripping and check that it boots a container.

The linker gate runs the real UnityLinker but executes the stripped assemblies on desktop Mono. This builds an actual
IL2CPP player - WebGL, the IL2CPP target every editor install here has (WebGL is always IL2CPP) - so AOT compilation
and the stripped player's runtime are exercised too:

  1. pack each family package from its checkout's HEAD the way OpenUPM publishes a tag (lib/pack.py) and create or
     refresh the throwaway project (default ~/workspace/openugd/v2/il2cpp-smoke, outside every git
     repository) from il2cpp-template/: the family tarballs plus com.unity.ugui, the Boot scripts, the build script
     and a .jslib that copies the player's summary into the page title;
  2. build it in batchmode through -executeMethod Il2CppSmoke.Editor.SmokeBuild.Run: WebGL, release, Managed Stripping
     Level Medium (--stripping high for High), compression Disabled; build errors fail the run;
  3. check the build folder (index.html, the loader, framework, .data and .wasm), print build time, build size,
     Library size and free disk, and delete the bulky intermediates (the final build is kept);
  4. serve the build with python3 -m http.server on a free localhost port, check that index.html and the .wasm
     answer 200, and open the page in headless Chrome (a throwaway profile) until document.title carries the
     player's summary line, "OPENUGD-IL2CPP: PASS <n>/<n>" or "OPENUGD-IL2CPP: FAIL <k>/<n> <names>";
  5. stop the server, or with --serve leave it running and print its URL and how to stop it.

Safety: refuses when the project sits inside a git work tree, when a Unity process holds the project, or when less
than --min-free-gb (4) of disk is free; stops the build if free disk drops under that or the project grows past
--max-project-gb (6). Never touches the package checkouts (they are only read through git).

Exit status: 0 the player reported PASS (or, with --no-check, the build and the server checks passed); 1 the gate
failed (build or compile errors, missing build files, an HTTP error, the player reported FAIL or never reported);
2 the tools could not run (lock, licence, disk, timeout, packing, no Chrome without --no-check).
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import CONFIG, TOOLS, dir_size, family_config, family_root, free_disk, human_size, load_json, \
    selected_repos, table  # noqa: E402
from unity import DEFAULT_EDITOR, UnityInstall  # noqa: E402
import headless  # noqa: E402
import level2 as L2  # noqa: E402
import pack as P  # noqa: E402

TEMPLATE = os.path.join(TOOLS, 'il2cpp-template')
DEFAULT_PROJECT = '~/workspace/openugd/v2/il2cpp-smoke'
STATE = '.upm-tools.json'
TARBALLS = 'Tarballs'
BUILD_DIR = os.path.join('Build', 'WebGL')
LOGS = os.path.join('Logs', 'upm-tools')
EXECUTE_METHOD = 'Il2CppSmoke.Editor.SmokeBuild.Run'
LEVELS = {'medium': 'Medium', 'high': 'High'}
GB = 1024 ** 3
WATCH_POLL, SIZE_EVERY = 5, 30      # seconds between free-disk and project-size checks while the editor runs

SUMMARY_PREFIX = 'OPENUGD-IL2CPP:'
SUMMARY_RE = re.compile(r'^OPENUGD-IL2CPP: (?:(?P<pass>PASS) (?P<p>\d+)/(?P<pn>\d+)|'
                        r'(?P<fail>FAIL) (?P<k>\d+)/(?P<fn>\d+)(?: (?P<names>\S+))?)\s*$')
CHECK_RE = re.compile(r'^OPENUGD-IL2CPP check (?P<name>\S+): (?P<status>PASS|FAIL)(?: (?P<detail>.*))?$')

# The editor log lines that mean the licence, not the project, stopped the run. Only consulted when the run failed:
# the licensing client logs harmless errors on successful Personal-licence runs too.
LICENCE_RES = [re.compile(p, re.I) for p in (
    r'No valid Unity Editor license', r'License is not active', r'No ULF license found',
    r'\blicen[cs]e\b.*\b(expired|not valid|invalid|revoked)\b', r'No user entitlements',
    r'Failed to (?:activate|update|validate) (?:the )?licen[cs]e', r'Licen[cs]e (?:validation|activation) failed')]

# Folders a finished build no longer needs, relative to the project. Library/Bee/artifacts/WebGL holds IL2CPP's C++
# output and the compiled objects: by far the largest part of a WebGL build. Deleting it makes the next build
# regenerate it; the import cache (Library/Artifacts, Library/PackageCache, ScriptAssemblies) is kept.
INTERMEDIATES = [os.path.join('Library', 'Bee', 'artifacts', 'WebGL'), os.path.join('Library', 'Il2cppBuildCache'),
                 os.path.join('Library', 'PlayerDataCache'), 'Temp']
# The kinds of file an uncompressed WebGL build must contain under Build/, with the suffix that identifies each.
BUILD_KINDS = [('loader', '.loader.js'), ('framework', '.framework.js'), ('data', '.data'), ('wasm', '.wasm')]
COMPRESSED_SUFFIXES = ('.gz', '.br', '.unityweb')
# Console lines of headless Chrome's software GL and of the static server that say nothing about the player.
CONSOLE_NOISE = ('GL Driver Message', 'favicon.ico')


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='il2cpp-smoke.sh', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--editor', default=DEFAULT_EDITOR, help='editor version or path (default: %s; needs its WebGL '
                                                             'module)' % DEFAULT_EDITOR)
    ap.add_argument('--stripping', default='medium', choices=sorted(LEVELS),
                    help='Managed Stripping Level of the player (default: medium)')
    ap.add_argument('--project', help='the throwaway project (default: config/family.json "il2cppSmokeProject", '
                                      'else %s); must be outside every git work tree' % DEFAULT_PROJECT)
    ap.add_argument('--root', help='folder holding one checkout per package repo (default: $OPENUGD_ROOT or '
                                   'config/family.json "root")')
    ap.add_argument('--packages', help='comma-separated repo folders to install (default: all six family packages); '
                                       'family dependencies are added automatically')
    ap.add_argument('--no-npm', action='store_true', help='write the tarballs from the git export without npm')
    ap.add_argument('--serve', action='store_true', help='leave the HTTP server running after the script exits')
    ap.add_argument('--port', type=int, default=0, help='port for the server (default: a free one)')
    ap.add_argument('--no-build', action='store_true', help='skip packing and building: check and serve the build '
                                                            'already in the project')
    ap.add_argument('--no-editor', action='store_true', help='pack and prepare the project, then stop without '
                                                             'starting Unity')
    ap.add_argument('--no-check', action='store_true', help='do not open the page in headless Chrome (the server '
                                                            'checks still run); the run cannot then report PASS')
    ap.add_argument('--chrome', help='Chrome or Chromium executable for the headless check (default: found under '
                                     '/Applications, or $CHROME)')
    ap.add_argument('--check-timeout', type=int, default=180, help='seconds to wait for the page title to carry '
                                                                   'the summary (default 180)')
    ap.add_argument('--stop', action='store_true', help='stop a server left running by --serve, and exit')
    ap.add_argument('--clean', action='store_true', help='delete the project\'s Library before building')
    ap.add_argument('--keep-intermediates', action='store_true', help='do not delete the build intermediates')
    ap.add_argument('--graphics', action='store_true', help='run the editor without -nographics')
    ap.add_argument('--timeout', type=int, default=60, help='minutes allowed for the editor run (default 60)')
    ap.add_argument('--min-free-gb', type=float, default=4.0, help='refuse to start, and stop the build, below this '
                                                                   'much free disk (default 4)')
    ap.add_argument('--max-project-gb', type=float, default=6.0, help='stop the build when the project grows past '
                                                                      'this size (default 6)')
    return ap.parse_args(argv)


def project_path(arg=None):
    return os.path.abspath(arg or family_config().get('il2cppSmokeProject') or DEFAULT_PROJECT)


# --- project ------------------------------------------------------------------------------------------------------
def template_files(template=TEMPLATE):
    """Template-relative paths of every file in the template (dot-names skipped)."""
    out = []
    for d, dirs, files in os.walk(template):
        dirs[:] = sorted(x for x in dirs if not x.startswith('.'))
        out += [os.path.relpath(os.path.join(d, f), template) for f in sorted(files) if not f.startswith('.')]
    return out


def build_manifest(template_manifest, packed, unity_versions):
    """Packages/manifest.json for the project: the template's Unity packages at the editor map's versions, plus one
    file: reference per family tarball, relative to Packages/ (as level2 --tarball writes it)."""
    manifest = json.loads(json.dumps(template_manifest))
    deps = manifest.setdefault('dependencies', {})
    for name in list(deps):
        if name in unity_versions:
            deps[name] = unity_versions[name]
    for info in packed:
        deps[info['name']] = 'file:../%s/%s' % (TARBALLS, os.path.basename(info['tgz']))
    manifest.pop('testables', None)
    return manifest


def refresh_project(project, unity, packed, clean=False, template=TEMPLATE, config_dir=CONFIG):
    """Create or refresh the project from the template. Files the template no longer has, but an earlier refresh
    copied, are removed with their .meta. Library is deleted when the editor changes or with clean. Returns the
    manifest written."""
    os.makedirs(project, exist_ok=True)
    state_path = os.path.join(project, STATE)
    state = load_json(state_path) if os.path.exists(state_path) else {}
    lib = os.path.join(project, 'Library')
    if os.path.isdir(lib) and (clean or state.get('editor') not in (None, unity.version)):
        print('  deleting Library (%s)' % ('--clean' if clean else 'last opened with %s' % state.get('editor')))
        shutil.rmtree(lib)
    files = template_files(template)
    for rel in files:
        dst = os.path.join(project, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(os.path.join(template, rel), dst)
    for rel in sorted(set(state.get('templateFiles') or []) - set(files)):
        for path in (os.path.join(project, rel), os.path.join(project, rel) + '.meta'):
            if os.path.isfile(path):
                os.remove(path)
    cfg = os.path.join(config_dir, 'unity', unity.version + '.json')
    versions = load_json(cfg)['packages'] if os.path.exists(cfg) else {}
    manifest = build_manifest(load_json(os.path.join(template, 'Packages', 'manifest.json')), packed, versions)
    with open(os.path.join(project, 'Packages', 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2)
        f.write('\n')
    lock = os.path.join(project, 'Packages', 'packages-lock.json')
    if os.path.exists(lock):
        os.remove(lock)
    os.makedirs(os.path.join(project, 'ProjectSettings'), exist_ok=True)
    with open(os.path.join(project, 'ProjectSettings', 'ProjectVersion.txt'), 'w') as f:
        f.write('m_EditorVersion: %s\n' % unity.version)
    state.update(editor=unity.version, templateFiles=files,
                 refreshed=datetime.datetime.now().isoformat(timespec='seconds'))
    with open(state_path, 'w') as f:
        json.dump(state, f, indent=2)
    return manifest


# --- editor run ---------------------------------------------------------------------------------------------------
def build_command(unity, project, log, stripping, output, result, graphics=False):
    return ([unity.executable, '-batchmode'] + ([] if graphics else ['-nographics']) +
            ['-projectPath', project, '-logFile', log, '-buildTarget', 'WebGL', '-executeMethod', EXECUTE_METHOD,
             '-smokeStripping', LEVELS[stripping], '-smokeOutput', output, '-smokeResult', result])


def du_bytes(path):
    """Size of a folder through du (much faster than walking a Library in Python); 0 if it does not exist."""
    if not os.path.exists(path):
        return 0
    p = subprocess.run(['du', '-sk', path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        return int(p.stdout.split()[0]) * 1024
    except (IndexError, ValueError):
        return dir_size(path)


def disk_guard(free, project_bytes, min_free, max_project):
    """Why the build must stop now, or None. Sizes in bytes; project_bytes None when not measured this time."""
    if free < min_free:
        return 'free disk dropped to %s, under the %s minimum' % (human_size(free), human_size(min_free))
    if project_bytes is not None and project_bytes > max_project:
        return 'the project grew to %s, past the %s limit' % (human_size(project_bytes), human_size(max_project))
    return None


def run_watched(cmd, project, timeout_s, min_free, max_project, poll=None, size_every=None):
    """Run the editor, polling free disk (every `poll` s) and the project size (every `size_every` s); stop the
    whole process group when disk_guard objects or the timeout passes. Returns (exit code or None, seconds,
    stop reason or None, peak project bytes, lowest free bytes)."""
    poll = WATCH_POLL if poll is None else poll
    size_every = SIZE_EVERY if size_every is None else size_every
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    reason, peak, lowest, last_size = None, 0, free_disk(project), 0.0
    while proc.poll() is None:
        time.sleep(poll)
        free = free_disk(project)
        lowest = min(lowest, free)
        size = None
        if time.time() - last_size >= size_every:
            size, last_size = du_bytes(project), time.time()
            peak = max(peak, size)
        reason = disk_guard(free, size, min_free, max_project)
        if reason is None and time.time() - t0 > timeout_s:
            reason = 'the editor run passed the %d-minute timeout' % (timeout_s // 60)
        if reason:
            stop_group(proc)
            break
    return proc.returncode if not reason else None, time.time() - t0, reason, peak, lowest


def stop_group(proc, grace=30):
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 10)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def licence_problems(log_path):
    """Editor log lines that blame the licence."""
    if not os.path.exists(log_path):
        return []
    out = []
    with open(log_path, encoding='utf-8', errors='replace') as f:
        for line in f:
            if any(r.search(line) for r in LICENCE_RES) and line.strip() not in out:
                out.append(line.strip())
    return out


def read_result(path):
    try:
        return load_json(path)
    except (OSError, ValueError):
        return None


# --- build folder -------------------------------------------------------------------------------------------------
def verify_build(build_dir):
    """What the build folder holds. ok only with index.html and one uncompressed file of each kind in Build/."""
    out = {'dir': build_dir, 'files': {}, 'missing': [], 'unexpected': [], 'sizes': {}}
    if not os.path.isfile(os.path.join(build_dir, 'index.html')):
        out['missing'].append('index.html')
    else:
        out['files']['index'] = 'index.html'
    names = sorted(os.listdir(os.path.join(build_dir, 'Build'))) if os.path.isdir(
        os.path.join(build_dir, 'Build')) else []
    for kind, suffix in BUILD_KINDS:
        hits = [n for n in names if n.endswith(suffix)]
        if not hits:
            out['missing'].append('Build/*' + suffix)
        else:
            out['files'][kind] = 'Build/' + hits[0]
    out['unexpected'] = ['Build/' + n for n in names if n.endswith(COMPRESSED_SUFFIXES)]
    for kind, rel in out['files'].items():
        out['sizes'][kind] = os.path.getsize(os.path.join(build_dir, rel))
    out['total'] = dir_size(build_dir) if os.path.isdir(build_dir) else 0
    out['ok'] = not out['missing'] and not out['unexpected']
    return out


def delete_intermediates(project, paths=INTERMEDIATES):
    """Delete the intermediates that exist; returns [(relative path, bytes freed)]."""
    freed = []
    for rel in paths:
        path = os.path.join(project, rel)
        if os.path.isdir(path):
            size = du_bytes(path)
            shutil.rmtree(path, ignore_errors=True)
            freed.append((rel, size))
    return freed


def largest_children(path, count=6):
    """The largest direct children of a folder, as [(name, bytes)]."""
    if not os.path.isdir(path):
        return []
    sizes = [(n, du_bytes(os.path.join(path, n))) for n in os.listdir(path)]
    return sorted(sizes, key=lambda x: -x[1])[:count]


# --- serving ------------------------------------------------------------------------------------------------------
def free_port(host='127.0.0.1'):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def http_get(url, method='GET', timeout=10):
    """(status, content type, bytes) or (None, error text, 0)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=timeout) as r:
            body = r.read() if method == 'GET' else b''
            return r.status, r.headers.get('Content-Type', ''), int(r.headers.get('Content-Length') or len(body))
    except urllib.error.HTTPError as e:
        return e.code, str(e.reason), 0
    except (urllib.error.URLError, OSError) as e:
        return None, str(getattr(e, 'reason', e)), 0


def start_server(directory, port, log_path, wait=10):
    """python3 -m http.server on 127.0.0.1:port in its own session, so it outlives this script. Returns the
    process, or raises RuntimeError when it does not answer within `wait` seconds."""
    log = open(log_path, 'ab')
    proc = subprocess.Popen([sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1', '--directory',
                             directory], cwd=directory, stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True)
    log.close()
    deadline = time.time() + wait
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError('the server exited with %s; see %s' % (proc.returncode, log_path))
        status, _, _ = http_get('http://127.0.0.1:%d/' % port, method='HEAD', timeout=2)
        if status is not None:
            return proc
        time.sleep(0.2)
    proc.terminate()
    raise RuntimeError('the server did not answer on port %d within %d s' % (port, wait))


def server_state_path(project):
    return os.path.join(project, LOGS, 'server.json')


def is_our_server(pid, port):
    """True when pid is alive and is the http.server this script started on port."""
    p = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    command = p.stdout.decode('utf-8', 'replace')
    return 'http.server' in command and str(port) in command.split()


def stop_server(project, quiet=False):
    """Stop the server an earlier run left running (server.json). Returns True when one was stopped."""
    path = server_state_path(project)
    if not os.path.exists(path):
        if not quiet:
            print('no server recorded in %s' % path)
        return False
    state = read_result(path) or {}
    stopped = False
    pid, port = state.get('pid'), state.get('port')
    if pid and is_our_server(pid, port):
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
        stopped = True
        print('stopped the server on %s (pid %s)' % (state.get('url'), pid))
    elif not quiet:
        print('the server recorded in %s (pid %s) is not running' % (path, pid))
    os.remove(path)
    return stopped


# --- the player's report ------------------------------------------------------------------------------------------
def parse_summary(text):
    """The summary line as a dict, or None when `text` is not one. FAIL k/n means k of the n checks failed, and
    then names lists exactly those k checks."""
    m = SUMMARY_RE.match((text or '').strip())
    if not m:
        return None
    if m.group('pass'):
        passed, total = int(m.group('p')), int(m.group('pn'))
        if passed != total or total == 0:
            return None
        return {'status': 'PASS', 'failed': 0, 'total': total, 'names': []}
    failed, total = int(m.group('k')), int(m.group('fn'))
    names = [n for n in (m.group('names') or '').split(',') if n]
    if not 0 < failed <= total or len(names) != failed:
        return None
    return {'status': 'FAIL', 'failed': failed, 'total': total, 'names': names}


def parse_console(lines):
    """Per-check lines and summaries found among console texts: ([(name, status, detail)], [summary dicts])."""
    checks, summaries = [], []
    for line in lines:
        for part in line.splitlines():
            part = part.strip()
            m = CHECK_RE.match(part)
            if m:
                checks.append((m.group('name'), m.group('status'), m.group('detail') or ''))
            elif part.startswith(SUMMARY_PREFIX):
                s = parse_summary(part)
                summaries.append(s if s else {'status': 'MALFORMED', 'line': part})
    return checks, summaries


def judge_page(result):
    """Turn headless.open_and_wait's result into (status, summary dict or None, checks, problems)."""
    texts = [t for _, t in result['console']]
    checks, summaries = parse_console(texts)
    summary = parse_summary(result['title']) if result['reached'] else None
    problems = []
    if not result['reached']:
        status = 'NO RESULT'
        problems.append('the page title never carried the summary within %s s (last title: %r)' % (
            result['seconds'], result['title']))
    elif summary is None:
        status = 'MALFORMED'
        problems.append('the title is not a valid summary line: %r' % result['title'])
    else:
        status = summary['status']
        if len([s for s in summaries if s.get('status') != 'MALFORMED']) != 1:
            problems.append('expected exactly one summary line in the console, saw %d' % len(summaries))
        if checks and len(checks) != summary['total']:
            problems.append('the console has %d check line(s), the summary counts %d' % (len(checks),
                                                                                        summary['total']))
    return status, summary, checks, problems


# --- report -------------------------------------------------------------------------------------------------------
def print_build(res):
    print('\n== Build (Unity %s, stripping %s)' % (res['editor'], res['stripping']))
    r = res.get('result') or {}
    rows = [['editor exit', res.get('exit')], ['wall time', '%.0f s' % res.get('seconds', 0)],
            ['BuildReport result', r.get('result', '(no result file)')],
            ['BuildReport time', '%.0f s' % r['totalSeconds'] if 'totalSeconds' in r else '-'],
            ['log', res.get('log')]]
    print(table(rows, ['step', 'value']))
    settings = r.get('settings') or {}
    if settings:
        print('  player settings: ' + ', '.join('%s=%s' % kv for kv in settings.items()))
    licence = res.get('licence') or []
    for title, items in (('compile errors', res['scan']['errors']), ('build errors', r.get('errors') or []),
                         ('licence problems', licence),
                         ('licence / package / batchmode problems', [x for x in res['scan']['fatal']
                                                                     if x not in licence])):
        if items:
            print('\n  %s (%d):' % (title, len(items)))
            for x in items[:40]:
                print('    ' + x.replace('\n', '\n    ')[:2000])
    if r.get('exception'):
        print('\n  the build script threw:\n    ' + r['exception'].replace('\n', '\n    ')[:3000])


def print_sizes(res, project):
    v = res.get('verify')
    if v:
        rows = [[kind, v['files'].get(kind, 'MISSING'), human_size(v['sizes'][kind]) if kind in v['sizes'] else '']
                for kind in ['index'] + [k for k, _ in BUILD_KINDS]]
        print('\n== Build folder %s' % v['dir'])
        print(table(rows, ['file', 'path', 'size']))
        for u in v['unexpected']:
            print('  UNEXPECTED %s: a compressed file, which a plain static server cannot serve' % u)
        print('  build size: %s' % human_size(v['total']))
    s = res.get('sizes')
    if s:
        print('\n== Disk')
        if s.get('library_largest'):
            print('  largest in Library after the build: ' + ', '.join(
                '%s %s' % (n, human_size(b)) for n, b in s['library_largest']))
        for rel, b in s.get('freed') or []:
            print('  deleted %-40s %s' % (rel, human_size(b)))
        print('  Library: %s; project: %s (peak during the build %s); free disk: %s (lowest during the build %s)' % (
            human_size(s['library']), human_size(s['project']), human_size(s.get('peak', 0)),
            human_size(free_disk(project)), human_size(s.get('lowest', 0))))


def main(argv):
    a = parse_args(argv)
    project = project_path(a.project)
    logs = os.path.join(project, LOGS)
    if a.stop:
        stop_server(project)
        return 0
    root = family_root(a.root)
    print('IL2CPP SMOKE  project %s  stripping %s%s' % (project, LEVELS[a.stripping],
                                                       '  (--no-build)' if a.no_build else ''))
    top = L2.inside_git(project)
    if top:
        print('refusing: %s is inside the git work tree %s; keep the project out of every repo' % (project, top))
        return 2
    holders = L2.lock_holders(project)
    if holders:
        print('refusing: the project is in use (pid %s holds Temp/UnityLockfile or runs Unity on it)'
              % ', '.join(holders))
        return 2
    free = free_disk(project if os.path.exists(project) else os.path.dirname(project))
    print('              free disk %s' % human_size(free))
    if free < a.min_free_gb * GB and not a.no_build:
        print('refusing: less than %.1f GB free' % a.min_free_gb)
        return 2
    os.makedirs(logs, exist_ok=True)
    stop_server(project, quiet=True)
    report = {'project': project, 'stripping': LEVELS[a.stripping], 'tooling': [], 'failures': []}
    build_dir = os.path.join(project, BUILD_DIR)
    unity = None

    if not a.no_build:
        repos, added = L2.family_closure(root, selected_repos(a.packages))
        print('              packages %s%s' % (', '.join(repos), ('  (added as dependencies: %s)' % ', '.join(added))
                                               if added else ''))
        unity = UnityInstall(a.editor)
        webgl = os.path.join(unity.root, 'PlaybackEngines', 'WebGLSupport')
        if not os.path.isdir(webgl) and not a.no_editor:
            print('refusing: %s has no WebGL module (%s)' % (unity.version, webgl))
            return 2
        dest = os.path.join(project, TARBALLS)
        shutil.rmtree(dest, ignore_errors=True)
        packed = {}
        for r in repos:
            try:
                packed[r] = P.pack(os.path.join(root, r), dest, use_npm=not a.no_npm)
            except (RuntimeError, OSError, ValueError, KeyError) as e:
                print('refusing: cannot pack %s: %s' % (r, e))
                return 2
        if not L2.report_packing(packed, repos):
            report['failures'].append('packing: a tracked file is missing from a tarball, or an extra one is in it')
        report['packed'] = {r: {k: packed[r][k] for k in ('name', 'version', 'sha', 'size', 'method', 'dirty')}
                            for r in repos}
        manifest = refresh_project(project, unity, [packed[r] for r in repos], clean=a.clean)
        print('\n  manifest: %s' % ', '.join('%s=%s' % kv for kv in manifest['dependencies'].items()))
        if a.no_editor:
            print('\nIL2CPP SMOKE: NOT RUN (--no-editor: the project is prepared in %s)' % project)
            return 1 if report['failures'] else 0

        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        log = os.path.join(logs, '%s-%s-build-%s.log' % (unity.version, stamp, a.stripping))
        result_path = os.path.join(logs, 'build-result.json')
        for stale in (result_path,):
            if os.path.exists(stale):
                os.remove(stale)
        shutil.rmtree(build_dir, ignore_errors=True)     # a failed build must not leave the old one looking valid
        print('\n  building (WebGL, IL2CPP, %s stripping)...' % LEVELS[a.stripping], end='', flush=True)
        cmd = build_command(unity, project, log, a.stripping, build_dir, result_path, a.graphics)
        code, secs, reason, peak, lowest = run_watched(cmd, project, a.timeout * 60, a.min_free_gb * GB,
                                                       a.max_project_gb * GB)
        print(' exit %s in %.0f s' % (code, secs))
        res = {'editor': unity.version, 'stripping': LEVELS[a.stripping], 'exit': code, 'seconds': round(secs),
               'log': log, 'scan': L2.scan_log(log), 'result': read_result(result_path), 'stopped': reason}
        report['build'] = res
        built = code == 0 and (res['result'] or {}).get('result') == 'Succeeded'
        if reason:
            report['tooling'].append('stopped the build: ' + reason)
        elif not built:
            res['licence'] = licence_problems(log)
            if res['licence']:
                report['tooling'].append('the Unity licence is not valid: sign in to Unity Hub (and activate a '
                                         'licence there), then run this again')
            elif res['scan']['errors']:
                report['failures'].append('the project has compile errors')
            elif res['result'] is None and res['scan']['fatal']:
                report['tooling'].append('the editor could not run: %s' % res['scan']['fatal'][0])
            else:
                report['failures'].append('the build failed (%s)' % ((res['result'] or {}).get('result')
                                                                      or 'exit %s, no result file' % code))
        print_build(res)
        res['verify'] = verify_build(build_dir) if built else None
        sizes = {'peak': peak, 'lowest': lowest, 'library_largest': largest_children(os.path.join(project, 'Library'))}
        if not a.keep_intermediates and not L2.lock_holders(project):
            sizes['freed'] = delete_intermediates(project)
        sizes['library'] = du_bytes(os.path.join(project, 'Library'))
        sizes['project'] = du_bytes(project)
        res['sizes'] = sizes
        print_sizes(res, project)
        if sizes['project'] > a.max_project_gb * GB:
            print('  warning: the project is larger than %.1f GB after the cleanup' % a.max_project_gb)
        if built and not res['verify']['ok']:
            report['failures'].append('the build folder is incomplete: %s' % ', '.join(
                res['verify']['missing'] + res['verify']['unexpected']))
    else:
        v = verify_build(build_dir)
        report['verify'] = v
        print_sizes({'verify': v}, project)
        if not v['ok']:
            print('refusing: --no-build, and %s holds no complete build (%s)' % (build_dir, ', '.join(
                v['missing'] + v['unexpected'])))
            return 2

    verified = (report.get('build') or {}).get('verify') or report.get('verify')
    page = None
    if verified and verified['ok'] and not report['tooling']:
        port = a.port or free_port()
        url = 'http://127.0.0.1:%d/' % port
        try:
            server = start_server(build_dir, port, os.path.join(logs, 'server.log'))
        except RuntimeError as e:
            report['tooling'].append('could not start the server: %s' % e)
            server = None
        if server:
            print('\n== Server %s (pid %d, serving %s)' % (url, server.pid, build_dir))
            with open(server_state_path(project), 'w') as f:
                json.dump({'pid': server.pid, 'port': port, 'url': url, 'directory': build_dir}, f, indent=2)
            rows, http_failures = [], []
            for kind in ['index'] + [k for k, _ in BUILD_KINDS]:
                status, ctype, length = http_get(url + verified['files'][kind], method='GET' if kind == 'index'
                                                 else 'HEAD')
                rows.append([verified['files'][kind], status, ctype, human_size(length)])
                if status != 200:
                    http_failures.append('HTTP %s for %s' % (status, verified['files'][kind]))
            print(table(rows, ['path', 'status', 'content type', 'length']))
            report['http'] = rows
            report['failures'] += http_failures
            if not a.no_check and not http_failures:
                chrome = headless.find_chrome(a.chrome)
                if not chrome:
                    report['tooling'].append('no Chrome or Chromium found for the headless check: pass --chrome, '
                                             'or --no-check and open %s yourself' % url)
                else:
                    print('\n== Headless check (%s, up to %d s)' % (os.path.basename(chrome), a.check_timeout))
                    try:
                        page = headless.open_and_wait(url, chrome, SUMMARY_PREFIX, timeout=a.check_timeout)
                    except (RuntimeError, OSError) as e:
                        report['tooling'].append('headless Chrome could not be driven: %s' % e)
                if page:
                    status, summary, checks, problems = judge_page(page)
                    report['page'] = {'status': status, 'summary': summary, 'title': page['title'],
                                      'seconds': page['seconds'], 'checks': checks, 'problems': problems,
                                      'console': page['console'], 'exceptions': page['exceptions']}
                    if checks:
                        print(table([[n, s, d[:160]] for n, s, d in checks], ['check', 'status', 'detail']))
                    others = [t for lvl, t in page['console'] if not t.startswith('OPENUGD-IL2CPP')
                              and (lvl in ('error', 'warning', 'assert') or 'xception' in t)
                              and not any(noise in t for noise in CONSOLE_NOISE)]
                    for t in others[:20]:
                        print('  console: ' + t[:300])
                    for t in page['exceptions'][:10]:
                        print('  page exception: ' + t[:300])
                    for p in problems:
                        print('  ' + p)
                    print('  title: %s  (after %s s)' % (page['title'], page['seconds']))
                    if status != 'PASS':
                        report['failures'].append('the player reported %s%s' % (
                            status, (': ' + ', '.join(summary['names'])) if summary and summary['names'] else ''))
                    elif problems:
                        report['failures'].extend(problems)
            if a.serve:
                print('\n  serving %s' % url)
                print('  stop it with: ./il2cpp-smoke.sh --stop   (or: kill %d)' % server.pid)
            else:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                os.remove(server_state_path(project))
                print('\n  server stopped (pass --serve to keep it running)')
    elif a.serve:
        print('\n  not serving: there is no complete build')

    out = os.path.join(logs, 'il2cpp-smoke-report.json')
    with open(out, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    print('\nreport: %s' % out)
    for t in report['tooling']:
        print('TOOLING: %s' % t)
    for t in report['failures']:
        print('FAILED: %s' % t)
    if report['tooling']:
        print('IL2CPP SMOKE: NOT RUN TO THE END')
        return 2
    if report['failures']:
        print('IL2CPP SMOKE: FAIL')
        return 1
    if page is None:
        print('IL2CPP SMOKE: BUILT AND SERVED, PLAYER NOT CHECKED (--no-check): open the URL and read the console')
        return 0
    print('IL2CPP SMOKE: PASS (%s)' % page['title'])
    return 0


def entry():
    try:
        return main(sys.argv[1:])
    except KeyboardInterrupt:
        return 130
    except SystemExit as e:
        if isinstance(e.code, str):     # a tooling problem reported with a message
            sys.stderr.write(e.code + '\n')
            return 2
        raise


if __name__ == '__main__':
    sys.exit(entry())
