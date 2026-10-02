"""Tests for the built-in module check (lib/modules.py, the second half of the deps step).

* Rule tests on a synthetic editor: no Unity install needed.
* Map tests on the installed editors (skipped when the editor is missing).
* The fixture test runs level 1 on a copy of tests/fixtures/audio-listener, which reproduces the corelib
  AudioListener defect: it must fail without com.unity.modules.audio and pass with it. Needs the default editor
  and the .NET SDK; skipped otherwise. Takes a few seconds.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TOOLS, 'lib'))

import modules as MOD  # noqa: E402
from unity import DEFAULT_EDITOR, HUB_EDITORS, UnityInstall  # noqa: E402

MODULES_ASSET = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!877146078 &1
PlatformModuleSetup:
  m_ObjectHideFlags: 0
  modules:
  - name: SharedInternals
    dependencies: []
    strippable: 1
    controlledByBuiltinPackage: 0
  - name: Core
    dependencies:
      - SharedInternals
    strippable: 1
    controlledByBuiltinPackage: 0
  - name: Audio
    dependencies:
      - Core
      - SharedInternals
    strippable: 1
    controlledByBuiltinPackage: 1
  - name: IMGUI
    dependencies:
      - Core
    strippable: 1
    controlledByBuiltinPackage: 1
  - name: UI
    dependencies:
      - Core
    strippable: 1
    controlledByBuiltinPackage: 1
"""


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f)


def package(name, deps=None, repo=None):
    return SimpleNamespace(name=name, repo=repo or name, dependencies=deps or {})


def asmdef(name, editor_only=False, **data):
    return SimpleNamespace(name=name, editor_only=editor_only, data=data)


class ParseTests(unittest.TestCase):
    def test_reads_names_dependencies_and_the_flag(self):
        mods = MOD.parse_modules_asset(MODULES_ASSET)
        self.assertEqual([m[0] for m in mods], ['SharedInternals', 'Core', 'Audio', 'IMGUI', 'UI'])
        self.assertEqual(mods[0][1], [])
        self.assertEqual(mods[2][1], ['Core', 'SharedInternals'])
        self.assertEqual([m[2] for m in mods], [False, False, True, True, True])

    def test_a_module_without_the_flag_is_an_error(self):
        with self.assertRaises(MOD.ModuleDataError):
            MOD.parse_modules_asset('  modules:\n  - name: Core\n    dependencies: []\n')


