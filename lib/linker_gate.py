#!/usr/bin/env python3
"""Linker gate: run the real UnityLinker of an installed editor over a probe that uses com.openugd.context
the way a game does, and fail unless the container still works after Medium and High managed code
stripping (audit P0-4).

Pipeline, per editor:
  1. compile com.openugd.lifetime and com.openugd.context from the package sources, then the probe
     (linker/probe/App.cs), with the editor's own Roslyn against its netstandard.dll and the player
     defines, optimised, as a player build does;
  2. run UnityLinker with the options the editor passes for an IL2CPP macOS player (--use-editor-options,
     --dotnetruntime=il2cpp, unityaot-macos class libraries) at rule set Aggressive (Medium) and
     Experimental (High), rooting the probe through linker/probe/root.xml;
  3. inspect the stripped assemblies (linker/inspect) and assert that
       - the constructors the container calls survive: implicit, greedy (no attribute), [Inject];
       - every [Inject] field, property and constructor survives AND still carries [Inject] (the
         container reads the attribute at runtime; an attribute the linker removes means injection
         silently does nothing);
       - OpenUGD.InjectAttribute itself, its constructor and its Optional setter survive;
       - no IL2091/IL2087 trim warning in the linker log mentions OpenUGD;
  4. run the stripped probe on the editor's Mono and require every CHECK line to be True.

The gate needs no licence and never starts the editor.
"""
import argparse
import json
import os
import platform
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asmdefs as A          # noqa: E402
import versions              # noqa: E402
from common import CONFIG, TOOLS, family_root, load_json, run, table  # noqa: E402
from unity import DEFAULT_EDITOR, UnityInstall  # noqa: E402

LEVELS = {'minimal': 'Minimal', 'low': 'Conservative', 'medium': 'Aggressive', 'high': 'Experimental'}
INJECT = 'OpenUGD.InjectAttribute'
PROBE = os.path.join(TOOLS, 'linker', 'probe')
INSPECT = os.path.join(TOOLS, 'linker', 'inspect')

# (type, member kind, member name, parameter count or None, must carry [Inject])
EXPECTED = [
    ('Probe.Clock', 'ctor', '.ctor', 0, False),
    ('Probe.SaveService', 'ctor', '.ctor', 1, False),
    ('Probe.Profile', 'ctor', '.ctor', 1, False),
    ('Probe.OneOff', 'ctor', '.ctor', 1, False),
    ('Probe.Marked', 'ctor', '.ctor', 1, True),
    ('Probe.Members', 'ctor', '.ctor', 0, False),
    ('Probe.Members', 'field', 'Field', None, True),
    ('Probe.Members', 'property', 'Property', None, True),
    ('Probe.Members', 'field', '_private', None, True),
    ('Probe.WithOptional', 'property', 'Localization', None, True),
    ('Probe.WithOptional', 'property', 'Clock', None, True),
    ('Probe.FromFactory', 'property', 'Injected', None, True),
    ('Probe.ViewLike', 'property', 'Clock', None, True),
    ('Probe.ViewLike', 'field', '_save', None, True),
]
RUNTIME_CHECKS = ['greedy-constructor', 'awake-boot', 'inject-constructor', 'inject-field', 'inject-property',
                  'inject-private-field', 'inject-optional', 'factory-member-injection',
                  'instantiate-unregistered', 'inject-existing-object']


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='linker-gate.sh', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--editor', action='append', default=None,
                    help='editor version or path; repeat for several (default: %s; 6000.3.3f1 is supported)'
                         % DEFAULT_EDITOR)
    ap.add_argument('--levels', default='medium,high',
                    help='comma-separated stripping levels: %s (default: medium,high)' % ', '.join(LEVELS))
    ap.add_argument('--root', help='folder holding upm-lifetime and upm-context (default: $OPENUGD_ROOT or '
                                   'config/family.json "root")')
    ap.add_argument('--out', help='work folder (default: $OPENUGD_HARNESS_OUT/linker or ./out/linker)')
    ap.add_argument('--no-run', action='store_true', help='skip executing the stripped probe on Mono')
    ap.add_argument('--probe', default=PROBE, help='folder with App.cs and root.xml (default: linker/probe); '
                                                  'for testing the gate itself')
    a = ap.parse_args(argv)
    a.editor = a.editor or [DEFAULT_EDITOR]
    a.levels = [x.strip().lower() for x in a.levels.split(',') if x.strip()]
    bad = [x for x in a.levels if x not in LEVELS]
    if bad:
        ap.error('unknown level(s): %s' % ', '.join(bad))
    return a


