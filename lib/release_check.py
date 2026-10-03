#!/usr/bin/env python3
"""Release check: is each package's HEAD ready to be tagged as the planned version? Tags nothing and changes no
repository (--json writes only the report file).

Per package, reading the committed tree of HEAD (what a tag would publish):
  version      package.json "version" equals the planned version (config/family.json "release.version",
               or --version). The tag is the version itself, as the existing tags are ("1.2.0", "0.6.1"), so a
               mismatch means the tag would not equal package.json "version": OpenUPM would publish the wrong
               version or none, and the check refuses.
  tag          no tag of that name exists yet, or it already points at HEAD.
  changelog    the first "## " heading of CHANGELOG.md is "## [Unreleased]" or "## [<version>]", and the file
               does not have both (one release, one section).
  readme       the README's Install section pins the version in all three forms: `openupm add <name>@<version>`,
               a scoped-registry entry "<name>": "<version>", and a git URL "<repository>.git#<version>"; the git
               form also lists every family package the package needs, transitively, each pinned to a tag of the
               same major at or above the declared minimum (git URLs do not resolve OpenUPM dependencies).
  family deps  every com.openugd.* dependency is declared at a minimum of the planned major (2.x) that does not
               exceed that package's own version under --root.
  unity        package.json "unity" is the family minimum (config/family.json "release.unity").
  samples      every package.json "samples" path exists in HEAD.
  meta         every file and folder in HEAD that Unity imports (not a dot-name, not inside a "~" folder) has a
               tracked .meta, and no tracked .meta lacks its asset.
  clean        the worktree has no staged, unstaged or untracked changes.

Then it prints the commands that would tag each package's HEAD, in publication order (dependencies first), and
the push commands. It never runs them.
Exit status: 0 every package is ready, 1 at least one is not (the commands are then marked as refused or
blocked), 2 the tools could not run.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import meta as M  # noqa: E402
import versions  # noqa: E402
from common import family_config, family_root, git, selected_repos, table  # noqa: E402

FAMILY_SCOPE = 'com.openugd.'
HEADING_RE = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
FENCE_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})')
INSTALL_TITLE_RE = re.compile(r'^`?install(?:ation|ing)?\b', re.I)
CHANGELOG_H2_RE = re.compile(r'^##\s+\[?([^\]\s]+)\]?')


def parse_args(argv):
    cfg = family_config().get('release', {})
    ap = argparse.ArgumentParser(prog='release-check.sh', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--packages', help='comma-separated repo folders (default: the family packages in '
                                       'config/family.json)')
    ap.add_argument('--version', default=cfg.get('version'),
                    help='the version every selected package is to be tagged as (default: config/family.json '
                         '"release.version", %s)' % cfg.get('version'))
    ap.add_argument('--unity', default=cfg.get('unity'),
                    help='the "unity" minimum every package must declare (default: %s)' % cfg.get('unity'))
    ap.add_argument('--root', help='folder holding one checkout per package repo (default: $OPENUGD_ROOT or '
                                   'config/family.json "root")')
    ap.add_argument('--json', help='also write the findings to this file')
    a = ap.parse_args(argv)
    if not a.version:
        ap.error('no planned version: pass --version or set "release.version" in config/family.json')
    try:
        versions.parse_semver(a.version)
    except ValueError as e:
        ap.error(str(e))
    return a


# --- reading HEAD --------------------------------------------------------------------------------------------
def show(repo, path):
    code, out = git(repo, 'show', 'HEAD:' + path)
    return out if code == 0 else None


def head_tree(repo):
    code, out = git(repo, 'ls-tree', '-r', '--name-only', 'HEAD')
    if code != 0:
        raise SystemExit('cannot read HEAD of %s: %s' % (repo, out.strip()))
    return [l for l in out.splitlines() if l]


def major(v):
    return versions.parse_semver(v)[0][0]


def norm_repo_url(url):
    """'https://github.com/openugd/upm-lifetime.git' and 'git+https://...' -> 'github.com/openugd/upm-lifetime'."""
    u = (url or '').strip().split('#', 1)[0]
    u = re.sub(r'^git\+', '', u)
    u = re.sub(r'^[a-z]+://', '', u)
    u = re.sub(r'^git@([^:]+):', r'\1/', u)
    u = re.sub(r'\.git$', '', u.rstrip('/'))
    return u.lower()


# --- README --------------------------------------------------------------------------------------------------
def install_section(text):
    """(first line number, text) of the README section whose heading starts with "Install", up to the next
    heading of the same or a higher level; headings inside code fences do not count."""
    lines = text.splitlines()
    fence = None
    start = level = None
    for i, line in enumerate(lines):
        m = FENCE_RE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not line.strip()[len(marker):].strip():
                fence = None
            continue
        if fence is not None:
            continue
        h = HEADING_RE.match(line)
        if not h:
            continue
        if start is None:
            if INSTALL_TITLE_RE.match(h.group(2)):
                start, level = i, len(h.group(1))
        elif len(h.group(1)) <= level:
            return start + 1, '\n'.join(lines[start:i])
    return (start + 1, '\n'.join(lines[start:])) if start is not None else (None, None)


def manifest_entries(text, name):
    """Every "name": "value" pair for `name` in the text."""
    return re.findall(r'"%s"\s*:\s*"([^"]*)"' % re.escape(name), text)


def is_git_url(value):
    return '://' in value or value.startswith('git@') or value.startswith('git+') or '.git' in value


def check_readme(readme, pkg, version, family, closure):
    """Findings for the README's install snippets."""
    name = pkg['name']
    if readme is None:
        return ['no README.md in HEAD']
    line, sec = install_section(readme)
    if sec is None:
        return ['no "Install" section (a heading starting with "Install")']
    out = []
    where = 'README.md:%d' % line
    # openupm-cli
    adds = re.findall(r'openupm\s+add\s+([^\n`]*)', sec)
    tokens = [t for a in adds for t in a.split() if t.split('@', 1)[0] == name]
    if not tokens:
        out.append('%s: no `openupm add %s@%s`' % (where, name, version))
    for t in tokens:
        pinned = t.split('@', 1)[1] if '@' in t else None
        if pinned != version:
            out.append('%s: `openupm add %s` %s' % (where, t, 'does not pin a version' if pinned is None
                                                     else 'pins %s, not %s' % (pinned, version)))
    entries = manifest_entries(sec, name)
    registry = [v for v in entries if not is_git_url(v)]
    gits = [v for v in entries if is_git_url(v)]
    if not registry:
        out.append('%s: no scoped-registry entry "%s": "%s"' % (where, name, version))
    for v in registry:
        if v != version:
            out.append('%s: scoped-registry entry "%s": "%s" is not %s' % (where, name, v, version))
    own_url = norm_repo_url((pkg.get('repository') or {}).get('url'))
    if not gits:
        out.append('%s: no git URL entry "%s": "<repository>.git#%s"' % (where, name, version))
    for v in gits:
        tag = v.split('#', 1)[1] if '#' in v else None
        if tag != version:
            out.append('%s: git URL "%s" %s' % (where, v, 'is not pinned (no #%s)' % version if tag is None
                                                 else 'pins #%s, not #%s' % (tag, version)))
        if own_url and norm_repo_url(v) != own_url:
            out.append('%s: git URL "%s" is not package.json "repository" (%s)' % (where, v, own_url))
    # The git form must list the family packages it needs: git URLs do not resolve OpenUPM dependencies.
    if gits:
        for dep, minimum in sorted(closure.items()):
            info = family.get(dep)
            dep_gits = [v for v in manifest_entries(sec, dep) if is_git_url(v)]
            if not dep_gits:
                out.append('%s: the git URL install does not list %s (needed; git URLs do not resolve OpenUPM '
                           'dependencies)' % (where, dep))
                continue
            for v in dep_gits:
                tag = v.split('#', 1)[1] if '#' in v else None
                if tag is None:
                    out.append('%s: git URL for %s is not pinned: "%s"' % (where, dep, v))
                    continue
                try:
                    good = major(tag) == major(version) and versions.parse_semver(tag) >= \
                        versions.parse_semver(minimum)
                except ValueError:
                    good = False
                if not good:
                    out.append('%s: git URL for %s pins #%s; needs a %d.x tag at or above %s' % (
                        where, dep, tag, major(version), minimum))
                if info and info.get('url') and norm_repo_url(v) != info['url']:
                    out.append('%s: git URL for %s is not its repository (%s): "%s"' % (where, dep, info['url'], v))
    return out


