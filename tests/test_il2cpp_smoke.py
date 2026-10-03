"""Unit tests for il2cpp-smoke.sh (lib/il2cpp_smoke.py, lib/headless.py) that need no Unity licence.

The end-to-end tests run the script's main() against a fake editor (fixtures/il2cpp-smoke/fake_unity.py) that
writes what the real editor and SmokeBuild.Run write: a licence failure, a compile error, or a build folder whose
page reports a summary. The headless-Chrome cases are skipped without Chrome. TemplateCompileTests compiles the
template's C# against the package checkouts with the editor's own Roslyn; it is skipped without the default editor or
the checkouts.
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import warnings

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TOOLS, 'lib'))

import headless  # noqa: E402
import il2cpp_smoke as S  # noqa: E402

FIXTURE = os.path.join(TOOLS, 'tests', 'fixtures', 'il2cpp-smoke')
BOOT = os.path.join(TOOLS, 'il2cpp-template', 'Assets', 'Il2CppSmoke', 'Boot.cs')


def write(root, rel, text='x'):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    return path


class FakeUnity(object):
    def __init__(self, version):
        self.version = version


class SummaryTests(unittest.TestCase):
    def test_pass(self):
        self.assertEqual(S.parse_summary('OPENUGD-IL2CPP: PASS 29/29'),
                         {'status': 'PASS', 'failed': 0, 'total': 29, 'names': []})

    def test_fail_counts_the_failed_checks_and_names_them(self):
        s = S.parse_summary('OPENUGD-IL2CPP: FAIL 2/29 inject-field,presenter-view')
        self.assertEqual((s['status'], s['failed'], s['total']), ('FAIL', 2, 29))
        self.assertEqual(s['names'], ['inject-field', 'presenter-view'])

    def test_malformed(self):
        for text in ('OPENUGD-IL2CPP: PASS 28/29', 'OPENUGD-IL2CPP: PASS 0/0', 'OPENUGD-IL2CPP: FAIL 2/29 one',
                     'OPENUGD-IL2CPP: FAIL 0/29', 'OPENUGD-IL2CPP: FAIL 30/29 a', 'OPENUGD-IL2CPP PASS 1/1',
                     'Unity WebGL Player | OpenUGD IL2CPP Smoke', '', None):
            self.assertIsNone(S.parse_summary(text), text)

    def test_console_lines(self):
        checks, summaries = S.parse_console([
            'OPENUGD-IL2CPP boot: Unity 6000.0.41f1, WebGLPlayer, IL2CPP, release build',
            'OPENUGD-IL2CPP check player-il2cpp: PASS',
            'OPENUGD-IL2CPP check inject-field: FAIL [Inject] public field left null',
            'Smoke/Boot->il2cpp-log-probe',
            'OPENUGD-IL2CPP: FAIL 1/2 inject-field'])
        self.assertEqual(checks, [('player-il2cpp', 'PASS', ''),
                                  ('inject-field', 'FAIL', '[Inject] public field left null')])
        self.assertEqual([s['status'] for s in summaries], ['FAIL'])

    def page(self, title, console, reached=True):
        return {'title': title, 'reached': reached, 'seconds': 3.0, 'console': [('log', t) for t in console],
                'exceptions': []}

    def test_judge_pass(self):
        status, summary, checks, problems = S.judge_page(self.page('OPENUGD-IL2CPP: PASS 1/1', [
            'OPENUGD-IL2CPP check a: PASS', 'OPENUGD-IL2CPP: PASS 1/1']))
        self.assertEqual((status, problems), ('PASS', []))

    def test_judge_no_result_and_mismatch(self):
        self.assertEqual(S.judge_page(self.page('Unity WebGL Player', [], reached=False))[0], 'NO RESULT')
        status, _, _, problems = S.judge_page(self.page('OPENUGD-IL2CPP: PASS 2/2', [
            'OPENUGD-IL2CPP check a: PASS', 'OPENUGD-IL2CPP: PASS 2/2']))
        self.assertEqual(status, 'PASS')
        self.assertTrue(any('1 check line' in p for p in problems))


class BootScriptTests(unittest.TestCase):
    """The summary's count is only meaningful if Boot.Checks lists every check exactly once."""

    def setUp(self):
        with open(BOOT, encoding='utf-8') as f:
            self.source = f.read()
        block = re.search(r'Checks\s*=\s*\{(.*?)\};', self.source, re.S).group(1)
        self.listed = re.findall(r'"([^"]+)"', block)

    def test_names_are_unique_and_parseable(self):
        self.assertEqual(len(self.listed), len(set(self.listed)))
        self.assertGreaterEqual(len(self.listed), 25)
        for name in self.listed:
            self.assertRegex(name, r'^[a-z0-9-]+$')
            self.assertTrue(S.CHECK_RE.match('OPENUGD-IL2CPP check %s: PASS' % name))

    def test_every_listed_check_runs_and_nothing_else_does(self):
        used = set(re.findall(r'\b(?:Check|Record)\("([^"]+)"', self.source))
        self.assertEqual(used, set(self.listed))

    def test_prefix_matches_the_tool(self):
        self.assertIn('public const string Prefix = "OPENUGD-IL2CPP";', self.source)
        self.assertEqual(S.SUMMARY_PREFIX, 'OPENUGD-IL2CPP:')


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='upm-tools-il2cpp-test-')
        self.template = os.path.join(self.tmp, 'template')
        self.project = os.path.join(self.tmp, 'project')
        self.config = os.path.join(self.tmp, 'config')
        write(self.template, 'Packages/manifest.json', '{"dependencies": {"com.unity.ugui": "1.0.0"}, '
                                                       '"testables": ["x"]}')
        write(self.template, 'Assets/Il2CppSmoke/Boot.cs', 'v1')
        write(self.template, 'Assets/Il2CppSmoke/Old.cs', 'old')
        write(self.template, 'ProjectSettings/EditorSettings.asset')
        write(self.config, 'unity/6000.0.41f1.json', '{"packages": {"com.unity.ugui": "2.0.0"}}')
        self.packed = [{'name': 'com.openugd.lifetime', 'tgz': '/x/Tarballs/com.openugd.lifetime-2.0.0.tgz'}]

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def refresh(self, version='6000.0.41f1', clean=False):
        return S.refresh_project(self.project, FakeUnity(version), self.packed, clean=clean,
                                 template=self.template, config_dir=self.config)

    def test_manifest(self):
        m = S.build_manifest({'dependencies': {'com.unity.ugui': '1.0.0', 'com.other': '3.0.0'}, 'testables': []},
                             self.packed, {'com.unity.ugui': '2.0.0'})
        self.assertEqual(m['dependencies'], {'com.unity.ugui': '2.0.0', 'com.other': '3.0.0',
                                             'com.openugd.lifetime': 'file:../Tarballs/com.openugd.lifetime-2.0.0.tgz'})
        self.assertNotIn('testables', m)

    def test_refresh_creates_and_updates(self):
        manifest = self.refresh()
        self.assertEqual(manifest['dependencies']['com.unity.ugui'], '2.0.0')
        with open(os.path.join(self.project, 'ProjectSettings', 'ProjectVersion.txt')) as f:
            self.assertEqual(f.read(), 'm_EditorVersion: 6000.0.41f1\n')
        write(self.project, 'Packages/packages-lock.json', '{}')
        write(self.project, 'Assets/Il2CppSmoke/Old.cs.meta', 'guid')
        write(self.project, 'Assets/Mine.cs', 'kept')
        os.remove(os.path.join(self.template, 'Assets/Il2CppSmoke/Old.cs'))
        write(self.template, 'Assets/Il2CppSmoke/Boot.cs', 'v2')
        self.refresh()
        self.assertFalse(os.path.exists(os.path.join(self.project, 'Packages/packages-lock.json')))
        self.assertFalse(os.path.exists(os.path.join(self.project, 'Assets/Il2CppSmoke/Old.cs')))
        self.assertFalse(os.path.exists(os.path.join(self.project, 'Assets/Il2CppSmoke/Old.cs.meta')))
        self.assertTrue(os.path.exists(os.path.join(self.project, 'Assets/Mine.cs')))
        with open(os.path.join(self.project, 'Assets/Il2CppSmoke/Boot.cs')) as f:
            self.assertEqual(f.read(), 'v2')

    def test_library_deleted_on_editor_change_or_clean(self):
        self.refresh()
        write(self.project, 'Library/x', 'cache')
        with contextlib.redirect_stdout(io.StringIO()):
            self.refresh()
            self.assertTrue(os.path.exists(os.path.join(self.project, 'Library/x')))
            self.refresh(version='6000.3.3f1')
            self.assertFalse(os.path.exists(os.path.join(self.project, 'Library')))
            write(self.project, 'Library/x', 'cache')
            self.refresh(version='6000.3.3f1', clean=True)
            self.assertFalse(os.path.exists(os.path.join(self.project, 'Library')))

    def test_real_template_has_what_the_build_needs(self):
        files = S.template_files()
        for rel in ('Packages/manifest.json', 'ProjectSettings/EditorSettings.asset',
                    'Assets/Il2CppSmoke/Boot.cs', 'Assets/Il2CppSmoke/Editor/SmokeBuild.cs',
                    'Assets/Plugins/WebGL/OpenUGDSmoke.jslib'):
            self.assertIn(rel, files)
        with open(os.path.join(S.TEMPLATE, 'Assets', 'Il2CppSmoke', 'Editor', 'SmokeBuild.cs')) as f:
            text = f.read()
        self.assertIn('namespace Il2CppSmoke.Editor', text)
        self.assertEqual(S.EXECUTE_METHOD, 'Il2CppSmoke.Editor.SmokeBuild.Run')
        with open(os.path.join(S.TEMPLATE, 'Assets', 'Plugins', 'WebGL', 'OpenUGDSmoke.jslib')) as f:
            self.assertIn('OpenUGDSmokeSetTitle', f.read())


class BuildFolderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='upm-tools-il2cpp-build-')
        write(self.dir, 'index.html', '<html></html>')
        for name in ('WebGL.loader.js', 'WebGL.framework.js', 'WebGL.data', 'WebGL.wasm'):
            write(self.dir, 'Build/' + name, 'abc')

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_complete(self):
        v = S.verify_build(self.dir)
        self.assertTrue(v['ok'])
        self.assertEqual(v['files']['wasm'], 'Build/WebGL.wasm')
        self.assertEqual(v['sizes']['data'], 3)

    def test_missing_and_compressed(self):
        os.remove(os.path.join(self.dir, 'Build', 'WebGL.wasm'))
        write(self.dir, 'Build/WebGL.data.gz')
        v = S.verify_build(self.dir)
        self.assertFalse(v['ok'])
        self.assertEqual(v['missing'], ['Build/*.wasm'])
        self.assertEqual(v['unexpected'], ['Build/WebGL.data.gz'])

    def test_nothing_there(self):
        v = S.verify_build(os.path.join(self.dir, 'nope'))
        self.assertFalse(v['ok'])
        self.assertIn('index.html', v['missing'])

    def test_intermediates(self):
        project = self.dir
        write(project, 'Library/Bee/artifacts/WebGL/il2cpp/a.cpp', 'x' * 5000)
        write(project, 'Library/Bee/artifacts/other/keep.txt')
        write(project, 'Library/ScriptAssemblies/Assembly-CSharp.dll')
        freed = S.delete_intermediates(project)
        self.assertEqual([rel for rel, _ in freed], [os.path.join('Library', 'Bee', 'artifacts', 'WebGL')])
        self.assertTrue(os.path.exists(os.path.join(project, 'Library/Bee/artifacts/other/keep.txt')))
        self.assertTrue(os.path.exists(os.path.join(project, 'Library/ScriptAssemblies/Assembly-CSharp.dll')))
        self.assertTrue(S.verify_build(project)['ok'])


