"""Turn asmdefs into one SDK-style project per (assembly, variant), with exactly the references Unity gives.

The rules that matter, each one learned from a false result in an earlier harness:
* An asmdef sees only the assemblies it lists. SDK ProjectReferences are transitive by default, so every
  generated project sets DisableTransitiveProjectReferences.
* Unity adds UnityEngine.UI (and UnityEditor.UI when compiling for the editor) to every asmdef without
  noEngineReferences (UnityCsReference AutoReferencedPackageAssemblies.cs). Without that, corelib reports
  dozens of false CS0246 errors.
* Engine module DLLs are referenced unless noEngineReferences; editor modules only in the editor variant.
* Unity compiles against its own netstandard.dll and compat shims, C# 9, with /nowarn:0169,0649,0282,1701,1702
  (read from the editor's own compiler response files).
* An assembly whose defineConstraints are unmet is not compiled at all; a reference to an assembly that is
  not compiled, or to a name that does not exist, is silently dropped by Unity.
"""
import os
from xml.sax.saxutils import escape

import versions

DOC_CODES = ('CS1591', 'CS1570', 'CS1572', 'CS1573', 'CS1574', 'CS1580', 'CS1584', 'CS1734')
AUTO_REFS = {'editor': ['UnityEngine.UI', 'UnityEditor.UI'], 'player': ['UnityEngine.UI']}
TEST_PACKAGES = (('NUnit', '3.14.0'), ('NUnit3TestAdapter', '4.6.0'), ('Microsoft.NET.Test.Sdk', '17.11.1'))


def msbuild_escape(path):
    out = path
    for ch, rep in (('%', '%25'), ('$', '%24'), ('@', '%40'), ("'", '%27'), (';', '%3B'), ('?', '%3F'),
                    ('*', '%2A')):
        out = out.replace(ch, rep)
    return escape(out)


class Node(object):
    """One project to build: an asmdef in one variant, a test runner, a README snippet or a canary."""

    def __init__(self, nid, name, variant, kind, package=None, asmdef=None):
        self.id = nid
        self.name = name
        self.variant = variant
        self.kind = kind
        self.package = package
        self.asmdef = asmdef
        self.sources = list(asmdef.sources) if asmdef else []
        self.defines = []
        self.hint_refs = []          # (path, private)
        self.deps = []               # Node
        self.notes = []
        self.docs_as_errors = False
        self.generate_docs = False
        self.optimize = variant == 'player'
        self.allow_unsafe = bool(asmdef and asmdef.data.get('allowUnsafeCode'))
        self.expect = 'pass'
        self.expect_codes = ()
        self.expect_mentions = None
        self.label = ''
        # filled in by the build
        self.status = None
        self.built = False
        self.reason = ''
        self.errors = []
        self.warnings = []
        self.log = ''
        self.dir = None

    @property
    def csproj(self):
        # Unique file names: a solution refuses two projects with the same name.
        return os.path.join(self.dir, '%s.%s.csproj' % (self.name, self.variant))

    @property
    def dll(self):
        return os.path.join(self.dir, 'bin', self.name + '.dll')