# --- CHANGELOG -----------------------------------------------------------------------------------------------
def check_changelog(text, version):
    if text is None:
        return ['no CHANGELOG.md in HEAD'], None
    fence = None
    heads = []
    for line in text.splitlines():
        m = FENCE_RE.match(line)
        if m:
            fence = None if fence else m.group(1)
            continue
        if fence is None:
            h = CHANGELOG_H2_RE.match(line)
            if h and line.startswith('## '):
                heads.append((h.group(1), line.strip()))
    if not heads:
        return ['no "## " release heading'], None
    labels = [h[0].lower() for h in heads]
    first = heads[0]
    out = []
    if first[0].lower() not in ('unreleased', version.lower()):
        out.append('first release heading is "%s", not "## [Unreleased]" or "## [%s]"' % (first[1], version))
    if 'unreleased' in labels and version.lower() in labels:
        out.append('has both "## [Unreleased]" and "## [%s]": one release, one section' % version)
    return out, first[1]


# --- package -------------------------------------------------------------------------------------------------
def load_family(root):
    """{package name: {repo, version, url, deps}} for every configured family repo present under root (HEAD)."""
    fam = {}
    for p in family_config()['packages']:
        repo = os.path.join(root, p['repo'])
        if not os.path.isdir(repo):
            continue
        text = show(repo, 'package.json')
        if text is None:
            continue
        pj = json.loads(text)
        fam[pj['name']] = {'repo': p['repo'], 'version': pj.get('version'), 'deps': pj.get('dependencies') or {},
                           'url': norm_repo_url((pj.get('repository') or {}).get('url'))}
    return fam


