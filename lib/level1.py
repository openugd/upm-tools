#!/usr/bin/env python3
"""Level 1: the per-commit gate for the OpenUGD package family. Needs the .NET SDK and an installed Unity
editor (only its DLLs and the package sources it ships are read; no licence, no editor process).

Steps (all run by default; --steps picks a subset):
  build    compile every runtime, editor and test asmdef of the selected packages, editor and player
           variants, with exactly the references Unity gives; CS1591, CS1570, CS1572, CS1573, CS1574,
           CS1580, CS1584 and CS1734 are errors in runtime/editor assemblies, other warnings are reported
  samples  compile every Samples~ asmdef with its declared references only
  canary   the tools' canary asmdefs: one must fail (references are not transitive), two must pass
  readme   compile each C# fence that declares a type in each package README.md
  tests    dotnet test every test asmdef with --filter "TestCategory!=RequiresUnity"; the passed count
           must not fall below the per-suite floor in config/test-floors.json
  meta     .meta coverage of each package root against the working tree and git (see lib/meta.py)
  deps     every family or Unity package an asmdef references must be declared in package.json, and every
           engine module (com.unity.modules.*) or Unity package assembly a compiled assembly references in its
           metadata must be guaranteed by package.json, directly or through a declared Unity package

Exit status: 0 all steps passed, 1 the gate failed, 2 the tools could not run.
"""
import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asmdefs as A          # noqa: E402
import build as B            # noqa: E402
import meta as M             # noqa: E402
import modules as MOD        # noqa: E402
import readme as R           # noqa: E402
from common import CONFIG, TOOLS, family_config, family_root, load_json, run, selected_repos, table  # noqa: E402
from projects import DOC_CODES, Graph, write_project  # noqa: E402
from unity import DEFAULT_EDITOR, UnityInstall  # noqa: E402