def runtime_sources(root, repo):
    pkg = A.load_family(root, [repo])[0]
    rt = [x for x in pkg.asmdefs if x.kind == 'runtime']
    if len(rt) != 1:
        raise SystemExit('%s: expected one runtime asmdef, found %s' % (repo, [x.name for x in rt]))
    return rt[0]


def player_defines(unity):
    path = os.path.join(CONFIG, 'unity', unity.version + '.json')
    if not os.path.exists(path):
        path = os.path.join(CONFIG, 'unity', DEFAULT_EDITOR + '.json')
    cfg = load_json(path)
    d = cfg['defines']
    return versions.unity_version_symbols(unity.version) + d.get('common', []) + d.get('player', []), cfg


def csc(unity, work, name, target, sources, refs, defines, nowarn):
    dotnet, cscdll = unity.csc
    out = os.path.join(work, name + ('.exe' if target == 'exe' else '.dll'))
    rsp = os.path.join(work, name + '.rsp')
    lines = ['-target:' + target, '-out:"%s"' % out, '-nostdlib+', '-noconfig', '-langversion:9.0',
             '-deterministic', '-optimize+', '-debug:portable', '-nologo',
             '-nowarn:' + ','.join(nowarn)] + ['-define:' + d for d in dict.fromkeys(defines)]
    lines += ['-r:"%s"' % r for r in refs] + ['"%s"' % s for s in sources]
    with open(rsp, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    code, text = run([dotnet, cscdll, '@' + rsp], timeout=600)
    errors = [l for l in text.splitlines() if ': error ' in l]
    return code == 0 and os.path.exists(out), out, text, errors


def build_inspector(out):
    """Build linker/inspect in the work folder, so the repository never gets bin/ or obj/."""
    src = os.path.join(out, 'inspect')
    os.makedirs(src, exist_ok=True)
    for f in ('Inspect.csproj', 'Program.cs'):
        shutil.copy(os.path.join(INSPECT, f), os.path.join(src, f))
    dll = os.path.join(src, 'bin', 'Inspect.dll')
    code, text = run(['dotnet', 'build', os.path.join(src, 'Inspect.csproj'), '-nologo', '-v', 'q',
                      '-o', os.path.dirname(dll)], timeout=600)
    if code != 0 or not os.path.exists(dll):
        sys.stdout.write(text)
        raise SystemExit('could not build the inspector')
    return dll


def inspect(inspector, paths):
    code, text = run(['dotnet', inspector] + paths, timeout=300)
    if code != 0:
        raise SystemExit('inspector failed: %s' % text)
    return json.loads(text)


def find_member(types, tname, kind, mname, params):
    t = types.get(tname)
    if t is None:
        return None, 'type removed'
    if kind == 'ctor':
        for m in t['methods']:
            if m['name'] == '.ctor' and not m['static'] and m['params'] == params:
                return m, None
        return None, 'constructor(%d) removed' % params
    for m in t['fields' if kind == 'field' else 'properties']:
        if m['name'] == mname:
            return m, None
    return None, '%s removed' % kind


def link(unity, work, level, probe_exe, ctx_dll, lt_dll, probe_dir):
    out = os.path.join(work, level)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    aot = unity.unityaot
    framework = sorted([os.path.join(aot, f) for f in os.listdir(aot) if f.endswith('.dll')] +
                       [os.path.join(aot, 'Facades', f) for f in os.listdir(os.path.join(aot, 'Facades'))
                        if f.endswith('.dll')])
    arch = 'ARM64' if platform.machine() in ('arm64', 'aarch64') else 'x64'
    cmd = [unity.linker, '--use-editor-options', '--dotnetprofile=unityaot-macos', '--dotnetruntime=il2cpp',
           '--platform=MacOSX', '--architecture=' + arch, '--rule-set=' + LEVELS[level], '--out=' + out,
           '--include-link-xml=' + os.path.join(probe_dir, 'root.xml'),
           '--include-unity-root-assembly=' + probe_exe,
           '--search-directory=' + os.path.dirname(probe_exe), '--search-directory=' + aot,
           '--search-directory=' + os.path.join(aot, 'Facades'), '--enable-report']
    cmd += ['--allowed-assembly=' + p for p in [probe_exe, ctx_dll, lt_dll] + framework]
    code, text = run(cmd, timeout=1800)
    log = out + '.log'
    with open(log, 'w') as f:
        f.write(text)
    return code, out, log, text


def run_probe(unity, stripped_dir, work, level):
    rundir = os.path.join(work, level + '-run')
    shutil.rmtree(rundir, ignore_errors=True)
    os.makedirs(rundir)
    for name in ('Probe.exe', 'Probe.dll', 'com.openugd.context.dll', 'com.openugd.lifetime.dll'):
        src = os.path.join(stripped_dir, name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(rundir, 'Probe.exe' if name.startswith('Probe.') else name))
    # Unity's Mono defaults to its net_4_x profile, which lacks a GAC entry for System.Core; point it at the
    # full 4.5 class libraries. Only the probe and the OpenUGD assemblies come from the linker's output.
    profile = os.path.join(os.path.dirname(os.path.dirname(unity.mono)), 'lib', 'mono', '4.5')
    code, text = run([unity.mono, os.path.join(rundir, 'Probe.exe')], cwd=rundir, timeout=300,
                     extra_env={'MONO_PATH': profile + ':' + os.path.join(profile, 'Facades')})
    checks = dict(re.findall(r'^CHECK (\S+)=(True|False)\s*$', text, re.M))
    return code, text, checks


def gate_editor(a, root, spec, out):
    unity = UnityInstall(spec)
    work = os.path.join(out, unity.version)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    results = {'editor': unity.version, 'levels': {}, 'tooling': []}
    for tool in (unity.linker, unity.csc[0], unity.csc[1]):
        if not os.path.exists(tool):
            results['tooling'].append('missing %s' % tool)
    if results['tooling']:
        return results
    defines, cfg = player_defines(unity)
    nowarn = cfg.get('noWarn', [])
    ns = unity.netstandard_refs()
    lt = runtime_sources(root, 'upm-lifetime')
    ctx = runtime_sources(root, 'upm-context')
    ok1, lt_dll, t1, e1 = csc(unity, work, 'com.openugd.lifetime', 'library', lt.sources, ns, defines, nowarn)
    ok2 = ok3 = False
    if ok1:
        ok2, ctx_dll, t2, e2 = csc(unity, work, 'com.openugd.context', 'library', ctx.sources, ns + [lt_dll],
                                   defines, nowarn)
    if ok1 and ok2:
        ok3, probe_exe, t3, e3 = csc(unity, work, 'Probe', 'exe', [os.path.join(a.probe, 'App.cs')],
                                     ns + [lt_dll, ctx_dll], defines, nowarn)
    if not (ok1 and ok2 and ok3):
        failed = 'com.openugd.lifetime' if not ok1 else 'com.openugd.context' if not ok2 else 'the probe'
        errs = e1 if not ok1 else e2 if not ok2 else e3
        results['tooling'].append('compiling %s failed:\n      %s' % (failed, '\n      '.join(errs[:20])))
        return results
    inspector = build_inspector(out)
    unstripped = inspect(inspector, [probe_exe, ctx_dll])
    for level in a.levels:
        r = {'checks': [], 'warnings': [], 'runtime': None, 'linker_exit': None}
        results['levels'][level] = r
        code, sdir, log, text = link(unity, work, level, probe_exe, ctx_dll, lt_dll, a.probe)
        r['linker_exit'], r['log'] = code, log
        probe_out = next((os.path.join(sdir, n) for n in ('Probe.exe', 'Probe.dll')
                          if os.path.exists(os.path.join(sdir, n))), None)
        ctx_out = os.path.join(sdir, 'com.openugd.context.dll')
        if code != 0 or not probe_out or not os.path.exists(ctx_out):
            r['checks'].append(('UnityLinker run', False, 'exit %d; see %s' % (code, log)))
            continue
        data = inspect(inspector, [probe_out, ctx_out])
        probe_types = data.get('Probe', {})
        ctx_types = data.get('com.openugd.context', {})
        for tname, kind, mname, params, needs_inject in EXPECTED:
            label = '%s %s' % (tname.split('.', 1)[1], ('ctor(%d)' % params) if kind == 'ctor' else mname)
            before, _ = find_member(unstripped.get('Probe', {}), tname, kind, mname, params)
            if before is None:
                r['checks'].append((label, False, 'not in the unstripped probe: the probe and the gate disagree'))
                continue
            m, why = find_member(probe_types, tname, kind, mname, params)
            if m is None:
                r['checks'].append((label, False, why))
            elif needs_inject and INJECT not in m['attrs']:
                r['checks'].append((label, False, 'kept, but [Inject] was removed'))
            elif kind == 'property' and not m['setter']:
                r['checks'].append((label, False, 'kept, but its setter was removed'))
            else:
                r['checks'].append((label, True, ''))
        inj = ctx_types.get(INJECT)
        if inj is None:
            r['checks'].append(('InjectAttribute type', False, 'removed'))
        else:
            r['checks'].append(('InjectAttribute type', True, 'base %s' % inj['base']))
            has_ctor = any(m['name'] == '.ctor' and not m['static'] for m in inj['methods'])
            r['checks'].append(('InjectAttribute ctor', has_ctor, '' if has_ctor else 'removed'))
            has_set = any(m['name'] == 'set_Optional' for m in inj['methods'])
            r['checks'].append(('InjectAttribute set_Optional', has_set, '' if has_set else 'removed'))
        trim = [l.strip() for l in text.splitlines() if re.search(r'\bIL20(91|87)\b', l) and 'OpenUGD' in l]
        r['warnings'] = trim
        r['checks'].append(('no IL2091/IL2087 on OpenUGD', not trim, '%d warning(s)' % len(trim) if trim else ''))
        r['il5999'] = sum(1 for l in text.splitlines() if 'IL5999' in l and 'OpenUGD' in l)
        if not a.no_run:
            if not os.path.exists(unity.mono):
                r['checks'].append(('runtime on Mono', False, 'mono not found at %s' % unity.mono))
            else:
                rc, rtext, checks = run_probe(unity, sdir, work, level)
                r['runtime'] = rtext
                failed_build = re.search(r'^BUILD FAILED: (.*)$', rtext, re.M)
                if failed_build:
                    problems = re.search(r'(\d+) problems? (?:was|were) found', failed_build.group(1))
                    r['checks'].append(('run: BuildAsync', False, 'threw%s' % (
                        ': %s problem(s)' % problems.group(1) if problems else '')))
                else:
                    r['checks'].append(('run: BuildAsync', True, ''))
                for c in ([] if failed_build else RUNTIME_CHECKS):
                    if c not in checks:
                        r['checks'].append(('run: ' + c, False, 'no CHECK line (exit %d)' % rc))
                    else:
                        r['checks'].append(('run: ' + c, checks[c] == 'True', '' if checks[c] == 'True' else 'False'))
                if failed_build:
                    r['build_failure'] = failed_build.group(1)
    return results


def main(argv):
    a = parse_args(argv)
    root = family_root(a.root)
    base = a.out or os.environ.get('OPENUGD_HARNESS_OUT') or os.path.join(TOOLS, 'out')
    out = os.path.abspath(os.path.join(base, 'linker') if not a.out else a.out)
    os.makedirs(out, exist_ok=True)
    print('LINKER GATE  root %s  out %s' % (root, out))
    all_results = [gate_editor(a, root, spec, out) for spec in a.editor]
    columns = []
    matrix = {}
    order = []
    ok = True
    for res in all_results:
        if res['tooling']:
            ok = False
            print('\n%s: could not run' % res['editor'])
            for t in res['tooling']:
                print('  ' + t)
            continue
        for level, r in res['levels'].items():
            col = '%s %s' % (res['editor'], level)
            columns.append(col)
            for label, passed, detail in r['checks']:
                if label not in order:
                    order.append(label)
                matrix[(label, col)] = 'ok' if passed else 'FAIL' + (' (%s)' % detail if detail else '')
                ok = ok and passed
    if columns:
        rows = [[label] + [matrix.get((label, c), '-') for c in columns] for label in order]
        print()
        print(table(rows, ['check'] + columns))
    for res in all_results:
        for level, r in res.get('levels', {}).items():
            if r.get('build_failure'):
                print('\n%s %s: the stripped probe failed to build its context:\n  %s'
                      % (res['editor'], level, r['build_failure'][:600]))
            if r.get('warnings'):
                print('\n%s %s: trim warnings mentioning OpenUGD (full log %s):'
                      % (res['editor'], level, r['log']))
                for w in r['warnings']:
                    print('  ' + w)
            if r.get('il5999'):
                print('\n%s %s: %d IL5999 "unhandled reflection" notes on OpenUGD code (informational)'
                      % (res['editor'], level, r['il5999']))
    with open(os.path.join(out, 'linker-gate-report.json'), 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    print('\nLINKER GATE: %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


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