def family_closure(name, family, deps):
    """{family dependency: highest declared minimum} over the transitive family dependencies."""
    out = {}
    todo = [(d, v) for d, v in deps.items() if d.startswith(FAMILY_SCOPE)]
    while todo:
        d, v = todo.pop()
        try:
            higher = d not in out or versions.parse_semver(v) > versions.parse_semver(out[d])
        except ValueError:
            higher = d not in out
        if higher:
            out[d] = v
            todo += [(dd, vv) for dd, vv in (family.get(d, {}).get('deps') or {}).items()
                     if dd.startswith(FAMILY_SCOPE)]
    out.pop(name, None)
    return out


def check_package(repo, a, family):
    r = {'repo': os.path.basename(repo), 'path': repo, 'checks': {}, 'notes': []}

    def put(label, problems):
        r['checks'][label] = list(problems)

    code, sha = git(repo, 'rev-parse', 'HEAD')
    if code != 0:
        raise SystemExit('%s is not a git checkout' % repo)
    r['sha'] = sha.strip()
    r['branch'] = git(repo, 'branch', '--show-current')[1].strip() or '(detached)'
    text = show(repo, 'package.json')
    if text is None:
        raise SystemExit('%s: no package.json in HEAD' % repo)
    pj = json.loads(text)
    r['name'], r['package_version'] = pj['name'], pj.get('version')
    r['tag'] = a.version

    put('version', [] if pj.get('version') == a.version else
        ['package.json version is %s; the tag would be %s' % (pj.get('version'), a.version)])

    code, tagged = git(repo, 'rev-parse', '-q', '--verify', 'refs/tags/%s^{commit}' % a.version)
    tagged = tagged.strip() if code == 0 else None
    r['tagged_at_head'] = tagged == r['sha']
    put('tag', [] if tagged in (None, r['sha']) else
        ['tag %s already exists at %s (not HEAD); a published version cannot change' % (a.version, tagged[:10])])
    if r['tagged_at_head']:
        r['notes'].append('tag %s already points at HEAD' % a.version)

    problems, heading = check_changelog(show(repo, 'CHANGELOG.md'), a.version)
    put('changelog', problems)
    if heading and heading.lower().startswith('## [unreleased'):
        r['notes'].append('CHANGELOG: the heading is "## [Unreleased]"; rename it "## [%s] - <date>" in a release '
                          'commit and run release-check again (the tag command below names the current HEAD)'
                          % a.version)

    closure = family_closure(pj['name'], family, pj.get('dependencies') or {})
    put('readme', check_readme(show(repo, 'README.md'), pj, a.version, family, closure))

    deps = []
    for dep, minimum in sorted((pj.get('dependencies') or {}).items()):
        if not dep.startswith(FAMILY_SCOPE):
            continue
        try:
            if major(minimum) != major(a.version):
                deps.append('%s is declared at %s, not a %d.x minimum' % (dep, minimum, major(a.version)))
                continue
        except ValueError:
            deps.append('%s is declared at "%s", which is not a version' % (dep, minimum))
            continue
        have = family.get(dep, {}).get('version')
        if have is None:
            r['notes'].append('%s is not under --root; its version was not compared' % dep)
        elif versions.parse_semver(minimum) > versions.parse_semver(have):
            deps.append('%s is declared at %s, above its version under --root (%s): it would not resolve'
                        % (dep, minimum, have))
    put('family deps', deps)

    put('unity', [] if not a.unity or pj.get('unity') == a.unity else
        ['package.json "unity" is %s, not %s' % (pj.get('unity'), a.unity)])

    tree = head_tree(repo)
    tree_set = set(tree)
    samples = []
    for s in pj.get('samples') or []:
        path = (s.get('path') or '').strip('/')
        if not any(f.startswith(path + '/') for f in tree):
            samples.append('sample "%s": %s is not in HEAD' % (s.get('displayName', path), path or '(no path)'))
    put('samples', samples)

    put('meta', ['%s %s' % (label, path) for label, path in
                 M._check_set(tree_set, 'MISSING-IN-HEAD', 'ORPHAN-IN-HEAD', repo)])

    code, status = git(repo, 'status', '--porcelain', '--untracked-files=normal')
    dirty = [l for l in status.splitlines() if l.strip()]
    put('clean', ['%d uncommitted change(s): %s' % (len(dirty), ', '.join(l[3:] for l in dirty[:6]) +
                                                    (' ...' if len(dirty) > 6 else ''))] if dirty else [])

    code, remote = git(repo, 'remote', 'get-url', 'origin')
    r['remote'] = remote.strip() if code == 0 else None
    r['deps'] = [d for d in (pj.get('dependencies') or {}) if d.startswith(FAMILY_SCOPE)]
    r['ok'] = not any(r['checks'].values())
    return r


