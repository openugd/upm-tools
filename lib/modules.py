"""Unity built-in modules and Unity packages a compiled assembly needs, checked against package.json.

Unity compiles every assembly that can end up in a player (anything but an Editor-only asmdef) against the
engine modules the project actually has: the ones Unity cannot disable, plus each module whose
com.unity.modules.* package is installed. Code that uses a type from any other module compiles in a project
that happens to have the module and fails with CS1069 in one that does not. Level 1 compiles against every
module DLL, so it reads what each compiled assembly references in its metadata (asmrefs/, a
System.Reflection.Metadata dumper) and asks whether the package's own package.json guarantees it.

Everything comes from the selected editor install, so the map follows the editor version:

* ``Contents/Resources/modules.asset`` (``PlatformModuleSetup``) lists every engine module with
  ``controlledByBuiltinPackage``. ``0``: no package controls the module and Unity always references it.
  ``1``: the built-in package ``com.unity.modules.<name in lower case>`` controls it; that the package exists
  in ``BuiltInPackages`` is checked, not assumed. A module's assembly is ``UnityEngine.<name>Module``.
* ``Contents/Resources/PackageManager/BuiltInPackages/*/package.json`` and, for packages not built in,
  ``Contents/Resources/PackageManager/Editor/<name>-<version>.tgz`` hold the manifests of the Unity packages
  the editor ships. They give the dependency closure: com.unity.ugui depends on com.unity.modules.ui and
  com.unity.modules.imgui, so a package that declares ugui has both.

Editor modules (``UnityEditor.*Module``) carry no such flag (``editor_modules.asset`` in 6000.0, absent in
6000.3), and Unity references all of them in every editor compile whatever packages are installed, so they
never need a declaration. A use that drags an engine type along shows up as a reference to the engine module.
"""
import glob
import json
import os
import re
import tarfile

from common import load_json, run
from versions import parse_semver

ENGINE_MODULE_RE = re.compile(r'^UnityEngine\.(\w+)Module$')


class ModuleDataError(Exception):
    """The editor install does not hold the module data the check needs."""


def parse_modules_asset(text):
    """[(name, [dependencies], controlled)] from a PlatformModuleSetup YAML file. Only the three keys the
    check needs are read; a module entry without controlledByBuiltinPackage is an error, not a default."""
    modules = []
    cur = None
    in_deps = False
    for line in text.splitlines():
        m = re.match(r'^\s*- name:\s*(\S+)\s*$', line)
        if m:
            if cur is not None:
                modules.append(cur)
            cur = {'name': m.group(1), 'deps': [], 'controlled': None}
            in_deps = False
            continue
        if cur is None:
            continue
        m = re.match(r'^\s*dependencies:\s*(\[\s*\])?\s*$', line)
        if m:
            in_deps = not m.group(1)
            continue
        m = re.match(r'^\s*controlledByBuiltinPackage:\s*([01])\s*$', line)
        if m:
            cur['controlled'] = m.group(1) == '1'
            in_deps = False
            continue
        m = re.match(r'^\s*-\s*(\S+)\s*$', line)
        if m and in_deps:
            cur['deps'].append(m.group(1))
            continue
        if re.match(r'^\s*\w+:', line):
            in_deps = False
    if cur is not None:
        modules.append(cur)
    bad = [m['name'] for m in modules if m['controlled'] is None]
    if bad:
        raise ModuleDataError('modules.asset entries without controlledByBuiltinPackage: %s' % ', '.join(bad))
    return [(m['name'], m['deps'], m['controlled']) for m in modules]