class GuardTests(unittest.TestCase):
    def test_disk_guard(self):
        gb = S.GB
        self.assertIsNone(S.disk_guard(10 * gb, 2 * gb, 4 * gb, 6 * gb))
        self.assertIsNone(S.disk_guard(10 * gb, None, 4 * gb, 6 * gb))
        self.assertIn('free disk dropped', S.disk_guard(3 * gb, None, 4 * gb, 6 * gb))
        self.assertIn('past the', S.disk_guard(10 * gb, 7 * gb, 4 * gb, 6 * gb))

    def test_run_watched_stops_the_process_group(self):
        tmp = tempfile.mkdtemp(prefix='upm-tools-il2cpp-watch-')
        try:
            t0 = time.time()
            code, secs, reason, _, _ = S.run_watched(['/bin/sh', '-c', 'sleep 60 & sleep 60'], tmp, 600,
                                                     min_free=1 << 62, max_project=1 << 62, poll=0.2,
                                                     size_every=0.2)
            self.assertIsNone(code)
            self.assertIn('free disk dropped', reason)
            self.assertLess(time.time() - t0, 30)
        finally:
            shutil.rmtree(tmp)

    def test_run_watched_returns_the_exit_code(self):
        tmp = tempfile.mkdtemp(prefix='upm-tools-il2cpp-watch-')
        try:
            code, _, reason, _, _ = S.run_watched(['/bin/sh', '-c', 'exit 3'], tmp, 600, 0, 1 << 62, poll=0.1)
            self.assertEqual((code, reason), (3, None))
        finally:
            shutil.rmtree(tmp)

    def test_licence_lines(self):
        tmp = tempfile.mkdtemp(prefix='upm-tools-il2cpp-log-')
        try:
            log = write(tmp, 'editor.log', '[Licensing::Client] Error: Code 500 while processing request\n'
                                           'No valid Unity Editor license found. Please activate your license.\n'
                                           'Building WebGL...\n')
            self.assertEqual(S.licence_problems(log),
                             ['No valid Unity Editor license found. Please activate your license.'])
            self.assertEqual(S.licence_problems(os.path.join(tmp, 'missing.log')), [])
        finally:
            shutil.rmtree(tmp)

    def test_build_command(self):
        class U(object):
            executable = '/Unity'
        cmd = S.build_command(U(), '/p', '/p/log', 'high', '/p/Build/WebGL', '/p/r.json')
        self.assertIn('-nographics', cmd)
        self.assertEqual(cmd[cmd.index('-buildTarget') + 1], 'WebGL')
        self.assertEqual(cmd[cmd.index('-executeMethod') + 1], S.EXECUTE_METHOD)
        self.assertEqual(cmd[cmd.index('-smokeStripping') + 1], 'High')
        self.assertNotIn('-nographics', S.build_command(U(), '/p', '/l', 'medium', '/o', '/r', graphics=True))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp(prefix='upm-tools-il2cpp-serve-')
        self.build = os.path.join(self.project, S.BUILD_DIR)
        write(self.build, 'index.html', '<html>hi</html>')
        write(self.build, 'Build/WebGL.wasm', '\0asm')
        os.makedirs(os.path.join(self.project, S.LOGS))

    def tearDown(self):
        S.stop_server(self.project, quiet=True)
        shutil.rmtree(self.project)

    def test_serves_and_stops(self):
        port = S.free_port()
        server = S.start_server(self.build, port, os.path.join(self.project, S.LOGS, 'server.log'))
        url = 'http://127.0.0.1:%d/' % port
        with open(S.server_state_path(self.project), 'w') as f:
            json.dump({'pid': server.pid, 'port': port, 'url': url}, f)
        self.assertEqual(S.http_get(url + 'index.html')[0], 200)
        status, ctype, _ = S.http_get(url + 'Build/WebGL.wasm', method='HEAD')
        self.assertEqual((status, ctype), (200, 'application/wasm'))
        self.assertEqual(S.http_get(url + 'nope.js', method='HEAD')[0], 404)
        self.assertTrue(S.is_our_server(server.pid, port))
        self.assertFalse(S.is_our_server(server.pid, port + 1))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(S.stop_server(self.project))
        self.assertIn('stopped the server', out.getvalue())
        server.wait(timeout=10)
        self.assertIsNone(S.http_get(url, timeout=2)[0])
        self.assertFalse(os.path.exists(S.server_state_path(self.project)))


