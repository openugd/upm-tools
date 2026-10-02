"""Packages and assembly definitions, read the way Unity reads them."""
import os
import re

from common import load_json

_GUID_RE = re.compile(r'^guid:\s*([0-9a-f]{32})\s*$', re.M)


def unity_ignores(name):
    """Unity's hidden-asset rule: dot-names, names ending in '~', 'cvs' and '*.tmp' are never imported."""
    low = name.lower()
    return name.startswith('.') or name.endswith('~') or low == 'cvs' or low.endswith('.tmp')


def read_guid(meta_path):
    try:
        with open(meta_path, encoding='utf-8', errors='replace') as f:
            m = _GUID_RE.search(f.read())
        return m.group(1) if m else None
    except OSError:
        return None


class Package(object):
    def __init__(self, root, origin):
        self.root = root
        self.origin = origin              # 'family' or 'unity' or 'tools'
        self.json = load_json(os.path.join(root, 'package.json')) if origin != 'tools' else {}
        self.name = self.json.get('name', os.path.basename(root))
        self.version = self.json.get('version', '0.0.0')
        self.dependencies = self.json.get('dependencies') or {}
        self.repo = os.path.basename(root)
        self.asmdefs = []

    def __repr__(self):
        return 'Package(%s)' % self.name


class Asmdef(object):
    def __init__(self, path, package, kind=None):
        self.path = path
        self.dir = os.path.dirname(path)
        self.package = package
        self.data = load_json(path)
        self.name = self.data['name']
        self.guid = read_guid(path + '.meta')
        self.kind = kind or self._classify()
        self.sources = []

    def _classify(self):
        rel = os.path.relpath(self.dir, self.package.root).split(os.sep)
        if 'Samples~' in rel or 'Samples' == rel[0]:
            return 'sample'
        refs = ' '.join(self.data.get('references') or [])
        if ('TestRunner' in refs or 'UNITY_INCLUDE_TESTS' in (self.data.get('defineConstraints') or [])
                or 'nunit.framework.dll' in (self.data.get('precompiledReferences') or [])):
            return 'test'
        if self.editor_only:
            return 'editor'
        return 'runtime'

    @property
    def editor_only(self):
        return (self.data.get('includePlatforms') or []) == ['Editor']

    def compiles_for(self, variant):
        inc = self.data.get('includePlatforms') or []
        exc = self.data.get('excludePlatforms') or []
        if variant == 'editor':
            return (not inc or 'Editor' in inc) and 'Editor' not in exc
        return not (inc and all(p == 'Editor' for p in inc))

    @property
    def references(self):
        return self.data.get('references') or []

    @property
    def no_engine(self):
        return bool(self.data.get('noEngineReferences'))

    def __repr__(self):
        return 'Asmdef(%s)' % self.name


def _walk_asmdefs(root, include_samples):
    found = []
    for d, dirs, files in os.walk(root):
        keep = []
        for x in dirs:
            if x.startswith('.'):
                continue
            if x.endswith('~') and not (include_samples and x == 'Samples~' and d == root):
                continue
            keep.append(x)
        dirs[:] = sorted(keep)
        found += [os.path.join(d, f) for f in sorted(files) if f.endswith('.asmdef')]
    return found


def assign_sources(asmdefs):
    """Each .cs file belongs to the asmdef in its nearest ancestor folder; Unity skips hidden folders and
    any folder ending in '~' below the asmdef's own folder."""
    owners = {a.dir: a for a in asmdefs}
    for a in asmdefs:
        out = []
        for d, dirs, files in os.walk(a.dir):
            dirs[:] = sorted(x for x in dirs if not unity_ignores(x) and os.path.join(d, x) not in owners)
            out += [os.path.join(d, f) for f in sorted(files) if f.endswith('.cs') and not unity_ignores(f)]
        a.sources = out


def orphan_sources(package, asmdefs):
    """.cs files under a package that no asmdef owns. In an immutable package Unity compiles nothing for
    them (they would land in Assembly-CSharp only when copied into Assets/)."""
    owned = {s for a in asmdefs for s in a.sources}
    out = []
    for d, dirs, files in os.walk(package.root):
        dirs[:] = sorted(x for x in dirs if not x.startswith('.') and (not x.endswith('~') or x == 'Samples~'))
        out += [os.path.join(d, f) for f in files if f.endswith('.cs') and os.path.join(d, f) not in owned]
    return out


def load_family(root, repos):
    pkgs = []
    for repo in repos:
        path = os.path.join(root, repo)
        if not os.path.isfile(os.path.join(path, 'package.json')):
            raise SystemExit('no package.json in %s' % path)
        p = Package(path, 'family')
        p.asmdefs = [Asmdef(f, p) for f in _walk_asmdefs(path, include_samples=True)]
        assign_sources(p.asmdefs)
        pkgs.append(p)
    return pkgs


def load_unity_package(path):
    p = Package(path, 'unity')
    files = []
    for sub in ('Runtime', 'Editor'):
        if os.path.isdir(os.path.join(path, sub)):
            files += _walk_asmdefs(os.path.join(path, sub), include_samples=False)
    p.asmdefs = [Asmdef(f, p, kind='unity') for f in files]
    assign_sources(p.asmdefs)
    return p


def load_canaries(path):
    """The tools' own canary asmdefs (canary/<case>/*.asmdef); see canary/README in the repo README."""
    p = Package(path, 'tools')
    p.name = 'upm-tools.canary'
    p.asmdefs = [Asmdef(f, p, kind='canary') for f in _walk_asmdefs(path, include_samples=False)]
    assign_sources(p.asmdefs)
    return p