class UnityManifests(object):
    """package.json of every Unity package the editor ships: BuiltInPackages, then the tarballs in
    PackageManager/Editor (read lazily, only for a name the closure actually reaches)."""

    def __init__(self, builtin_dir, tarball_dir):
        self.builtin_dir = builtin_dir
        self.builtin = {}            # name -> (version, dependencies)
        if os.path.isdir(builtin_dir):
            for d in sorted(os.listdir(builtin_dir)):
                pj = os.path.join(builtin_dir, d, 'package.json')
                if os.path.isfile(pj):
                    j = load_json(pj)
                    self.builtin[j.get('name', d)] = (j.get('version', ''), j.get('dependencies') or {})
        self.tarballs = {}           # name -> {version: path}
        for path in sorted(glob.glob(os.path.join(tarball_dir, '*.tgz'))):
            m = re.match(r'^(.+)-(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\.tgz$', os.path.basename(path))
            if m:
                self.tarballs.setdefault(m.group(1), {})[m.group(2)] = path
        self._tar_cache = {}

    def source(self, name, version=None):
        if name in self.builtin:
            return os.path.join(self.builtin_dir, name, 'package.json')
        path = self._tarball(name, version)
        return path + '!package/package.json' if path else None

    def _tarball(self, name, version):
        """The tarball for ``version``, else the newest one the editor ships."""
        found = self.tarballs.get(name)
        if not found:
            return None
        if version in found:
            return found[version]
        return found[max(found, key=parse_semver)]

    def dependencies(self, name, version=None):
        """{dependency: version} of a Unity package, or None when the editor ships no manifest for it."""
        if name in self.builtin:
            return self.builtin[name][1]
        path = self._tarball(name, version)
        if path is None:
            return None
        if path not in self._tar_cache:
            deps = None
            with tarfile.open(path) as t:
                for member in t:
                    if member.name in ('package/package.json', './package/package.json'):
                        with t.extractfile(member) as f:
                            deps = json.loads(f.read().decode('utf-8-sig')).get('dependencies') or {}
                        break
            self._tar_cache[path] = deps
        return self._tar_cache[path]

    def closure(self, roots):
        """Walk Unity package manifests from ``roots`` ({name: version}). Returns ({name: root it came
        through}, [reached names without a manifest]). Breadth first, so 'through' is the shortest route."""
        reached, unknown = {}, []
        todo = [(n, v, n) for n, v in sorted(roots.items())]
        while todo:
            name, version, via = todo.pop(0)
            if name in reached:
                continue
            reached[name] = via
            deps = self.dependencies(name, version)
            if deps is None:
                unknown.append(name)
                continue
            todo += [(d, dv, via) for d, dv in sorted(deps.items())]
        return reached, unknown


class ModuleMap(object):
    """Engine module assembly -> the com.unity.modules.* package that controls it (None: always present)."""

    def __init__(self, modules, manifests, source='modules.asset'):
        self.source = source
        self.manifests = manifests
        self.engine = {}
        self.problems = []
        for name, _deps, controlled in modules:
            pkg = 'com.unity.modules.' + name.lower() if controlled else None
            if pkg and pkg not in manifests.builtin:
                self.problems.append('%s: module %s is controlled by a built-in package, but %s is not in %s'
                                     % (source, name, pkg, manifests.builtin_dir))
            self.engine['UnityEngine.%sModule' % name] = pkg
        if not self.engine:
            raise ModuleDataError('no modules in %s' % source)

    @classmethod
    def from_editor(cls, unity):
        path = os.path.join(unity.contents, 'Resources', 'modules.asset')
        if not os.path.isfile(path):
            raise ModuleDataError('%s not found; cannot tell which modules every project has' % path)
        with open(path, encoding='utf-8') as f:
            modules = parse_modules_asset(f.read())
        manifests = UnityManifests(unity.builtin_packages,
                                   os.path.join(unity.contents, 'Resources', 'PackageManager', 'Editor'))
        mm = cls(modules, manifests, path)
        # Every module DLL the harness compiles against must be described, or a reference to it cannot be judged.
        for dll in unity.engine_refs():
            asm = os.path.splitext(os.path.basename(dll))[0]
            if ENGINE_MODULE_RE.match(asm) and asm not in mm.engine:
                mm.problems.append('%s has no entry in %s' % (os.path.basename(dll), path))
        return mm

    @property
    def always_present(self):
        return sorted(a for a, p in self.engine.items() if p is None)

    @property
    def controlled(self):
        return sorted(a for a, p in self.engine.items() if p is not None)


