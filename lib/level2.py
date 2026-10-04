#!/usr/bin/env python3
"""Level 2: the real-Unity gate, run on a machine with Unity installed and its licence activated in Unity Hub.

For each editor (default 6000.0.41f1):
  1. create or refresh the smoke project (default: config/family.json "smokeProject") from
     smoke-template/: Packages/manifest.json gets a "file:" reference to each selected package and lists every
     one of them under "testables"; Samples~ folders are copied into Assets/Samples the way the Package
     Manager's Import button does. By default the reference is the package checkout itself (mutable: Unity writes
     any missing .meta file next to the asset in the checkout). With --tarball, each package's HEAD is packed the
     way OpenUPM publishes a tag (git archive of the commit, then npm pack) into <smoke>/Tarballs, the manifest
     points at the .tgz files (immutable, as a registry install is), and samples are copied out of the tarballs;
  2. run the editor in batchmode to import the project, then once more for EditMode and once more for PlayMode
     tests (-runTests -testResults);
  3. report the tests run, passed and failed per test assembly (NUnit XML), the compile errors and
     warnings and every "has no meta file" message in the editor logs (with --tarball this is the immutable-folder
     message a registry user would see), the samples imported, and - for checkout installs - the untracked .meta
     files in each package checkout (git status), marking those this run created, and any tracked file the
     import changed.

--no-editor prepares the smoke project (and packs the tarballs) without starting Unity.
Safety: refuses to run when the smoke project is open in another Unity process (Temp/UnityLockfile held)
or sits inside a git work tree; never builds a player; prints Library size and free disk.
Exit status: 0 everything passed, 1 the gate failed, 2 the editor could not run (lock, licence, timeout) or a
package could not be packed.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (CONFIG, TOOLS, config_path, dir_size, family_config, family_root, free_disk,  # noqa: E402
                    human_size, load_json, selected_repos, table)
from unity import DEFAULT_EDITOR, UnityInstall  # noqa: E402
import pack as P  # noqa: E402

TEMPLATE = os.path.join(TOOLS, 'smoke-template')
STATE = '.upm-tools.json'
TARBALLS = 'Tarballs'      # under the smoke project, next to Assets/ and Packages/; Unity does not import it
COMPILE_RE = re.compile(r'^(?P<file>[^\n]*?\.cs)\((?P<line>\d+),(?P<col>\d+)\): (?P<sev>error|warning) '
                        r'(?P<code>CS\d+): (?P<msg>.*)$')
NO_META_RE = re.compile(r'has no meta file')
ORPHAN_META_RE = re.compile(r'A meta data file \(\.meta\) exists but its (?:asset|folder)')
# Lines that mean the editor could not do its job. The licensing client logs harmless "Error: Code 500"
# lines on every successful Personal-licence run, so only the definitive messages are listed.
FATAL_RES = [re.compile(p) for p in (r'No valid Unity Editor license', r'License is not active',
                                     r'An error occurred while resolving packages',
                                     r'Cannot open the project because it is already open',
                                     r'Aborting batchmode due to failure')]


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='level2.sh', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--editor', action='append', default=None,
                    help='editor version or path; repeat for several (default: %s; add 6000.3.3f1 for the '
                         'newer LTS)' % DEFAULT_EDITOR)
    ap.add_argument('--packages', help='comma-separated repo folders to install (default: all six family '
                                       'packages); family dependencies are added automatically')
    ap.add_argument('--root', help='folder holding one checkout per package repo (default: $OPENUGD_ROOT or '
                                   'config/family.json "root")')
    ap.add_argument('--smoke', default=None, help='smoke project folder (default: config/family.json '
                                                  '"smokeProject")')
    ap.add_argument('--tests', default='editmode,playmode', help='editmode, playmode, both (default) or none')
    ap.add_argument('--no-samples', action='store_true', help='do not copy Samples~ into Assets/Samples')
    ap.add_argument('--tarball', action='store_true',
                    help='install each package from a .tgz of its HEAD, packed as OpenUPM packs a tag (git archive '
                         'of the commit, then npm pack), instead of from the checkout; uncommitted changes are not '
                         'in it')
    ap.add_argument('--no-npm', action='store_true', help='with --tarball: write the tarball from the git '
                                                          'export without npm (npm\'s ignore rules not applied)')
    ap.add_argument('--no-editor', action='store_true', help='prepare the smoke project (and the tarballs) and stop '
                                                             'without starting Unity')
    ap.add_argument('--graphics', action='store_true', help='run without -nographics')
    ap.add_argument('--clean', action='store_true', help='delete the smoke project\'s Library first')
    ap.add_argument('--delete-library', action='store_true', help='delete Library afterwards to free disk')
    ap.add_argument('--timeout', type=int, default=40, help='minutes allowed per editor run (default 40)')
    ap.add_argument('--min-free-gb', type=float, default=3.0, help='refuse to start below this much free '
                                                                   'disk (default 3)')
    a = ap.parse_args(argv)
    a.editor = a.editor or [DEFAULT_EDITOR]
    t = a.tests.lower()
    a.modes = [] if t == 'none' else (['EditMode', 'PlayMode'] if t in ('both', 'editmode,playmode',
                                                                       'playmode,editmode') else
                                      [{'editmode': 'EditMode', 'playmode': 'PlayMode'}[x.strip()]
                                       for x in t.split(',')])
    return a


# --- safety ---------------------------------------------------------------------------------------------
def inside_git(path):
    probe = path
    while not os.path.exists(probe):
        probe = os.path.dirname(probe)
    p = subprocess.run(['git', '-C', probe, 'rev-parse', '--show-toplevel'], stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL)
    return p.stdout.decode().strip() if p.returncode == 0 else None


def lock_holders(smoke):
    """PIDs holding the smoke project's Temp/UnityLockfile, plus Unity processes started on it."""
    pids = set()
    lock = os.path.join(smoke, 'Temp', 'UnityLockfile')
    if os.path.exists(lock):
        p = subprocess.run(['lsof', '-t', lock], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        pids |= {x for x in p.stdout.decode().split() if x.strip()}
    p = subprocess.run(['pgrep', '-f', '-l', 'Unity.*-projectPath[ =]+%s' % re.escape(smoke)],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    for line in p.stdout.decode().splitlines():
        pid = line.split()[0]
        if pid != str(os.getpid()):
            pids.add(pid)
    return sorted(pids)


# --- smoke project --------------------------------------------------------------------------------------
def family_closure(root, repos):
    by_name = {}
    for p in family_config()['packages']:
        pj = os.path.join(root, p['repo'], 'package.json')
        if os.path.exists(pj):
            by_name[load_json(pj)['name']] = p['repo']
    out, added = list(repos), []
    i = 0
    while i < len(out):
        for dep in load_json(os.path.join(root, out[i], 'package.json')).get('dependencies') or {}:
            if dep in by_name and by_name[dep] not in out:
                out.append(by_name[dep])
                added.append(by_name[dep])
        i += 1
    return out, added


def refresh_project(a, smoke, root, repos, unity, packed=None):
    os.makedirs(smoke, exist_ok=True)
    state_path = os.path.join(smoke, STATE)
    state = load_json(state_path) if os.path.exists(state_path) else {}
    lib = os.path.join(smoke, 'Library')
    if os.path.isdir(lib) and (a.clean or state.get('editor') not in (None, unity.version)):
        print('  deleting Library (%s)' % ('--clean' if a.clean else 'last opened with %s' % state.get('editor')))
        shutil.rmtree(lib)
    for d, _, files in os.walk(TEMPLATE):
        for f in files:
            src = os.path.join(d, f)
            dst = os.path.join(smoke, os.path.relpath(src, TEMPLATE))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(src, dst)
    manifest = load_json(os.path.join(TEMPLATE, 'Packages', 'manifest.json'))
    cfg_path = os.path.join(CONFIG, 'unity', unity.version + '.json')
    if os.path.exists(cfg_path):
        versions = load_json(cfg_path)['packages']
        for k in list(manifest['dependencies']):
            if k in versions:
                manifest['dependencies'][k] = versions[k]
    packages = []
    for repo in repos:
        path = os.path.join(root, repo)
        if packed:
            info = packed[repo]
            pj = {'name': info['name'], 'version': info['version'], 'displayName': info['displayName'],
                  'samples': info['samples']}
            # Relative to Packages/, so the manifest does not depend on where the smoke project lives.
            manifest['dependencies'][pj['name']] = 'file:../%s/%s' % (TARBALLS, os.path.basename(info['tgz']))
        else:
            pj = load_json(os.path.join(path, 'package.json'))
            manifest['dependencies'][pj['name']] = 'file:' + path
        packages.append((repo, pj))
    manifest['testables'] = sorted(set(manifest.get('testables', [])) | {pj['name'] for _, pj in packages})
    with open(os.path.join(smoke, 'Packages', 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2)
        f.write('\n')
    lock = os.path.join(smoke, 'Packages', 'packages-lock.json')
    if os.path.exists(lock):
        os.remove(lock)
    with open(os.path.join(smoke, 'ProjectSettings', 'ProjectVersion.txt'), 'w') as f:
        f.write('m_EditorVersion: %s\n' % unity.version)
    assets = os.path.join(smoke, 'Assets')
    os.makedirs(assets, exist_ok=True)
    samples_root = os.path.join(assets, 'Samples')
    if os.path.isdir(samples_root):
        shutil.rmtree(samples_root)
        if os.path.exists(samples_root + '.meta'):
            os.remove(samples_root + '.meta')
    copied = []
    if not a.no_samples:
        for repo, pj in packages:
            for s in pj.get('samples') or []:
                label = s.get('displayName', s['path'])
                dst = os.path.join(samples_root, pj.get('displayName', pj['name']), pj['version'],
                                   s.get('displayName', os.path.basename(s['path'])))
                if packed:
                    files, no_meta = P.extract_sample(packed[repo]['tgz'], s['path'], dst)
                    if not files:
                        copied.append((repo, label, 'MISSING in the tarball: %s' % s['path'], []))
                        continue
                    copied.append((repo, label, os.path.relpath(dst, smoke), no_meta))
                    continue
                src = os.path.join(root, repo, s['path'])
                if not os.path.isdir(src):
                    copied.append((repo, label, 'MISSING: %s' % s['path'], []))
                    continue
                shutil.copytree(src, dst)
                copied.append((repo, label, os.path.relpath(dst, smoke), []))
    state['editor'] = unity.version
    state['refreshed'] = datetime.datetime.now().isoformat(timespec='seconds')
    with open(state_path, 'w') as f:
        json.dump(state, f, indent=2)
    return manifest, copied


# --- git state of the package checkouts ----------------------------------------------------------------
def git_state(repo_path):
    p = subprocess.run(['git', '-C', repo_path, 'status', '--porcelain', '--untracked-files=all'],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    untracked, changed = set(), set()
    for line in p.stdout.decode('utf-8', 'replace').splitlines():
        code, path = line[:2], line[3:]
        if code == '??':
            if path.endswith('.meta'):
                untracked.add(path)
        else:
            changed.add((code.strip(), path))
    return untracked, changed


# --- editor runs ----------------------------------------------------------------------------------------
def run_editor(unity, smoke, args, log, timeout_min):
    cmd = [unity.executable, '-batchmode', '-projectPath', smoke, '-logFile', log] + args
    t0 = time.time()
    try:
        p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout_min * 60)
        code = p.returncode
    except subprocess.TimeoutExpired:
        code = 'timeout'
    return code, time.time() - t0


def scan_log(path):
    res = {'errors': [], 'warnings': [], 'no_meta': [], 'orphan_meta': [], 'fatal': []}
    if not os.path.exists(path):
        res['fatal'].append('no log written at %s' % path)
        return res
    seen = set()
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n')
            m = COMPILE_RE.match(line.strip())
            if m:
                key = (m.group('file'), m.group('line'), m.group('code'), m.group('msg'))
                if key not in seen:
                    seen.add(key)
                    res['errors' if m.group('sev') == 'error' else 'warnings'].append(line.strip())
                continue
            if NO_META_RE.search(line):
                if line.strip() not in res['no_meta']:
                    res['no_meta'].append(line.strip())
            elif ORPHAN_META_RE.search(line):
                if line.strip() not in res['orphan_meta']:
                    res['orphan_meta'].append(line.strip())
            elif any(r.search(line) for r in FATAL_RES):
                if line.strip() not in res['fatal']:
                    res['fatal'].append(line.strip())
    return res


def parse_results(path):
    """NUnit 3 XML from -testResults: one row per test assembly."""
    if not os.path.exists(path):
        return None
    root = ET.parse(path).getroot()
    suites = []
    for s in root.iter('test-suite'):
        if s.get('type') != 'Assembly':
            continue
        fails = []
        for tc in s.iter('test-case'):
            if tc.get('result') == 'Failed':
                msg = tc.find('failure/message')
                fails.append('%s: %s' % (tc.get('fullname'), (msg.text or '').strip().splitlines()[0]
                                         if msg is not None and msg.text else ''))
        suites.append({'assembly': s.get('name'), 'total': int(s.get('total', 0)),
                       'passed': int(s.get('passed', 0)), 'failed': int(s.get('failed', 0)),
                       'skipped': int(s.get('skipped', 0)) + int(s.get('inconclusive', 0)), 'failures': fails})
    return {'total': int(root.get('total', 0)), 'passed': int(root.get('passed', 0)),
            'failed': int(root.get('failed', 0)), 'suites': suites}


def gate_editor(a, smoke, root, repos, spec, packed=None):
    unity = UnityInstall(spec)
    print('\n== Unity %s' % unity.version)
    res = {'editor': unity.version, 'runs': {}, 'ok': True, 'tooling': [], 'tarball': bool(packed)}
    if not os.path.exists(unity.executable) and not a.no_editor:
        res['tooling'].append('editor executable missing: %s' % unity.executable)
        res['ok'] = False
        return res
    manifest, copied = refresh_project(a, smoke, root, repos, unity, packed)
    res['samples'] = copied
    print('  manifest: %s' % ', '.join('%s=%s' % kv for kv in manifest['dependencies'].items()))
    if a.no_editor:
        res['no_editor'] = True
        return res
    # A tarball is immutable: Unity cannot write into the checkouts, which other work may be changing meanwhile.
    before = None if packed else {r: git_state(os.path.join(root, r)) for r in repos}
    logs = os.path.join(smoke, 'Logs', 'upm-tools')
    os.makedirs(logs, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    nographics = [] if a.graphics else ['-nographics']
    steps = [('import', nographics + ['-quit'], None)]
    for mode in a.modes:
        xml = os.path.join(logs, '%s-%s-%s.xml' % (unity.version, stamp, mode.lower()))
        steps.append((mode, nographics + ['-runTests', '-testPlatform', mode, '-testResults', xml], xml))
    for name, args, xml in steps:
        log = os.path.join(logs, '%s-%s-%s.log' % (unity.version, stamp, name.lower()))
        print('  %-8s running...' % name, end='', flush=True)
        code, secs = run_editor(unity, smoke, args, log, a.timeout)
        scan = scan_log(log)
        results = parse_results(xml) if xml else None
        print(' exit %s in %.0f s' % (code, secs))
        res['runs'][name] = {'exit': code, 'seconds': round(secs), 'log': log, 'scan': scan, 'results': results,
                             'xml': xml}
        if code == 'timeout':
            res['tooling'].append('%s timed out after %d min' % (name, a.timeout))
            break
        if name == 'import' and scan['fatal'] and code != 0:
            res['tooling'].append('import failed: %s' % scan['fatal'][0])
            break
        if name == 'import' and scan['errors']:
            res['skipped_tests'] = 'not run: the import reported compile errors'
            break
    res['git'] = {}
    if before is not None:
        after = {r: git_state(os.path.join(root, r)) for r in repos}
        for r in repos:
            u0, c0 = before[r]
            u1, c1 = after[r]
            res['git'][r] = {'untracked_meta': sorted(u1), 'new_untracked_meta': sorted(u1 - u0),
                             'changed_by_run': sorted(c1 - c0)}
    lib = os.path.join(smoke, 'Library')
    res['library_bytes'] = dir_size(lib) if os.path.isdir(lib) else 0
    if a.delete_library and os.path.isdir(lib):
        shutil.rmtree(lib)
        res['library_deleted'] = True
    return res


def report_packing(packed, repos):
    """What each tarball holds, against the commit it was packed from. Returns False on a packing problem."""
    ok = True
    print('\n== Tarballs (packed from HEAD of each checkout)')
    rows = []
    for r in repos:
        p = packed[r]
        rows.append([r, '%s %s' % (p['name'], p['version']), p['sha'][:10], len(p['files']), human_size(p['size']),
                     len(p['dropped']), 'yes' if p['dirty'] else 'no', os.path.basename(p['tgz'])])
    print(table(rows, ['repo', 'package', 'commit', 'files', 'size', 'tracked, not packed',
                       'uncommitted changes', 'tarball']))
    print('  method: %s' % ', '.join(sorted({packed[r]['method'] for r in repos})))
    for r in repos:
        p = packed[r]
        for f in p['dropped'][:40]:
            print('  NOT PACKED  %-22s %s   <- tracked by git, left out by npm\'s ignore rules' % (r, f))
            ok = False
        for f in p['extra'][:40]:
            print('  EXTRA       %-22s %s   <- in the tarball but not in the commit' % (r, f))
            ok = False
        if p['dirty']:
            print('  note        %-22s %d uncommitted change(s) are not in the tarball' % (r, len(p['dirty'])))
        for n in p['notes']:
            print('  note        %-22s %s' % (r, n))
    return ok


def report(results, repos, smoke, packed=None):
    ok = True
    if packed:
        ok = report_packing(packed, repos)
    for res in results:
        print('\n== Report: Unity %s%s' % (res['editor'], '  (packages installed from tarballs)'
                                          if res.get('tarball') else ''))
        for t in res['tooling']:
            print('  TOOLING: %s' % t)
        if res.get('no_editor'):
            print('  --no-editor: the smoke project is prepared; Unity was not started')
        rows = []
        for name, run in res['runs'].items():
            s = run['scan']
            rows.append([name, run['exit'], '%d s' % run['seconds'], len(s['errors']), len(s['warnings']),
                         len(s['no_meta']), len(s['orphan_meta']), os.path.relpath(run['log'], smoke)])
        if rows:
            print(table(rows, ['run', 'exit', 'time', 'compile errors', 'warnings', 'no-meta msgs',
                               'orphan-meta msgs', 'log (in smoke project)']))
        suites = []
        if res.get('skipped_tests'):
            print('  tests %s' % res['skipped_tests'])
            ok = False
        for name, run in res['runs'].items():
            if run['results'] is None:
                if name != 'import':
                    suites.append([name, '(no results file)', '', '', '', '', 'FAIL'])
                    ok = False
                continue
            if not run['results']['suites']:
                good = run['exit'] == 0
                suites.append([name, '(no %s tests in the selected packages)' % name, 0, 0, 0, 0,
                               'n/a' if good else 'FAIL'])
                ok = ok and good
            for st in run['results']['suites']:
                good = st['failed'] == 0 and st['total'] > 0
                ok = ok and good
                suites.append([name, st['assembly'], st['total'], st['passed'], st['failed'], st['skipped'],
                               'PASS' if good else 'FAIL'])
        if suites:
            print()
            print(table(suites, ['mode', 'test assembly', 'total', 'passed', 'failed', 'skipped', 'status']))
        for name, run in res['runs'].items():
            for st in (run['results'] or {}).get('suites', []):
                for f in st['failures'][:25]:
                    print('  FAILED [%s] %s' % (name, f))
        all_errors, all_warnings, no_meta, orphan, fatal = [], [], [], [], []
        for run in res['runs'].values():
            for k, dst in (('errors', all_errors), ('warnings', all_warnings), ('no_meta', no_meta),
                           ('orphan_meta', orphan), ('fatal', fatal)):
                for x in run['scan'][k]:
                    if x not in dst:
                        dst.append(x)
        for title, items in (('compile errors', all_errors), ('"has no meta file" messages', no_meta),
                             ('orphan .meta messages', orphan), ('licence / package / batchmode problems', fatal),
                             ('compile warnings', all_warnings)):
            if items:
                print('\n  %s (%d):' % (title, len(items)))
                for x in items[:60]:
                    print('    ' + x)
        if res.get('samples'):
            print('\n  samples imported into Assets/Samples: %d (%s)' % (
                len(res['samples']), ', '.join('%s/%s' % (r, n) for r, n, _, _ in res['samples'])))
            for r, n, where, sample_no_meta in res['samples']:
                if where.startswith('MISSING'):
                    print('    %s %s: %s' % (r, n, where))
                    ok = False
                if sample_no_meta:
                    # Not an error in Assets/, but the asset gets a new GUID on every import.
                    print('    note: %s %s has %d asset(s) without a .meta (new GUIDs on every import): %s' % (
                        r, n, len(sample_no_meta), ', '.join(sample_no_meta[:8]) +
                        (' ...' if len(sample_no_meta) > 8 else '')))
        if res.get('git'):
            print('\n  untracked .meta files per package checkout (git status):')
            for r in repos:
                g = res['git'][r]
                if not g['untracked_meta']:
                    print('    %-22s none' % r)
                for m in g['untracked_meta']:
                    print('    %-22s %s%s' % (r, m, '   <- created by this run' if m in g['new_untracked_meta']
                                              else ''))
                for code, path in g['changed_by_run']:
                    print('    %-22s %s %s   <- tracked file changed during this run' % (r, code, path))
        elif res.get('tarball') and res['runs']:
            print('\n  package checkouts: not inspected (tarballs are immutable; Unity cannot write into them)')
        if res['runs']:
            print('\n  Library: %s; free disk: %s' % (human_size(res.get('library_bytes', 0)),
                                                     human_size(free_disk(smoke))) +
                  ('  (Library deleted)' if res.get('library_deleted') else ''))
        git = res.get('git') or {}
        if res['tooling'] or all_errors or no_meta or orphan or \
                any(git[r]['untracked_meta'] or git[r]['changed_by_run'] for r in git):
            ok = False
        res['ok'] = ok
    return ok


def main(argv):
    a = parse_args(argv)
    root = family_root(a.root)
    smoke = os.path.abspath(os.path.expanduser(a.smoke)) if a.smoke else config_path(family_config()['smokeProject'])
    repos, added = family_closure(root, selected_repos(a.packages))
    print('LEVEL 2  smoke %s  root %s' % (smoke, root))
    print('         packages %s%s' % (', '.join(repos), ('  (added as dependencies: %s)' % ', '.join(added))
                                      if added else ''))
    top = inside_git(smoke)
    if top:
        print('refusing: %s is inside the git work tree %s; keep the smoke project out of every repo' % (smoke, top))
        return 2
    holders = lock_holders(smoke)
    if holders:
        print('refusing: the smoke project is in use (pid %s holds Temp/UnityLockfile or runs Unity on it)'
              % ', '.join(holders))
        return 2
    free = free_disk(os.path.dirname(smoke) if not os.path.exists(smoke) else smoke)
    print('         free disk %s' % human_size(free))
    if free < a.min_free_gb * 1024 ** 3:
        print('refusing: less than %.1f GB free' % a.min_free_gb)
        return 2
    packed = None
    if a.tarball:
        dest = os.path.join(smoke, TARBALLS)
        shutil.rmtree(dest, ignore_errors=True)
        packed = {}
        for r in repos:
            try:
                packed[r] = P.pack(os.path.join(root, r), dest, use_npm=not a.no_npm)
            except (RuntimeError, OSError, ValueError, KeyError) as e:
                print('refusing: cannot pack %s: %s' % (r, e))
                return 2
        print('         packed %d tarball(s) into %s' % (len(packed), dest))
    results = []
    for spec in a.editor:
        results.append(gate_editor(a, smoke, root, repos, spec, packed))
        if not a.no_editor and lock_holders(smoke):
            print('warning: a Unity process still holds the smoke project after the run')
    ok = report(results, repos, smoke, packed)
    out = os.path.join(smoke, 'Logs', 'upm-tools', 'level2-report.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print('\nreport: %s' % out)
    if packed:
        with open(os.path.join(os.path.dirname(out), 'level2-tarballs.json'), 'w') as f:
            json.dump(packed, f, indent=2, default=str)
        print('tarballs: %s' % os.path.join(os.path.dirname(out), 'level2-tarballs.json'))
    tooling = any(r['tooling'] for r in results)
    if a.no_editor:
        print('LEVEL 2: NOT RUN (--no-editor: smoke project prepared%s)' % (
            '' if not packed else '; packing %s' % ('clean' if ok else 'has findings')))
        return 2 if tooling else (0 if ok else 1)
    print('LEVEL 2: %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else (2 if tooling else 1)


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