class Graph(object):
    def __init__(self, unity, unity_cfg, family, unity_packages, canaries, out, nunit_dll, overrides=None):
        self.unity = unity
        self.cfg = unity_cfg
        self.out = out
        self.nunit_dll = nunit_dll
        self.family = family
        self.packages_by_name = {p.name: p for p in family}
        self.versions = dict(unity_cfg['packages'])
        self.versions['Unity'] = unity.version
        for p in family:
            self.versions[p.name] = p.version
        for k, v in (overrides or {}).items():
            if v:
                self.versions[k] = v
            else:
                self.versions.pop(k, None)
        self.unity_packages = [p for p in unity_packages if p.name in self.versions]
        self.asm = {}
        self.guid = {}
        for p in list(family) + self.unity_packages + ([canaries] if canaries else []):
            for a in p.asmdefs:
                if a.name in self.asm:
                    raise SystemExit('two asmdefs named %s: %s and %s' % (a.name, self.asm[a.name].path, a.path))
                self.asm[a.name] = a
                if a.guid:
                    self.guid[a.guid] = a.name
        self.prebuilt = {}
        for n in unity_cfg.get('prebuiltAssemblies', []):
            path = unity.template_script_assembly(n)
            if path:
                self.prebuilt[n] = path
        self.nodes = {}
        self.skipped = []            # (asmdef, variant, reason)
        self._memo = {}

    # --- defines ------------------------------------------------------------------------------------
    def global_defines(self, variant):
        d = self.cfg['defines']
        v = 'editor' if variant in ('editor', 'test') else 'player'
        return versions.unity_version_symbols(self.unity.version) + d.get('common', []) + d.get(v, [])

    def asm_defines(self, asmdef, variant):
        return self.global_defines(variant) + versions.version_defines(asmdef.data, self.versions)

    # --- nodes --------------------------------------------------------------------------------------
    def _dir(self, variant, name):
        return os.path.join(self.out, variant, name)

    def node_for(self, asmdef, variant):
        """The project compiling ``asmdef`` for ``variant``, or None (with the reason recorded) when Unity
        would not compile it there."""
        key = (asmdef.name, variant)
        if key in self._memo:
            return self._memo[key]
        self._memo[key] = None
        reason = None
        if not asmdef.compiles_for(variant):
            reason = 'editor-only' if variant == 'player' else 'excluded from the editor'
        else:
            defines = self.asm_defines(asmdef, variant)
            if not versions.constraints_hold(asmdef.data.get('defineConstraints'), defines):
                reason = 'defineConstraints %s unmet' % asmdef.data.get('defineConstraints')
        if reason:
            self.skipped.append((asmdef, variant, reason))
            return None
        n = Node('%s/%s' % (variant, asmdef.name), asmdef.name, variant, asmdef.kind, asmdef.package, asmdef)
        n.dir = self._dir(variant, asmdef.name)
        n.defines = defines
        family = asmdef.package.origin == 'family'
        n.docs_as_errors = family and asmdef.kind in ('runtime', 'editor')
        n.generate_docs = asmdef.kind != 'unity'
        self._memo[key] = n
        self._resolve_refs(n, asmdef, variant)
        if asmdef.kind == 'canary':
            self._canary_expectation(n)
        self.nodes[n.id] = n
        return n

    def _resolve_refs(self, n, asmdef, variant):
        for raw in asmdef.references:
            name = self.guid.get(raw[5:]) if raw.startswith('GUID:') else raw
            if name is None:
                n.notes.append('reference %s matches no asmdef GUID; Unity ignores it' % raw)
                continue
            if name in self.asm:
                dep = self.node_for(self.asm[name], variant)
                if dep is None:
                    n.notes.append('reference %s is not compiled for %s; Unity drops it' % (name, variant))
                else:
                    n.deps.append(dep)
            elif name in self.prebuilt:
                n.hint_refs.append((self.prebuilt[name], False))
            elif name in self.cfg.get('prebuiltAssemblies', []):
                n.notes.append('reference %s: no prebuilt copy found in the editor\'s template cache' % name)
            else:
                n.notes.append('unresolved reference %s; Unity ignores it silently' % name)
        if asmdef.data.get('overrideReferences'):
            for pre in asmdef.data.get('precompiledReferences') or []:
                if pre == 'nunit.framework.dll' and self.nunit_dll:
                    n.hint_refs.append((self.nunit_dll, False))
                else:
                    n.notes.append('precompiled reference %s not available to the harness' % pre)
        n.hint_refs = [(r, False) for r in self.unity.netstandard_refs()] + n.hint_refs
        if not asmdef.no_engine:
            n.hint_refs += [(r, False) for r in self.unity.engine_refs()]
            if variant == 'editor':
                n.hint_refs += [(r, False) for r in self.unity.editor_refs()]
            if asmdef.name not in AUTO_REFS['editor']:
                for auto in AUTO_REFS[variant]:
                    if auto in self.asm and all(d.name != auto for d in n.deps):
                        dep = self.node_for(self.asm[auto], variant)
                        if dep is not None:
                            n.deps.append(dep)

    def _canary_expectation(self, n):
        import json
        path = os.path.join(n.asmdef.dir, 'expect.json')
        if os.path.exists(path):
            with open(path) as f:
                e = json.load(f)
            n.expect = e.get('expect', 'pass')
            n.expect_codes = tuple(e.get('codes', ()))
            n.expect_mentions = e.get('mentions')
            n.label = e.get('why', '')

    def add_test_runner(self, asmdef):
        """A net10.0 test project over the same sources, for `dotnet test`. The faithful compile of the test
        asmdef (netstandard2.1, Unity's NUnit, declared references only) is the editor node; this one exists
        only to execute the engine-free tests, so it may see its dependencies transitively."""
        faithful = self.node_for(asmdef, 'editor')
        n = Node('test/%s' % asmdef.name, asmdef.name, 'test', 'testrun', asmdef.package, asmdef)
        n.dir = self._dir('test', asmdef.name)
        n.defines = self.asm_defines(asmdef, 'test')
        if faithful is None:
            return None
        seen = []

        def walk(node):
            for d in node.deps:
                if d.variant == 'editor' and d not in seen:
                    seen.append(d)
                    walk(d)
        walk(faithful)
        n.deps = seen
        if not asmdef.no_engine:
            n.hint_refs += [(r, True) for r in self.unity.engine_refs() + self.unity.editor_refs()]
        for raw in asmdef.references:
            if raw in self.prebuilt:
                n.hint_refs.append((self.prebuilt[raw], True))
        self.nodes[n.id] = n
        return n

    def add_snippet(self, nid, name, source_text, package, closure_assemblies):
        """A README code fence compiled as user code would be: player variant, seeing the package's
        auto-referenced assemblies, those of its dependencies, the engine and ugui (as Assembly-CSharp does)."""
        n = Node(nid, name, 'player', 'snippet', package)
        n.dir = os.path.join(self.out, 'snippet', name)
        n.defines = self.global_defines('player')
        n.hint_refs = [(r, False) for r in self.unity.netstandard_refs() + self.unity.engine_refs()]
        for a in closure_assemblies:
            dep = self.node_for(a, 'player')
            if dep is not None:
                n.deps.append(dep)
        for p in self.unity_packages:
            for a in p.asmdefs:
                if a.data.get('autoReferenced', True) and a.compiles_for('player'):
                    dep = self.node_for(a, 'player')
                    if dep is not None and dep not in n.deps:
                        n.deps.append(dep)
        os.makedirs(n.dir, exist_ok=True)
        src = os.path.join(n.dir, 'Snippet.cs')
        _write_if_changed(src, source_text)
        n.sources = [src]
        self.nodes[n.id] = n
        return n