# --- reading metadata -----------------------------------------------------------------------------------
ASMREFS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'asmrefs')


def build_asmrefs(out):
    """Build asmrefs/ in the output folder, so the repository never gets bin/ or obj/. Returns the DLL."""
    import shutil
    src = os.path.join(out, '_tools', 'asmrefs')
    os.makedirs(src, exist_ok=True)
    for f in ('AsmRefs.csproj', 'Program.cs'):
        shutil.copy(os.path.join(ASMREFS, f), os.path.join(src, f))
    dll = os.path.join(src, 'bin', 'AsmRefs.dll')
    code, text = run(['dotnet', 'build', os.path.join(src, 'AsmRefs.csproj'), '-nologo', '-v', 'q',
                      '-o', os.path.dirname(dll)], timeout=600)
    if code != 0 or not os.path.exists(dll):
        raise ModuleDataError('could not build asmrefs (%s):\n%s' % (src, text))
    return dll


def read_refs(tool, paths):
    """{path: {"name": ..., "references": {assembly: [types]}} or {"error": ...}} for each assembly."""
    if not paths:
        return {}
    code, text = run(['dotnet', tool] + list(paths), timeout=600)
    if code != 0:
        raise ModuleDataError('asmrefs failed (%d):\n%s' % (code, text))
    try:
        return json.loads(text)
    except ValueError:
        raise ModuleDataError('asmrefs printed no JSON:\n%s' % text)


# --- the check ------------------------------------------------------------------------------------------
class Finding(object):
    """One assembly using an assembly its package.json does not guarantee."""
    __slots__ = ('repo', 'package', 'assembly', 'variants', 'reference', 'types', 'needs', 'hint', 'kind')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def message(self):
        uses = ''
        if self.types:
            uses = ' (%s%s)' % (', '.join(self.types[:3]), ', ...' if len(self.types) > 3 else '')
        head = '%s [%s] uses %s%s' % (self.assembly, ', '.join(self.variants), self.reference, uses)
        if self.kind == 'unknown-module':
            return '%s, a module %s does not list; cannot tell whether every project has it' % (head, self.needs)
        what = 'that module exists only with' if self.kind == 'module' else 'that assembly comes from'
        text = '%s; %s %s, which package.json does not declare, directly or through a Unity package' % (
            head, what, self.needs)
        return text + ('; %s' % self.hint if self.hint else '')

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


def coverage(pkg, asmdef, manifests, family_names):
    """Unity packages an assembly can count on: its package.json's non-family dependencies and their Unity
    closure, plus each package whose versionDefines symbol its defineConstraints require (the assembly is not
    compiled without it). Family dependencies are not walked: declare what you use, as for asmdef references.
    Returns (reached {name: the declared package it came through}, guards {package: symbol} for versionDefines
    the constraints do not require, [reached Unity packages without a manifest])."""
    roots = {n: v for n, v in pkg.dependencies.items() if n not in family_names}
    constraints = set(asmdef.data.get('defineConstraints') or [])
    guards = {}
    for vd in asmdef.data.get('versionDefines') or []:
        name, sym = vd.get('name'), vd.get('define')
        if not name or not sym or name == 'Unity' or name in family_names:
            continue
        if sym in constraints:
            roots.setdefault(name, None)
        else:
            guards[name] = sym
    reached, unknown = manifests.closure(roots)
    return reached, guards, unknown


def family_route(pkg, need, by_name, manifests, family_names, seen=None):
    """A family dependency of ``pkg`` through which ``need`` would arrive anyway (for a hint), or None."""
    seen = set() if seen is None else seen
    for dep in sorted(pkg.dependencies):
        if dep not in by_name or dep in seen:
            continue
        seen.add(dep)
        other = by_name[dep]
        reached, _ = manifests.closure({n: v for n, v in other.dependencies.items() if n not in family_names})
        if need in reached or family_route(other, need, by_name, manifests, family_names, seen):
            return dep
    return None