STEPS = ('build', 'samples', 'canary', 'readme', 'tests', 'meta', 'deps')


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='level1.sh', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', help='folder holding one checkout per package repo (default: $OPENUGD_ROOT or '
                                   'config/family.json "root")')
    ap.add_argument('--packages', help='comma-separated repo folders to check (default: the six family '
                                       'packages); dependencies are added automatically')
    ap.add_argument('--out', help='build output folder (default: $OPENUGD_HARNESS_OUT, else out/ in the upm-tools '
                                  'folder)')
    ap.add_argument('--unity', default=os.environ.get('UNITY_EDITOR', DEFAULT_EDITOR),
                    help='editor version under $OPENUGD_UNITY_EDITORS (default /Applications/Unity/Hub/Editor), or a '
                         'path (default: %(default)s)')
    ap.add_argument('--unity-version', help='override the editor version read from the install')
    ap.add_argument('--package-map', help='package-version map for versionDefines (default: '
                                          'config/unity/<editor version>.json)')
    ap.add_argument('--package-version', action='append', default=[], metavar='NAME=VERSION',
                    help='override one entry of the package map; an empty VERSION removes the package '
                         '(e.g. com.unity.ugui= simulates a project without uGUI). Repeatable.')
    ap.add_argument('--steps', default=','.join(STEPS), help='comma-separated subset of: %s' % ', '.join(STEPS))
    ap.add_argument('--floors', default=os.path.join(CONFIG, 'test-floors.json'),
                    help='per-suite minimum passed-test counts (default: %(default)s)')
    ap.add_argument('--raise-floors', action='store_true',
                    help='after a green test run, raise each floor in the floors file to the passed count')
    ap.add_argument('--jobs', type=int, default=max(2, (os.cpu_count() or 4)), help='parallel builds')
    ap.add_argument('--details', type=int, default=400, help='max diagnostic lines to print (default 400)')
    # Accepted and ignored, for older callers; Unity package assemblies now come from the editor itself, so there
    # is nothing to point at.
    ap.add_argument('--script-assemblies', help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    a.steps = [s.strip() for s in a.steps.split(',') if s.strip()]
    bad = [s for s in a.steps if s not in STEPS]
    if bad:
        ap.error('unknown step(s): %s' % ', '.join(bad))
    return a


def resolve_packages(root, repos):
    """Add the family dependency closure of the selected repos (from package.json)."""
    by_name = {}
    for p in family_config()['packages']:
        pj = os.path.join(root, p['repo'], 'package.json')
        if os.path.exists(pj):
            by_name[load_json(pj)['name']] = p['repo']
    out, added = list(repos), []
    i = 0
    while i < len(out):
        pj = os.path.join(root, out[i], 'package.json')
        if os.path.exists(pj):
            for dep in (load_json(pj).get('dependencies') or {}):
                if dep in by_name and by_name[dep] not in out:
                    out.append(by_name[dep])
                    added.append(by_name[dep])
        i += 1
    order = [p['repo'] for p in family_config()['packages']]
    out.sort(key=lambda r: order.index(r) if r in order else len(order))
    return out, added


def closure(pkg, by_name):
    seen, todo = [], [pkg]
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.append(p)
        todo += [by_name[d] for d in p.dependencies if d in by_name]
    return seen


# --- tests ----------------------------------------------------------------------------------------------
TRX_NS = '{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}'


def run_tests(node, results_dir):
    trx = os.path.join(results_dir, node.name + '.trx')
    if os.path.exists(trx):
        os.remove(trx)
    code, out = run(['dotnet', 'test', node.csproj, '--no-build', '--no-restore', '-nologo',
                     '--filter', 'TestCategory!=RequiresUnity', '--logger', 'trx;LogFileName=%s' % trx,
                     '--results-directory', results_dir], timeout=1800)
    log = os.path.join(results_dir, node.name + '.log')
    with open(log, 'w') as f:
        f.write(out)
    res = {'suite': node.name, 'exit': code, 'log': log, 'passed': 0, 'failed': 0, 'total': 0,
           'skipped': 0, 'failures': []}
    if not os.path.exists(trx):
        res['error'] = 'no TRX written (see %s)' % log
        return res
    root = ET.parse(trx).getroot()
    c = root.find('.//%sCounters' % TRX_NS)
    if c is not None:
        res['total'] = int(c.get('total', 0))
        res['passed'] = int(c.get('passed', 0))
        res['failed'] = int(c.get('failed', 0)) + int(c.get('error', 0)) + int(c.get('timeout', 0)) + \
            int(c.get('aborted', 0))
        res['skipped'] = int(c.get('notExecuted', 0)) + int(c.get('inconclusive', 0))
    for r in root.iter('%sUnitTestResult' % TRX_NS):
        if r.get('outcome') in ('Failed', 'Error', 'Timeout', 'Aborted'):
            msg = r.find('.//%sMessage' % TRX_NS)
            res['failures'].append('%s: %s' % (r.get('testName'), (msg.text or '').strip().splitlines()[0]
                                               if msg is not None and msg.text else ''))
    return res


# --- package.json dependency declarations ------------------------------------------------------------
def check_dependencies(family, graph, root):
    """An asmdef reference to an assembly from another package needs that package in package.json, except
    from tests (their references arrive through 'testables') and from optional assemblies guarded by
    defineConstraints (they exist precisely so the package need not depend on it)."""
    owner = {}   # assembly name -> package name, over every configured family repo under root
    for cfg in family_config()['packages']:
        path = os.path.join(root, cfg['repo'])
        if os.path.isfile(os.path.join(path, 'package.json')):
            pkg = A.Package(path, 'family')
            for f in A._walk_asmdefs(path, include_samples=True):
                owner[load_json(f)['name']] = pkg.name
    for name, asm in graph.asm.items():
        owner[name] = asm.package.name if asm.package.origin != 'tools' else None
    problems = []
    for p in family:
        for a in p.asmdefs:
            if a.kind == 'test' or a.data.get('defineConstraints'):
                continue
            for raw in a.references:
                name = graph.guid.get(raw[5:]) if raw.startswith('GUID:') else raw
                dep = owner.get(name)
                if dep is None or dep == p.name:
                    continue
                if dep not in p.dependencies:
                    problems.append((p.repo, a.name, name, dep))
    return problems


def family_names(root, family):
    names = {p.name for p in family}
    for cfg in family_config()['packages']:
        pj = os.path.join(root, cfg['repo'], 'package.json')
        if os.path.isfile(pj):
            names.add(load_json(pj)['name'])
    return names


def check_modules(a, root, out, unity, g, family, selected, want, dep_problems, infra):
    """Engine modules and Unity package assemblies the compiled assemblies reference in their metadata
    (lib/modules.py). Returns a dict for the report, or None when the editor data or the reader is missing
    (recorded in ``infra``, which fails the gate)."""
    try:
        mm = MOD.ModuleMap.from_editor(unity)
        infra.extend(mm.problems)
        tool = MOD.build_asmrefs(out)
    except MOD.ModuleDataError as e:
        infra.append('deps: %s' % e)
        return None
    candidates = [n for n in want if n.kind in ('runtime', 'editor', 'sample') and n.package in selected]
    built = [n for n in candidates if n.built and os.path.exists(n.dll)]
    try:
        refs = MOD.read_refs(tool, [n.dll for n in built])
    except MOD.ModuleDataError as e:
        infra.append('deps: %s' % e)
        return None
    judged, unreadable = [], []
    for n in built:
        data = refs.get(n.dll) or {'error': 'no output for this file'}
        if data.get('error'):
            unreadable.append((n, data['error']))
        else:
            judged.append((n.package, n.asmdef, n.variant, data.get('references') or {}))
    # An assembly that did not compile cannot be judged. The build or samples step fails for it already when
    # it runs; when it does not run, the deps step must not pass on assemblies it never saw.
    unjudged = []
    for n in candidates:
        if n in built:
            continue
        owner_step = 'samples' if n.kind == 'sample' else 'build'
        unjudged.append((n, owner_step in a.steps))
    package_assemblies = {asm.name: p.name for p in g.unity_packages for asm in p.asmdefs}
    reported = {(repo, asm, ref) for repo, asm, ref, _dep in dep_problems}
    findings, usage, notes = MOD.check(selected, family, judged, mm, package_assemblies,
                                       family_names(root, family), reported)
    return {'map': mm, 'findings': findings, 'usage': usage, 'notes': notes, 'unreadable': unreadable,
            'unjudged': unjudged, 'judged': len(judged)}


# --- reporting ------------------------------------------------------------------------------------------
def rel(path, root):
    try:
        r = os.path.relpath(path, root)
        return path if r.startswith('..') else r
    except ValueError:
        return path


def node_counts(n):
    doc = sum(1 for d in n.errors + n.warnings if d.code in DOC_CODES)
    errs = sum(1 for d in n.errors if d.code not in DOC_CODES)
    warns = sum(1 for d in n.warnings if d.code not in DOC_CODES)
    return errs, doc, warns


def main(argv):
    a = parse_args(argv)
    t0 = time.time()
    root = family_root(a.root)
    out = os.path.abspath(a.out or os.environ.get('OPENUGD_HARNESS_OUT') or os.path.join(TOOLS, 'out'))
    unity = UnityInstall(a.unity, a.unity_version)
    map_path = a.package_map or os.path.join(CONFIG, 'unity', unity.version + '.json')
    if not os.path.exists(map_path):
        raise SystemExit('no package map for Unity %s at %s; pass --package-map (config/unity/*.json are '
                         'the maintained ones)' % (unity.version, map_path))
    unity_cfg = load_json(map_path)
    overrides = {}
    for kv in a.package_version:
        if '=' not in kv:
            raise SystemExit('--package-version expects NAME=VERSION, got %r' % kv)
        k, v = kv.split('=', 1)
        overrides[k.strip()] = v.strip()
    repos, added = resolve_packages(root, selected_repos(a.packages))
    family = A.load_family(root, repos)
    by_name = {p.name: p for p in family}
    selected = [p for p in family if p.repo not in added]

    os.makedirs(out, exist_ok=True)
    nunit = unity.nunit_framework(os.path.join(out, '_unity'))
    unity_pkgs = []
    for name in unity_cfg.get('sourcePackages', []):
        d = unity.builtin_package_dir(name)
        if d:
            unity_pkgs.append(A.load_unity_package(d))
    canaries = A.load_canaries(os.path.join(TOOLS, 'canary')) if 'canary' in a.steps else None
    g = Graph(unity, unity_cfg, family, unity_pkgs, canaries, out, nunit, overrides)

    print('LEVEL 1  unity %s  root %s' % (unity.version, root))
    print('         packages %s%s' % (', '.join(p.repo for p in selected),
                                      ('  (+ dependencies: %s)' % ', '.join(added)) if added else ''))
    print('         out %s' % out)
    infra = []
    if not nunit:
        infra.append('Unity NUnit (com.unity.ext.nunit) not found in the editor; test asmdefs cannot compile')
    for n in unity_cfg.get('prebuiltAssemblies', []):
        if n not in g.prebuilt:
            infra.append('%s not found in the editor template cache' % n)

    # --- graph ---
    want = []
    testruns = []
    kinds = set()
    if 'build' in a.steps:
        kinds |= {'runtime', 'editor', 'test'}
    if 'samples' in a.steps:
        kinds.add('sample')
    if 'tests' in a.steps:
        kinds.add('test')
    if 'deps' in a.steps:
        # the module check reads the compiled assemblies (tests are exempt, as for asmdef references)
        kinds |= {'runtime', 'editor', 'sample'}
    for p in selected:
        for asm in p.asmdefs:
            if asm.kind not in kinds:
                continue
            for variant in ('editor', 'player'):
                n = g.node_for(asm, variant)
                if n is not None:
                    want.append(n)
            if asm.kind == 'test' and 'tests' in a.steps:
                t = g.add_test_runner(asm)
                if t is not None:
                    testruns.append(t)
    canary_nodes = []
    if canaries:
        for asm in canaries.asmdefs:
            n = g.node_for(asm, 'player')
            if n is not None:
                canary_nodes.append(n)
    snippet_rows = []
    if 'readme' in a.steps:
        for p in selected:
            path = os.path.join(p.root, 'README.md')
            if not os.path.exists(path):
                snippet_rows.append((p, None, 'no README.md', None))
                continue
            with open(path, encoding='utf-8') as f:
                text = f.read()
            assemblies = [x for q in closure(p, by_name) for x in q.asmdefs
                          if x.kind == 'runtime' and x.data.get('autoReferenced', True)]
            for fence, verdict in R.compilable(text):
                node = None
                if verdict == 'compile':
                    name = 'readme.%s.L%d' % (p.repo, fence.line)
                    header = '// %s, code fence at line %d\n#line %d "%s"\n' % (path, fence.line, fence.line + 1,
                                                                              path)
                    node = g.add_snippet('snippet/' + name, name, header + fence.body, p, assemblies)
                snippet_rows.append((p, fence, verdict, node))

    nodes = list(g.nodes.values())
    for n in nodes:
        write_project(n, unity_cfg)
    sln = os.path.join(out, 'all.slnx')
    B.write_solution(sln, nodes)
    code, text = B.restore(sln, os.path.join(out, 'restore.log'))
    if code != 0:
        sys.stdout.write(text)
        print('restore failed; see %s' % os.path.join(out, 'restore.log'))
        return 2
    B.build_graph(nodes, os.path.join(out, 'logs'), a.jobs)

    # canary verdicts
    for n in canary_nodes:
        if n.expect == 'fail':
            if n.status == 'PASS':
                n.status, n.reason = 'FAIL', 'built, but must not: %s' % n.label
            elif n.status == 'FAIL':
                hits = [d for d in n.errors if (not n.expect_codes or d.code in n.expect_codes)
                        and (not n.expect_mentions or n.expect_mentions in d.msg)]
                if hits:
                    n.status, n.reason = 'PASS', 'failed as required: %s %s' % (hits[0].code, hits[0].msg)
                else:
                    n.reason = 'failed, but not with %s mentioning %s' % ('/'.join(n.expect_codes),
                                                                          n.expect_mentions)
            else:
                n.reason = 'could not be judged: ' + n.reason

    # --- tests ---
    test_results = []
    floors = load_json(a.floors) if os.path.exists(a.floors) else {}
    floors = {k: v for k, v in floors.items() if not k.startswith('_')}
    if 'tests' in a.steps:
        rdir = os.path.join(out, 'test-results')
        os.makedirs(rdir, exist_ok=True)
        for t in sorted(testruns, key=lambda x: x.name):
            if t.status != 'PASS':
                test_results.append({'suite': t.name, 'error': 'test project did not build (%s)'
                                     % (t.reason or 'compile errors'), 'passed': 0, 'failed': 0, 'total': 0,
                                     'skipped': 0, 'failures': []})
                continue
            test_results.append(run_tests(t, rdir))
        for r in test_results:
            floor = floors.get(r['suite'])
            r['floor'] = floor
            if r.get('error'):
                r['status'] = 'FAIL'
            elif r['failed']:
                r['status'] = 'FAIL'
            elif floor is None:
                r['status'] = 'FAIL'
                r['error'] = 'no floor configured in %s' % rel(a.floors, TOOLS)
            elif r['passed'] < floor:
                r['status'] = 'FAIL'
                r['error'] = 'passed %d < floor %d' % (r['passed'], floor)
            else:
                r['status'] = 'PASS'
        if a.raise_floors:
            # Floors only move up: a suite with failures, or one that did not run, keeps its floor.
            data = load_json(a.floors) if os.path.exists(a.floors) else {}
            for r in test_results:
                if not r['failed'] and r['total'] and 'did not build' not in (r.get('error') or ''):
                    data[r['suite']] = max(data.get(r['suite'], 0), r['passed'])
            with open(a.floors, 'w') as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.write('\n')
            print('floors written to %s' % a.floors)

    meta_results = M.check_family([p.root for p in selected]) if 'meta' in a.steps else {}
    dep_problems = check_dependencies(selected, g, root) if 'deps' in a.steps else []
    modules = check_modules(a, root, out, unity, g, family, selected, want, dep_problems, infra) \
        if 'deps' in a.steps else None
    return report(a, root, out, unity, g, selected, want, canary_nodes, snippet_rows, test_results,
                  meta_results, dep_problems, modules, infra, time.time() - t0)


def report(a, root, out, unity, g, selected, want, canary_nodes, snippet_rows, test_results, meta_results,
           dep_problems, modules, infra, elapsed):
    summary = []
    lines = []
    P = lambda s='': lines.append(s)

    # assemblies
    fam = [n for n in want if n.kind in ('runtime', 'editor', 'test')]
    smp = [n for n in want if n.kind == 'sample']
    for title, group, step in (('Assemblies', fam, 'build'), ('Samples~', smp, 'samples')):
        if step not in a.steps:
            continue
        rows = []
        for n in sorted(group, key=lambda n: (n.package.repo, n.name, n.variant)):
            e, d, w = node_counts(n)
            rows.append([n.kind, n.name, n.variant, n.status, e, d, w, n.reason if n.status != 'PASS' else ''])
        for asm, variant, reason in g.skipped:
            if asm.package.origin == 'family' and ((step == 'build' and asm.kind != 'sample') or
                                                   (step == 'samples' and asm.kind == 'sample')):
                if asm.package in selected and not (variant == 'player' and reason == 'editor-only'):
                    rows.append([asm.kind, asm.name, variant, 'SKIP', '', '', '', reason])
        P('== %s' % title)
        P(table(rows, ['kind', 'assembly', 'variant', 'status', 'errors', 'doc', 'warnings', 'reason']) if rows
          else '(none)')
        P()
        bad = [n for n in group if n.status != 'PASS']
        summary.append((step, 'FAIL' if bad else 'PASS', '%d project(s), %d failed or blocked'
                        % (len(group), len(bad))))
    # infrastructure (Unity package assemblies the family needs)
    infra_nodes = [n for n in g.nodes.values() if n.kind == 'unity' and n.status != 'PASS']
    for n in infra_nodes:
        infra.append('%s (%s) did not build: %s' % (n.name, n.variant, n.reason or 'see %s' % n.log))

    if 'canary' in a.steps:
        rows = [[n.name, n.expect, n.status, n.reason] for n in sorted(canary_nodes, key=lambda n: n.name)]
        P('== Canary')
        P(table(rows, ['canary', 'must', 'status', 'detail']))
        P()
        bad = [n for n in canary_nodes if n.status != 'PASS']
        summary.append(('canary', 'FAIL' if bad or not canary_nodes else 'PASS',
                        '%d canaries, %d wrong' % (len(canary_nodes), len(bad))))

    if 'readme' in a.steps:
        rows = []
        compiled = failed = 0
        for p, fence, verdict, node in snippet_rows:
            if fence is None:
                rows.append([p.repo, '-', 'SKIP', verdict])
                continue
            if node is None:
                rows.append([p.repo, 'L%d' % fence.line, 'SKIP', verdict])
                continue
            compiled += 1
            e, d, w = node_counts(node)
            if node.status != 'PASS':
                failed += 1
            rows.append([p.repo, 'L%d' % fence.line, node.status,
                         ('%d error(s)' % (e + d)) if node.status == 'FAIL' else (node.reason or
                                                                                  ('%d warning(s)' % w if w else ''))])
        P('== README snippets')
        P(table(rows, ['package', 'fence', 'status', 'detail']) if rows else '(no C# fences)')
        P()
        summary.append(('readme', 'FAIL' if failed else 'PASS', '%d fence(s) compiled, %d failed'
                        % (compiled, failed)))

    if 'tests' in a.steps:
        rows = []
        for r in test_results:
            rows.append([r['suite'], r['passed'], r['failed'], r['skipped'], r.get('floor', ''), r['status'],
                         r.get('error', '')])
        P('== Tests (dotnet test --filter "TestCategory!=RequiresUnity")')
        P(table(rows, ['suite', 'passed', 'failed', 'skipped', 'floor', 'status', 'detail']) if rows else '(none)')
        for r in test_results:
            for f in r['failures'][:20]:
                P('  FAILED %s' % f)
        P()
        bad = [r for r in test_results if r['status'] != 'PASS']
        summary.append(('tests', 'FAIL' if bad else 'PASS', '%d suite(s), %d passed tests, %d failing suite(s)'
                        % (len(test_results), sum(r['passed'] for r in test_results), len(bad))))

    if 'meta' in a.steps:
        P('== .meta')
        total = 0
        for rootp, problems in meta_results.items():
            total += len(problems)
            P('%-22s %s' % (os.path.basename(rootp), 'ok' if not problems else '%d problem(s)' % len(problems)))
            for kind, path in problems:
                P('    %-15s %s' % (kind, path))
        P()
        summary.append(('meta', 'FAIL' if total else 'PASS', '%d problem(s)' % total))

    if 'deps' in a.steps:
        P('== package.json dependencies')
        P('asmdef references:')
        for repo, asm, ref, dep in dep_problems:
            P('  %s: asmdef %s references %s, but package.json does not declare %s' % (repo, asm, ref, dep))
        if not dep_problems:
            P('  ok')
        bad_modules = 0
        if modules is None:
            P('engine modules and Unity package assemblies: not checked (see Tooling problems)')
        else:
            mm = modules['map']
            P('engine modules and Unity package assemblies, from the compiled metadata (%d assembly variants):'
              % modules['judged'])
            P('  Unity %s: %d modules every project has, %d controlled by a com.unity.modules.* package (%s)'
              % (unity.version, len(mm.always_present), len(mm.controlled), rel(mm.source, root)))
            rows = []
            for p in selected:
                used = modules['usage'].get(p.repo) or {}
                cells = ['%s -> %s %s' % (ref, need, how if how else 'NOT DECLARED')
                         for ref, (need, how) in sorted(used.items())]
                rows.append([p.repo, '; '.join(cells) if cells else 'none needed'])
            P('\n'.join('  ' + line for line in table(rows, ['package', 'needs a declaration']).splitlines()))
            for f in modules['findings']:
                P('  UNDECLARED %s: %s' % (f.repo, f.message()))
            for n, err in modules['unreadable']:
                P('  UNREADABLE %s (%s): %s' % (n.name, n.variant, err))
            for n, reported in modules['unjudged']:
                step = 'samples' if n.kind == 'sample' else 'build'
                P('  NOT CHECKED %s (%s): no assembly to read, %s%s%s' % (
                    n.name, n.variant, n.status, ': ' + n.reason if n.reason else '',
                    '; the %s step reports it' % step if reported else ''))
            for note in modules['notes']:
                P('  note: %s' % note)
            bad_modules = len(modules['findings']) + len(modules['unreadable']) + \
                sum(1 for _n, reported in modules['unjudged'] if not reported)
        P()
        if modules is None:
            detail = 'modules not checked'
        else:
            detail = '%d undeclared module/assembly use(s), %d assembly variant(s) not checked' % (
                len(modules['findings']) + len(modules['unreadable']), len(modules['unjudged']))
        summary.append(('deps', 'FAIL' if dep_problems or bad_modules or modules is None else 'PASS',
                        '%d undeclared asmdef reference(s), %s' % (len(dep_problems), detail)))

    # diagnostics, deduplicated across variants
    P('== Diagnostics (errors first; identical diagnostics from both variants shown once)')
    seen = {}
    order = []
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        if n.kind in ('unity', 'testrun'):
            if n.status == 'FAIL':
                for d in n.errors:
                    k = (n.name, d.key())
                    if k not in seen:
                        seen[k] = (d, [n.variant], n)
                        order.append(k)
            continue
        if n.kind == 'canary' and n.expect == 'fail':
            continue
        for d in n.errors + n.warnings:
            k = (n.name, d.key())
            if k in seen:
                seen[k][1].append(n.variant)
            else:
                seen[k] = (d, [n.variant], n)
                order.append(k)
    order.sort(key=lambda k: (seen[k][0].sev != 'error', k[0], seen[k][0].file, seen[k][0].line))
    shown = 0
    for k in order:
        d, variants, n = seen[k]
        if shown >= a.details:
            P('  ... %d more; full logs in %s' % (len(order) - shown, os.path.join(out, 'logs')))
            break
        loc = '%s(%d,%d)' % (rel(d.file, root), d.line, d.col) if d.line else rel(d.file, root)
        P('  %-7s %s %s: %s  [%s %s]' % (d.sev, d.code, loc, d.msg, n.name, ','.join(sorted(set(variants)))))
        shown += 1
    if not order:
        P('  none')
    notes = sorted({(n.name, note) for n in g.nodes.values() if n.kind not in ('unity',) for note in n.notes})
    if notes:
        P()
        P('== Notes')
        for name, note in notes:
            P('  %s: %s' % (name, note))
    P()
    if infra:
        P('== Tooling problems')
        for i in infra:
            P('  ' + i)
        P()
        summary.append(('tooling', 'FAIL', '%d problem(s)' % len(infra)))

    P('== Summary  (%.0f s)' % elapsed)
    P(table([[s, st, d] for s, st, d in summary], ['step', 'status', 'detail']))
    ok = all(st == 'PASS' for _, st, _ in summary)
    P()
    P('LEVEL 1: %s' % ('PASS' if ok else 'FAIL'))
    text = '\n'.join(lines)
    print(text)
    with open(os.path.join(out, 'level1-summary.txt'), 'w') as f:
        f.write(text + '\n')
    report_json = {
        'unity': unity.version, 'root': root, 'ok': ok,
        'summary': [{'step': s, 'status': st, 'detail': d} for s, st, d in summary],
        'projects': [{'id': n.id, 'kind': n.kind, 'status': n.status, 'reason': n.reason, 'log': n.log,
                      'errors': [str(d) for d in n.errors], 'warnings': [str(d) for d in n.warnings]}
                     for n in sorted(g.nodes.values(), key=lambda n: n.id)],
        'tests': test_results,
        'meta': {os.path.basename(k): v for k, v in meta_results.items()},
        'deps': dep_problems,
        'depsModules': None if modules is None else {
            'unity': unity.version,
            'source': modules['map'].source,
            'alwaysPresent': modules['map'].always_present,
            'findings': [f.as_dict() for f in modules['findings']],
            'usage': {repo: {ref: {'needs': need, 'how': how} for ref, (need, how) in used.items()}
                      for repo, used in modules['usage'].items()},
            'unreadable': [{'id': n.id, 'error': err} for n, err in modules['unreadable']],
            'unchecked': [{'id': n.id, 'reportedByStep': reported} for n, reported in modules['unjudged']],
            'notes': modules['notes'],
        },
        'tooling': infra,
    }
    with open(os.path.join(out, 'level1-report.json'), 'w') as f:
        json.dump(report_json, f, indent=2)
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