def _write_if_changed(path, text):
    try:
        with open(path, encoding='utf-8') as f:
            if f.read() == text:
                return
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def write_project(node, cfg):
    nowarn = list(cfg.get('noWarn', []))
    props = [
        ('AssemblyName', node.name),
        ('LangVersion', '9.0'),
        ('Nullable', 'disable'),
        ('ImplicitUsings', 'disable'),
        ('EnableDefaultItems', 'false'),
        ('GenerateAssemblyInfo', 'false'),
        ('GenerateTargetFrameworkAttribute', 'false'),
        ('Deterministic', 'true'),
        ('DebugType', 'portable'),
        ('Optimize', 'true' if node.optimize else 'false'),
        ('AllowUnsafeBlocks', 'true' if node.allow_unsafe else 'false'),
        ('DefineConstants', ';'.join(dict.fromkeys(node.defines))),
        ('OutputPath', 'bin/'),
        ('AppendTargetFrameworkToOutputPath', 'false'),
        ('ProduceReferenceAssembly', 'false'),
        ('EnableNETAnalyzers', 'false'),
        ('RunAnalyzers', 'false'),
        ('NuGetAudit', 'false'),
        ('IsPackable', 'false'),
        ('TreatWarningsAsErrors', 'false'),
    ]
    items = []
    if node.kind == 'testrun':
        props = [('TargetFramework', 'net10.0'), ('IsTestProject', 'true'),
                 ('GenerateDocumentationFile', 'false')] + props
        nowarn += ['1591', 'NU1701', 'NU1603']
        items += ['<PackageReference Include="%s" Version="%s" />' % pv for pv in TEST_PACKAGES]
    else:
        props = [('TargetFramework', 'netstandard2.1'), ('DisableImplicitFrameworkReferences', 'true'),
                 ('NoStdLib', 'true'), ('DisableTransitiveProjectReferences', 'true'),
                 ('GenerateDependencyFile', 'false'),
                 ('GenerateDocumentationFile', 'true' if node.generate_docs else 'false')] + props
        if node.kind == 'unity':
            props.append(('WarningLevel', '0'))
        if not node.docs_as_errors and node.generate_docs:
            nowarn.append('1591')
    props.append(('NoWarn', ';'.join(nowarn)))
    items += ['<Compile Include="%s" />' % msbuild_escape(s) for s in node.sources]
    for path, private in node.hint_refs:
        items.append('<Reference Include="%s"><HintPath>%s</HintPath><Private>%s</Private></Reference>'
                     % (escape(os.path.splitext(os.path.basename(path))[0]), msbuild_escape(path),
                        'true' if private else 'false'))
    for d in node.deps:
        items.append('<ProjectReference Include="%s" />' % msbuild_escape(d.csproj))
    text = ('<Project Sdk="Microsoft.NET.Sdk">\n  <!-- generated by upm-tools from %s; do not edit -->\n'
            '  <PropertyGroup>\n%s\n  </PropertyGroup>\n  <ItemGroup>\n%s\n  </ItemGroup>\n</Project>\n') % (
        escape(node.asmdef.path if node.asmdef else node.id),
        '\n'.join('    <%s>%s</%s>' % (k, escape(v), k) for k, v in props),
        '\n'.join('    ' + i for i in items))
    _write_if_changed(node.csproj, text)