def layers(results):
    """Publication order: a package goes one layer after the last of its family dependencies. A package with no
    family dependencies and no dependents among the selection is an independent leaf."""
    by_name = {r['name']: r for r in results}
    depth = {}

    def d(name, seen=()):
        if name in depth:
            return depth[name]
        if name in seen:
            raise SystemExit('family dependency cycle through %s' % name)
        deps = [x for x in by_name[name]['deps'] if x in by_name]
        depth[name] = 0 if not deps else 1 + max(d(x, seen + (name,)) for x in deps)
        return depth[name]

    for n in by_name:
        d(n)
    dependents = {n for r in results for n in r['deps']}
    leaves = [r for r in results if depth[r['name']] == 0 and not r['deps'] and r['name'] not in dependents
              and len(results) > 1]
    out = {}
    for r in results:
        if r in leaves:
            continue
        out.setdefault(depth[r['name']], []).append(r)
    return [out[k] for k in sorted(out)], leaves


def mark_ready(results):
    """'ready': no findings of its own and every selected family dependency ready, transitively. A package whose
    dependency's dependency cannot be tagged cannot be tagged either."""
    by_name = {r['name']: r for r in results}

    def ready(r, seen=()):
        if 'ready' not in r:
            if r['name'] in seen:
                raise SystemExit('family dependency cycle through %s' % r['name'])
            r['ready'] = r['ok'] and all(ready(by_name[d], seen + (r['name'],)) for d in r['deps'] if d in by_name)
        return r['ready']

    for r in results:
        ready(r)