class RuleTests(unittest.TestCase):
    """The check on a synthetic editor: modules.asset above, five built-in packages and one tarball."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        builtin = os.path.join(self.tmp, 'BuiltInPackages')
        for name, deps in (('com.unity.modules.audio', {}), ('com.unity.modules.imgui', {}),
                           ('com.unity.modules.ui', {}),
                           ('com.unity.ugui', {'com.unity.modules.ui': '1.0.0',
                                               'com.unity.modules.imgui': '1.0.0'}),
                           ('com.unity.timeline-ish', {'com.unity.modules.audio': '1.0.0'})):
            write_json(os.path.join(builtin, name, 'package.json'), {'name': name, 'version': '1.0.0',
                                                                     'dependencies': deps})
        tarballs = os.path.join(self.tmp, 'Editor')
        os.makedirs(tarballs)
        body = json.dumps({'name': 'com.unity.test-framework', 'version': '1.4.6',
                           'dependencies': {'com.unity.modules.imgui': '1.0.0'}}).encode()
        with tarfile.open(os.path.join(tarballs, 'com.unity.test-framework-1.4.6.tgz'), 'w:gz') as t:
            info = tarfile.TarInfo('package/package.json')
            info.size = len(body)
            t.addfile(info, io.BytesIO(body))
        self.mm = MOD.ModuleMap(MOD.parse_modules_asset(MODULES_ASSET),
                                MOD.UnityManifests(builtin, tarballs), 'modules.asset')
        self.family_names = {'com.openugd.core', 'com.openugd.widgets', 'com.openugd.audio-user'}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_check(self, pkg, asm, refs, family=None, variants=('editor', 'player'), already=()):
        judged = [(pkg, asm, v, refs) for v in variants]
        return MOD.check([pkg], family or [pkg], judged, self.mm, {'UnityEngine.UI': 'com.unity.ugui'},
                         self.family_names, already)

    def test_map(self):
        self.assertEqual(self.mm.problems, [])
        self.assertIsNone(self.mm.engine['UnityEngine.CoreModule'])
        self.assertEqual(self.mm.engine['UnityEngine.AudioModule'], 'com.unity.modules.audio')
        self.assertEqual(self.mm.always_present, ['UnityEngine.CoreModule', 'UnityEngine.SharedInternalsModule'])

    def test_a_controlled_module_without_its_package_is_a_finding(self):
        pkg = package('com.openugd.core', {'com.unity.ugui': '2.0.0'})
        findings, usage, _ = self.run_check(pkg, asmdef('com.openugd.core'),
                                            {'UnityEngine.AudioModule': ['UnityEngine.AudioListener'],
                                             'UnityEngine.CoreModule': ['UnityEngine.MonoBehaviour']})
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual((f.kind, f.assembly, f.reference, f.needs, f.variants, f.types),
                         ('module', 'com.openugd.core', 'UnityEngine.AudioModule', 'com.unity.modules.audio',
                          ['editor', 'player'], ['UnityEngine.AudioListener']))
        self.assertIn('com.unity.modules.audio', f.message())
        self.assertEqual(usage['com.openugd.core'], {'UnityEngine.AudioModule': ('com.unity.modules.audio', None)})

    def test_declaring_the_module_package_passes(self):
        pkg = package('com.openugd.core', {'com.unity.modules.audio': '1.0.0'})
        findings, usage, _ = self.run_check(pkg, asmdef('com.openugd.core'),
                                            {'UnityEngine.AudioModule': ['UnityEngine.AudioListener']})
        self.assertEqual(findings, [])
        self.assertEqual(usage['com.openugd.core']['UnityEngine.AudioModule'],
                         ('com.unity.modules.audio', 'declared'))

    def test_a_declared_unity_package_brings_its_modules(self):
        pkg = package('com.openugd.core', {'com.unity.ugui': '2.0.0'})
        findings, usage, _ = self.run_check(pkg, asmdef('com.openugd.core'),
                                            {'UnityEngine.UIModule': ['UnityEngine.Canvas'],
                                             'UnityEngine.IMGUIModule': ['UnityEngine.Event']})
        self.assertEqual(findings, [])
        self.assertEqual(usage['com.openugd.core']['UnityEngine.UIModule'],
                         ('com.unity.modules.ui', 'via com.unity.ugui'))

    def test_a_package_shipped_as_a_tarball_is_read_too(self):
        pkg = package('com.openugd.core', {'com.unity.test-framework': '1.4.6'})
        findings, usage, notes = self.run_check(pkg, asmdef('com.openugd.core'),
                                                {'UnityEngine.IMGUIModule': ['UnityEngine.GUI']})
        self.assertEqual((findings, notes), ([], []))
        self.assertEqual(usage['com.openugd.core']['UnityEngine.IMGUIModule'][1], 'via com.unity.test-framework')

    def test_a_package_the_editor_does_not_ship_is_a_note(self):
        pkg = package('com.openugd.core', {'com.example.thirdparty': '1.0.0'})
        findings, _, notes = self.run_check(pkg, asmdef('com.openugd.core'),
                                            {'UnityEngine.AudioModule': ['UnityEngine.AudioSource']})
        self.assertEqual(len(findings), 1)
        self.assertTrue(any('com.example.thirdparty' in n for n in notes))

    def test_editor_only_assemblies_need_no_module(self):
        pkg = package('com.openugd.core')
        findings, usage, _ = self.run_check(pkg, asmdef('com.openugd.core.editor', editor_only=True),
                                            {'UnityEngine.AudioModule': ['UnityEngine.AudioSource']},
                                            variants=('editor',))
        self.assertEqual((findings, usage['com.openugd.core']), ([], {}))

    def test_editor_modules_need_no_declaration(self):
        pkg = package('com.openugd.core')
        findings, usage, _ = self.run_check(pkg, asmdef('com.openugd.core'),
                                            {'UnityEditor.CoreModule': ['UnityEditor.EditorApplication'],
                                             'UnityEditor.PhysicsModule': ['UnityEditor.PhysicsDebugWindow']},
                                            variants=('editor',))
        self.assertEqual((findings, usage['com.openugd.core']), ([], {}))

    def test_a_module_missing_from_modules_asset_cannot_be_judged(self):
        pkg = package('com.openugd.core')
        findings, _, _ = self.run_check(pkg, asmdef('com.openugd.core'),
                                        {'UnityEngine.MysteryModule': ['UnityEngine.Mystery']})
        self.assertEqual([f.kind for f in findings], ['unknown-module'])

    def test_family_dependencies_do_not_count_but_are_named(self):
        audio_user = package('com.openugd.audio-user', {'com.unity.modules.audio': '1.0.0'})
        pkg = package('com.openugd.widgets', {'com.openugd.audio-user': '2.0.0'})
        findings, _, _ = self.run_check(pkg, asmdef('com.openugd.widgets'),
                                        {'UnityEngine.AudioModule': ['UnityEngine.AudioListener']},
                                        family=[pkg, audio_user])
        self.assertEqual(len(findings), 1)
        self.assertIn('com.openugd.audio-user brings it transitively', findings[0].hint)

    def test_define_constraints_on_a_version_define_cover_the_package(self):
        pkg = package('com.openugd.core')
        asm = asmdef('com.openugd.core.audio', defineConstraints=['OPENUGD_AUDIO'],
                     versionDefines=[{'name': 'com.unity.timeline-ish', 'expression': '',
                                      'define': 'OPENUGD_AUDIO'}])
        findings, usage, _ = self.run_check(pkg, asm, {'UnityEngine.AudioModule': ['UnityEngine.AudioSource']})
        self.assertEqual(findings, [])
        self.assertEqual(usage['com.openugd.core']['UnityEngine.AudioModule'][1], 'via com.unity.timeline-ish')

    def test_an_unconstrained_version_define_is_trusted_with_a_note(self):
        pkg = package('com.openugd.core')
        asm = asmdef('com.openugd.core', versionDefines=[{'name': 'com.unity.modules.audio', 'expression': '',
                                                          'define': 'OPENUGD_AUDIO'}])
        findings, usage, notes = self.run_check(pkg, asm, {'UnityEngine.AudioModule': ['UnityEngine.AudioSource']})
        self.assertEqual(findings, [])
        self.assertEqual(usage['com.openugd.core']['UnityEngine.AudioModule'][1], 'versionDefine OPENUGD_AUDIO')
        self.assertTrue(any('cannot see whether the use sits inside that #if' in n for n in notes))

    def test_implicit_ugui_needs_com_unity_ugui(self):
        pkg = package('com.openugd.core')
        findings, _, _ = self.run_check(pkg, asmdef('com.openugd.core.editor', editor_only=True),
                                        {'UnityEngine.UI': ['UnityEngine.UI.Image']}, variants=('editor',))
        self.assertEqual([(f.kind, f.needs) for f in findings], [('package', 'com.unity.ugui')])
        pkg = package('com.openugd.core', {'com.unity.ugui': '2.0.0'})
        findings, _, _ = self.run_check(pkg, asmdef('com.openugd.core'),
                                        {'UnityEngine.UI': ['UnityEngine.UI.Image']})
        self.assertEqual(findings, [])

    def test_a_reference_the_asmdef_rule_reported_is_not_repeated(self):
        pkg = package('com.openugd.core', repo='upm-core')
        findings, _, _ = self.run_check(pkg, asmdef('com.openugd.core'),
                                        {'UnityEngine.UI': ['UnityEngine.UI.Image']},
                                        already={('upm-core', 'com.openugd.core', 'UnityEngine.UI')})
        self.assertEqual(findings, [])

    def test_variants_are_merged_per_assembly(self):
        pkg = package('com.openugd.core')
        asm = asmdef('com.openugd.core')
        judged = [(pkg, asm, 'editor', {'UnityEngine.AudioModule': ['UnityEngine.AudioSource']}),
                  (pkg, asm, 'player', {'UnityEngine.CoreModule': ['UnityEngine.Object']})]
        findings, _, _ = MOD.check([pkg], [pkg], judged, self.mm, {}, self.family_names)
        self.assertEqual([f.variants for f in findings], [['editor']])


def editor_installed(version):
    return os.path.isdir(os.path.join(HUB_EDITORS, version, 'Unity.app'))


class EditorMapTests(unittest.TestCase):
    """The map read from each installed editor. The lists are not hard-coded: these are spot checks."""

    def check_editor(self, version):
        mm = MOD.ModuleMap.from_editor(UnityInstall(version))
        self.assertEqual(mm.problems, [])
        self.assertIsNone(mm.engine['UnityEngine.CoreModule'])
        self.assertIsNone(mm.engine['UnityEngine.SharedInternalsModule'])
        self.assertEqual(mm.engine['UnityEngine.AudioModule'], 'com.unity.modules.audio')
        self.assertEqual(mm.engine['UnityEngine.UIModule'], 'com.unity.modules.ui')
        reached, unknown = mm.manifests.closure({'com.unity.ugui': '2.0.0'})
        self.assertIn('com.unity.modules.ui', reached)
        self.assertIn('com.unity.modules.imgui', reached)
        self.assertEqual(unknown, [])
        reached, unknown = mm.manifests.closure({'com.unity.test-framework': None})
        self.assertIn('com.unity.modules.jsonserialize', reached)
        self.assertEqual(unknown, [])

    @unittest.skipUnless(editor_installed('6000.0.41f1'), 'Unity 6000.0.41f1 not installed')
    def test_6000_0(self):
        self.check_editor('6000.0.41f1')

    @unittest.skipUnless(editor_installed('6000.3.3f1'), 'Unity 6000.3.3f1 not installed')
    def test_6000_3(self):
        self.check_editor('6000.3.3f1')


@unittest.skipUnless(editor_installed(DEFAULT_EDITOR) and shutil.which('dotnet'),
                     'needs Unity %s and the .NET SDK' % DEFAULT_EDITOR)
class AudioListenerFixtureTests(unittest.TestCase):
    """Level 1 (build and deps steps) on a copy of tests/fixtures/audio-listener."""

    REPO = 'audio-listener'

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.root = os.path.join(cls.tmp, 'root')
        shutil.copytree(os.path.join(TOOLS, 'tests', 'fixtures', cls.REPO), os.path.join(cls.root, cls.REPO))
        cls.out = os.path.join(cls.tmp, 'out')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def level1(self, extra_dependencies):
        pj = os.path.join(self.root, self.REPO, 'package.json')
        with open(os.path.join(TOOLS, 'tests', 'fixtures', self.REPO, 'package.json')) as f:
            data = json.load(f)
        data['dependencies'].update(extra_dependencies)
        with open(pj, 'w') as f:
            json.dump(data, f, indent=2)
        p = subprocess.run([sys.executable, os.path.join(TOOLS, 'lib', 'level1.py'), '--root', self.root,
                            '--packages', self.REPO, '--steps', 'build,deps', '--out', self.out],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
        text = p.stdout.decode('utf-8', 'replace')
        report_path = os.path.join(self.out, 'level1-report.json')
        self.assertTrue(os.path.exists(report_path), text)
        with open(report_path) as f:
            return p.returncode, json.load(f), text

    def test_fails_without_the_audio_module_and_passes_with_it(self):
        code, report, text = self.level1({})
        self.assertEqual(code, 1, text)
        steps = {s['step']: s['status'] for s in report['summary']}
        self.assertEqual(steps, {'build': 'PASS', 'deps': 'FAIL'}, text)
        mods = report['depsModules']
        self.assertEqual([(f['assembly'], f['reference'], f['needs'], f['variants']) for f in mods['findings']],
                         [('upm-tools.fixture.audio-listener', 'UnityEngine.AudioModule',
                           'com.unity.modules.audio', ['editor', 'player'])], text)
        self.assertIn('UnityEngine.AudioListener', mods['findings'][0]['types'])
        usage = mods['usage'][self.REPO]
        self.assertEqual(usage['UnityEngine.UIModule'],
                         {'needs': 'com.unity.modules.ui', 'how': 'via com.unity.ugui'})
        self.assertNotIn('UnityEngine.CoreModule', usage)
        self.assertIn('UnityEngine.CoreModule', mods['alwaysPresent'])
        self.assertEqual(mods['unchecked'], [])

        code, report, text = self.level1({'com.unity.modules.audio': '1.0.0'})
        self.assertEqual(code, 0, text)
        mods = report['depsModules']
        self.assertEqual(mods['findings'], [])
        self.assertEqual(mods['usage'][self.REPO]['UnityEngine.AudioModule'],
                         {'needs': 'com.unity.modules.audio', 'how': 'declared'})


if __name__ == '__main__':
    unittest.main()