def _variant_order(v):
    return ('editor', 'player').index(v) if v in ('editor', 'player') else 2


def check(selected, family, judged, mm, package_assemblies, family_names, already_reported=()):
    """Judge the metadata references of compiled family assemblies.

    selected            packages whose assemblies are judged
    family              every loaded family package (selected plus dependencies), for hints
    judged              [(package, asmdef, variant, references)] where references is the asmrefs
                        {assembly: [types]} of that compiled variant
    mm                  ModuleMap of the editor
    package_assemblies  {assembly name: Unity package} of the Unity packages the harness compiles (ugui)
    family_names        names of every family package
    already_reported    {(repo, asmdef, assembly)} the asmdef-reference rule reported already

    Returns (findings, usage, notes). usage is {repo: {reference: (needs, how)}} for every reference that needs
    a declaration, with how = 'declared', 'via <package>', 'versionDefine <symbol>' or None (not covered).
    """
    by_name = {p.name: p for p in family}
    findings, notes = [], []
    usage = {p.repo: {} for p in selected}
    per_asm = {}
    for pkg, asmdef, variant, references in judged:
        if pkg not in selected:
            continue
        entry = per_asm.setdefault(asmdef.name, (pkg, asmdef, {}))[2]
        for ref, types in references.items():
            slot = entry.setdefault(ref, (set(), set()))
            slot[0].add(variant)
            slot[1].update(types)
    unknown_seen = set()
    for asm_name in sorted(per_asm):
        pkg, asmdef, used = per_asm[asm_name]
        reached, guards, unknown = coverage(pkg, asmdef, mm.manifests, family_names)
        for u in unknown:
            if (pkg.repo, u) not in unknown_seen:
                unknown_seen.add((pkg.repo, u))
                notes.append('%s: the editor ships no manifest for %s, so the modules it brings are not counted'
                             % (pkg.repo, u))
        for ref in sorted(used):
            variants = sorted(used[ref][0], key=_variant_order)
            types = sorted(used[ref][1])
            if ref in mm.engine:
                need = mm.engine[ref]
                if need is None or asmdef.editor_only:
                    continue        # always present, or an Editor-only assembly: Unity gives it every module
                kind = 'module'
            elif ENGINE_MODULE_RE.match(ref):
                findings.append(Finding(repo=pkg.repo, package=pkg.name, assembly=asm_name, variants=variants,
                                        reference=ref, types=types, needs=os.path.basename(mm.source),
                                        kind='unknown-module'))
                continue
            elif ref in package_assemblies:
                need = package_assemblies[ref]
                if need == pkg.name or (pkg.repo, asm_name, ref) in already_reported:
                    continue
                kind = 'package'
            else:
                continue
            if need in reached:
                how = 'declared' if reached[need] == need else 'via %s' % reached[need]
            else:
                guard = next((g for g in sorted(guards) if need in mm.manifests.closure({g: None})[0]), None)
                how = 'versionDefine %s' % guards[guard] if guard else None
            prev = usage[pkg.repo].get(ref)
            if prev is None or prev[1] is not None:
                usage[pkg.repo][ref] = (need, how)
            if how is None:
                route = family_route(pkg, need, by_name, mm.manifests, family_names)
                findings.append(Finding(repo=pkg.repo, package=pkg.name, assembly=asm_name, variants=variants,
                                        reference=ref, types=types, needs=need, kind=kind,
                                        hint=('%s brings it transitively; declare it directly' % route)
                                        if route else None))
            elif how.startswith('versionDefine'):
                notes.append('%s: %s uses %s, accepted because of its %s for %s; level 1 compiles with every '
                             'module present, so it cannot see whether the use sits inside that #if'
                             % (pkg.repo, asm_name, ref, how, guard))
    return findings, usage, notes