def print_commands(results, version):
    print('\nTag commands (not run). Each tags the commit checked above; after finish.sh and the merge into the '
          'default branch it is the same commit when both are fast-forwards.')
    ordered, leaves = layers(results)
    by_name = {r['name']: r for r in results}
    groups = [('layer %d' % (i + 1), g) for i, g in enumerate(ordered)] + \
             ([('independent (no family dependencies or dependents)', leaves)] if leaves else [])
    for title, group in groups:
        print('\n# %s' % title)
        for r in group:
            if r['checks']['version']:
                print('# REFUSED %s: the tag %s would not equal package.json version %s'
                      % (r['repo'], version, r['package_version']))
                continue
            if r['checks']['tag']:
                print('# REFUSED %s: %s' % (r['repo'], r['checks']['tag'][0]))
                continue
            blocked = [k for k, v in r['checks'].items() if v]
            blocked += ['dependency %s not ready' % d for d in r['deps'] if d in by_name and not by_name[d]['ready']]
            prefix = '# BLOCKED (%s) ' % ', '.join(blocked) if blocked else ''
            if r['tagged_at_head']:
                print('%s# %s: tag %s already at HEAD' % (prefix, r['repo'], version))
            else:
                print('%sgit -C %s tag -a %s -m "%s %s" %s' % (prefix, r['path'], version, r['name'], version,
                                                              r['sha']))
            if r['remote']:
                print('%sgit -C %s push origin %s' % (prefix, r['path'], version))
            else:
                print('# %s has no remote "origin": create the repository first, then push the tag' % r['repo'])
        if title.startswith('layer') and group is not ordered[-1]:
            print('# before the next layer, wait until each of these lists %s:' % version)
            for r in group:
                print('#   https://package.openupm.com/%s' % r['name'])


def main(argv):
    a = parse_args(argv)
    root = family_root(a.root)
    repos = selected_repos(a.packages)
    family = load_family(root)
    print('RELEASE CHECK  version %s  root %s  (nothing is tagged)' % (a.version, root))
    results = [check_package(os.path.join(root, repo), a, family) for repo in repos]
    mark_ready(results)
    labels = list(results[0]['checks']) if results else []
    rows = []
    for label in labels:
        rows.append([label] + [('ok' if not r['checks'][label] else 'FAIL (%d)' % len(r['checks'][label]))
                               for r in results])
    print()
    print(table([['commit'] + [r['sha'][:10] for r in results], ['branch'] + [r['branch'] for r in results]] + rows,
                ['check'] + [r['repo'] for r in results]))
    for r in results:
        problems = [(k, p) for k, v in r['checks'].items() for p in v]
        if not problems and not r['notes']:
            continue
        print('\n%s (%s %s):' % (r['repo'], r['name'], r['package_version']))
        for k, p in problems:
            print('  FAIL  %-11s %s' % (k, p))
        for n in r['notes']:
            print('  note  %s' % n)
    print_commands(results, a.version)
    if a.json:
        with open(a.json, 'w') as f:
            json.dump(results, f, indent=2)
    ok = all(r['ok'] for r in results)
    print('\nRELEASE CHECK: %s' % ('READY' if ok else 'NOT READY (%d of %d package(s) have findings)' % (
        sum(1 for r in results if not r['ok']), len(results))))
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