def chrome():
    return headless.find_chrome()


@unittest.skipUnless(chrome(), 'no Chrome or Chromium installed')
class HeadlessTests(unittest.TestCase):
    def test_reads_title_and_console(self):
        tmp = tempfile.mkdtemp(prefix='upm-tools-il2cpp-page-')
        try:
            write(tmp, 'index.html', '<!doctype html><title>loading</title><script>'
                                     'console.log("OPENUGD-IL2CPP check a: PASS");'
                                     'setTimeout(function(){console.log("OPENUGD-IL2CPP: PASS 1/1");'
                                     'document.title="OPENUGD-IL2CPP: PASS 1/1";},300);</script>')
            port = S.free_port()
            server = S.start_server(tmp, port, os.path.join(tmp, 'server.log'))
            try:
                page = headless.open_and_wait('http://127.0.0.1:%d/' % port, chrome(), S.SUMMARY_PREFIX, timeout=60)
            finally:
                server.terminate()
                server.wait(timeout=10)
            self.assertTrue(page['reached'])
            status, summary, checks, problems = S.judge_page(page)
            self.assertEqual((status, checks, problems), ('PASS', [('a', 'PASS', '')], []))
        finally:
            shutil.rmtree(tmp)


class EndToEndTests(unittest.TestCase):
    """main() against a fake editor and a one-package family; Unity is never started."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='upm-tools-il2cpp-e2e-')
        editor = os.path.join(self.tmp, 'editors', '6000.0.41f1')
        exe = os.path.join(editor, 'Unity.app', 'Contents', 'MacOS', 'Unity')
        os.makedirs(os.path.dirname(exe))
        shutil.copy(os.path.join(FIXTURE, 'fake_unity.py'), exe)
        os.chmod(exe, 0o755)
        os.makedirs(os.path.join(editor, 'PlaybackEngines', 'WebGLSupport'))
        self.editor = editor
        self.root = os.path.join(self.tmp, 'root')
        repo = os.path.join(self.root, 'upm-lifetime')
        write(repo, 'package.json', '{"name": "com.openugd.lifetime", "version": "2.0.0", "displayName": "Lifetime"}')
        write(repo, 'package.json.meta')
        for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q',
                                                     '-m', 'init']):
            subprocess.run(['git'] + args, cwd=repo, check=True, stdout=subprocess.DEVNULL)
        self.project = os.path.join(self.tmp, 'project')
        self.poll = S.WATCH_POLL
        S.WATCH_POLL = 0.2

    def tearDown(self):
        S.WATCH_POLL = self.poll
        S.stop_server(self.project, quiet=True)
        os.environ.pop('FAKE_UNITY_MODE', None)
        shutil.rmtree(self.tmp)

    def run_main(self, mode, *extra):
        os.environ['FAKE_UNITY_MODE'] = mode
        out = io.StringIO()
        with contextlib.redirect_stdout(out), warnings.catch_warnings():
            # --serve leaves the server running on purpose; its Popen object is dropped while the process lives.
            warnings.simplefilter('ignore', ResourceWarning)
            code = S.main(['--editor', self.editor, '--project', self.project, '--root', self.root,
                           '--packages', 'upm-lifetime', '--no-npm', '--min-free-gb', '0.1'] + list(extra))
        return code, out.getvalue()

    def report(self):
        with open(os.path.join(self.project, S.LOGS, 'il2cpp-smoke-report.json')) as f:
            return json.load(f)

    def test_licence_failure_is_a_tooling_problem(self):
        code, out = self.run_main('licence', '--no-check')
        self.assertEqual(code, 2, out)
        self.assertIn('the Unity licence is not valid', out)
        self.assertIn('No valid Unity Editor license found', out)
        self.assertNotIn('== Server', out)

    def test_compile_error_fails_the_gate(self):
        code, out = self.run_main('compile', '--no-check')
        self.assertEqual(code, 1, out)
        self.assertIn('the project has compile errors', out)
        self.assertIn("error CS0246", out)

    def test_built_and_served_without_a_browser(self):
        code, out = self.run_main('success', '--no-check')
        self.assertEqual(code, 0, out)
        self.assertIn('PLAYER NOT CHECKED', out)
        self.assertEqual([r[1] for r in self.report()['http']], [200] * 5)
        self.assertIn('server stopped', out)
        self.assertFalse(os.path.exists(S.server_state_path(self.project)))
        self.assertTrue(os.path.exists(os.path.join(self.project, 'Tarballs', 'com.openugd.lifetime-2.0.0.tgz')))
        with open(os.path.join(self.project, 'Packages', 'manifest.json')) as f:
            deps = json.load(f)['dependencies']
        self.assertEqual(deps['com.openugd.lifetime'], 'file:../Tarballs/com.openugd.lifetime-2.0.0.tgz')
        self.assertIn('com.unity.ugui', deps)
        # --no-build serves what is there; --serve leaves it running until --stop.
        code, out = self.run_main('success', '--no-build', '--no-check', '--serve')
        self.assertEqual(code, 0, out)
        state = S.read_result(S.server_state_path(self.project))
        self.assertEqual(S.http_get(state['url'] + 'index.html')[0], 200)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(S.main(['--project', self.project, '--stop']), 0)
        time.sleep(0.5)
        self.assertIsNone(S.http_get(state['url'], timeout=2)[0])

    def test_refuses_inside_git(self):
        code, out = self.run_main('success', '--project', os.path.join(self.root, 'upm-lifetime', 'proj'),
                                  '--no-check')
        self.assertEqual(code, 2)
        self.assertIn('inside the git work tree', out)

    def test_no_build_without_a_build(self):
        code, out = self.run_main('success', '--no-build', '--no-check')
        self.assertEqual(code, 2)
        self.assertIn('holds no complete build', out)

    @unittest.skipUnless(chrome(), 'no Chrome or Chromium installed')
    def test_player_pass_and_fail_read_from_the_page(self):
        code, out = self.run_main('success')
        self.assertEqual(code, 0, out)
        self.assertIn('IL2CPP SMOKE: PASS (OPENUGD-IL2CPP: PASS 2/2)', out)
        code, out = self.run_main('failpage')
        self.assertEqual(code, 1, out)
        self.assertIn('the player reported FAIL: lifetime-nesting', out)
        self.assertEqual(self.report()['page']['checks'][1][:2], ['lifetime-nesting', 'FAIL'])


def _editor():
    try:
        from unity import UnityInstall
        u = UnityInstall('6000.0.41f1')
        return u if os.path.exists(u.csc[1]) else None
    except SystemExit:
        return None


MONO_VTABLE = 'TypeLoadException: VTable setup of type'


@unittest.skipUnless(_editor(), 'the default editor (6000.0.41f1) is not installed')
class TemplateCompileTests(unittest.TestCase):
    """Without a Unity licence: the template's C# compiles against the checkouts' package sources (the runtime scripts
    as the WebGL player compiles them, the build script as the editor does), and the engine-free checks pass on desktop
    Mono, as compiled and after UnityLinker at Medium. That proves the checks' own expectations, so a FAIL from the
    IL2CPP player is about IL2CPP, not about the check."""

    WANTED = [('upm-lifetime', 'com.openugd.lifetime'), ('upm-signal', 'com.openugd.signal'),
              ('upm-context', 'com.openugd.context'), ('upm-corelib', 'com.openugd.logging'),
              ('upm-corelib', 'com.openugd.logging.unity'), ('upm-corelib', 'com.openugd.presenters'),
              ('upm-corelib', 'com.openugd.commands'), ('upm-corelib', 'com.openugd.corelib')]
    # Boot.cs checks that need the engine; the harness runs every other one.
    ENGINE_BOUND = {'player-il2cpp', 'log-unity-sink', 'inject-monobehaviour', 'presenter-view', 'context-behaviour'}

    @classmethod
    def setUpClass(cls):
        import linker_gate as G
        from common import family_root
        cls.G = G
        root = family_root()
        if not all(os.path.isfile(os.path.join(root, r, 'package.json')) for r, _ in cls.WANTED):
            raise unittest.SkipTest('package checkouts not found under %s' % root)
        cls.unity = _editor()
        cls.work = tempfile.mkdtemp(prefix='upm-tools-il2cpp-compile-')
        cls.defines, cfg = G.player_defines(cls.unity)
        cls.editor_defines = [d for d in cls.defines if d != 'ENABLE_IL2CPP'] + cfg['defines'].get('editor', [])
        cls.nowarn = cfg.get('noWarn', [])
        cls.ns = cls.unity.netstandard_refs()
        cls.dlls, cls.error = G.compile_family(cls.unity, cls.work, G.family_asmdefs(root, cls.WANTED), cls.ns,
                                               cls.defines, cls.nowarn)
        cls.refs = cls.ns + list(cls.dlls.values()) + cls.unity.engine_refs()
        cls.scripts = os.path.join(S.TEMPLATE, 'Assets', 'Il2CppSmoke')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def csc(self, name, target, sources, refs, defines):
        ok, out, text, errors = self.G.csc(self.unity, self.work, name, target, sources, refs, defines, self.nowarn)
        self.assertTrue(ok, '\n'.join(errors) or text)
        return out

    def test_compiles(self):
        self.assertIsNone(self.error)
        runtime = sorted(os.path.join(self.scripts, f) for f in os.listdir(self.scripts) if f.endswith('.cs'))
        webgl = [d for d in self.defines if 'STANDALONE' not in d] + ['UNITY_WEBGL', 'PLATFORM_WEBGL']
        self.csc('Assembly-CSharp', 'library', runtime, self.refs, webgl)
        game = self.csc('Assembly-CSharp.editor', 'library', runtime, self.refs, self.editor_defines)
        self.csc('Assembly-CSharp-Editor', 'library', [os.path.join(self.scripts, 'Editor', 'SmokeBuild.cs')],
                 self.refs + self.unity.editor_refs() + [game], self.editor_defines)

    def test_engine_free_checks_pass_on_mono_before_and_after_the_linker(self):
        self.assertIsNone(self.error)
        if not os.path.exists(self.unity.mono) or not os.path.exists(self.unity.linker):
            self.skipTest('the editor has no Mono or UnityLinker')
        G = self.G
        with open(BOOT, encoding='utf-8') as f:
            listed = re.findall(r'"([^"]+)"', re.search(r'Checks\s*=\s*\{(.*?)\};', f.read(), re.S).group(1))
        expected = [n for n in listed if n not in self.ENGINE_BOUND]
        sources = [os.path.join(FIXTURE, 'Harness.cs'), os.path.join(self.scripts, 'Services.cs'),
                   os.path.join(self.scripts, 'Checks.cs')]
        probe = self.csc('Probe', 'exe', sources, self.refs, self.defines)
        runs = {'as compiled': G.run_probe(self.unity, self.work, self.work, 'unstripped')}
        code, stripped, log, _ = G.link(self.unity, self.work, 'medium', probe, list(self.dlls.values()),
                                        G.player_engine_dir(self.unity), G.PROBE)
        self.assertEqual(code, 0, 'UnityLinker failed; see %s' % log)
        runs['after UnityLinker (Medium)'] = G.run_probe(self.unity, stripped, self.work, 'medium')
        for label, (rc, text, checks, failures) in runs.items():
            self.assertIn('DONE', text, '%s: %s' % (label, text))
            self.assertEqual(sorted(checks), sorted(expected), '%s: %s' % (label, text))
            failed = [n for n in expected if checks[n] != 'True']
            if label != 'as compiled':
                # UnityLinker in IL2CPP mode removes interface methods nothing calls (IEnumerator.Reset of the
                # iterator behind Context.Contracts, which every child build walks). IL2CPP fills such a vtable slot
                # with a stub; Mono refuses to load the type. Linked with --dotnetruntime=mono instead, Reset stays and
                # the check passes. So on Mono this failure says nothing about IL2CPP: the player decides.
                failed = [n for n in failed if not any(f.startswith(n + ': ') and MONO_VTABLE in f for f in failures)]
            self.assertEqual(failed, [], '%s: %s' % (label, failures))


if __name__ == '__main__':
    unittest.main()
