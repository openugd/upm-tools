"""Locate the pieces of an installed Unity editor the tools need, across the two install layouts in use.

6000.0 keeps the scripting files directly in Unity.app/Contents; 6000.3 moved them to
Unity.app/Contents/Resources/Scripting. Everything here is read-only.
"""
import glob
import os
import re
import tarfile

# Where a bare version ('6000.0.41f1') is looked up. CI points it at a partial editor from ci/fetch-editor.py.
HUB_EDITORS = os.environ.get('OPENUGD_UNITY_EDITORS') or '/Applications/Unity/Hub/Editor'
DEFAULT_EDITOR = '6000.0.41f1'
_VERSION_RE = re.compile(r'^\d+\.\d+\.\d+[a-z]+\d+$')


def editor_path(spec):
    """Accept '6000.0.41f1' (resolved under the Hub folder) or any path to the editor folder/app."""
    if _VERSION_RE.match(spec) and not os.path.exists(spec):
        return os.path.join(HUB_EDITORS, spec)
    return spec


class UnityInstall(object):
    def __init__(self, spec, version=None):
        path = os.path.abspath(editor_path(spec))
        if path.endswith('/Contents'):
            contents = path
        elif path.endswith('.app'):
            contents = os.path.join(path, 'Contents')
        else:
            contents = os.path.join(path, 'Unity.app', 'Contents')
        if not os.path.isdir(contents):
            raise SystemExit('Unity editor not found: %s (looked for %s)' % (spec, contents))
        self.contents = contents
        self.root = os.path.dirname(os.path.dirname(contents))
        scripting = os.path.join(contents, 'Resources', 'Scripting')
        self.scripting = scripting if os.path.isdir(os.path.join(scripting, 'Managed')) else contents
        self.version = version or self._detect_version()

    def _detect_version(self):
        name = os.path.basename(self.root)
        if _VERSION_RE.match(name):
            return name
        plist = os.path.join(self.contents, 'Info.plist')
        if os.path.exists(plist):
            import plistlib
            with open(plist, 'rb') as f:
                v = plistlib.load(f).get('CFBundleVersion', '')
            if _VERSION_RE.match(v):
                return v
        raise SystemExit('cannot tell the Unity version of %s; pass --unity-version' % self.root)

    # --- compile references -------------------------------------------------------------------------
    @property
    def engine_dir(self):
        return os.path.join(self.scripting, 'Managed', 'UnityEngine')

    def engine_refs(self):
        return sorted(glob.glob(os.path.join(self.engine_dir, 'UnityEngine*.dll')))

    def editor_refs(self):
        refs = sorted(glob.glob(os.path.join(self.engine_dir, 'UnityEditor*.dll')))
        graphs = os.path.join(self.scripting, 'Managed', 'UnityEditor.Graphs.dll')
        return refs + ([graphs] if os.path.exists(graphs) else [])

    def netstandard_refs(self):
        """Unity compiles against its own .NET Standard 2.1 reference assembly plus the compat shims."""
        base = os.path.join(self.scripting, 'NetStandard')
        refs = [os.path.join(base, 'ref', '2.1.0', 'netstandard.dll')]
        for sub in ('netstandard', 'netfx'):
            refs += sorted(glob.glob(os.path.join(base, 'compat', '2.1.0', 'shims', sub, '*.dll')))
        missing = [r for r in refs[:1] if not os.path.exists(r)]
        if missing:
            raise SystemExit('netstandard.dll missing in %s' % base)
        return refs

    # --- packages the editor ships ------------------------------------------------------------------
    @property
    def builtin_packages(self):
        return os.path.join(self.contents, 'Resources', 'PackageManager', 'BuiltInPackages')

    def builtin_package_dir(self, name):
        d = os.path.join(self.builtin_packages, name)
        return d if os.path.isfile(os.path.join(d, 'package.json')) else None

    def template_script_assembly(self, assembly):
        """Unity ships project templates with a prebuilt Library/ScriptAssemblies (editor builds of
        ugui, TMP and the Test Framework compiled by this exact editor)."""
        pattern = os.path.join(self.contents, 'Resources', 'PackageManager', 'ProjectTemplates', 'libcache',
                               '*', 'ScriptAssemblies', assembly + '.dll')
        hits = sorted(glob.glob(pattern))
        return hits[-1] if hits else None

    def nunit_framework(self, cache_dir):
        """Unity's custom NUnit (com.unity.ext.nunit, NUnit 3.5 based). Built in from 6000.3; shipped as a
        tarball in the editor's package cache before that."""
        rel = os.path.join('net40', 'unity-custom', 'nunit.framework.dll')
        d = self.builtin_package_dir('com.unity.ext.nunit')
        if d and os.path.exists(os.path.join(d, rel)):
            return os.path.join(d, rel)
        tgzs = sorted(glob.glob(os.path.join(self.contents, 'Resources', 'PackageManager', 'Editor',
                                             'com.unity.ext.nunit-*.tgz')))
        if not tgzs:
            return None
        out = os.path.join(cache_dir, os.path.basename(tgzs[-1])[:-4], rel)
        if not os.path.exists(out):
            with tarfile.open(tgzs[-1]) as t:
                member = t.getmember('package/' + rel)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with t.extractfile(member) as src, open(out, 'wb') as dst:
                    dst.write(src.read())
        return out

    # --- IL2CPP / linker ----------------------------------------------------------------------------
    @property
    def linker(self):
        return os.path.join(self.scripting, 'il2cpp', 'build', 'deploy', 'UnityLinker')

    @property
    def unityaot(self):
        return os.path.join(self.scripting, 'MonoBleedingEdge', 'lib', 'mono', 'unityaot-macos')

    @property
    def mono(self):
        return os.path.join(self.scripting, 'MonoBleedingEdge', 'bin', 'mono')

    @property
    def csc(self):
        """Unity's bundled Roslyn and the .NET runtime it runs on: the compiler the editor itself uses."""
        return (os.path.join(self.scripting, 'NetCoreRuntime', 'dotnet'),
                os.path.join(self.scripting, 'DotNetSdkRoslyn', 'csc.dll'))

    @property
    def executable(self):
        return os.path.join(self.contents, 'MacOS', 'Unity')
