"""Unit tests for release-check.sh (lib/release_check.py), on a throwaway family of two or three git repositories."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lib'))

import release_check as R  # noqa: E402

META = 'fileFormatVersion: 2\nguid: %s\n'

README = '''# {title}

## Install

```sh
openupm add {name}@{version}
```

```json
{{
  "dependencies": {{
    "{name}": "{version}"
  }}
}}
```

### Git URL

```json
{{
  "dependencies": {{
{git}
  }}
}}
```

## Quick start
'''


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


def read(root, rel):
    with open(os.path.join(root, rel)) as f:
        return f.read()


def commit(repo, message='c'):
    sh(repo, 'git', 'add', '-A')
    sh(repo, 'git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', message)


def make_package(root, repo, name, version, deps, git_lines):
    path = os.path.join(root, repo)
    os.makedirs(path)
    sh(path, 'git', 'init', '-q')
    write(path, 'package.json', json.dumps({
        'name': name, 'version': version, 'unity': '6000.0', 'dependencies': deps,
        'repository': {'type': 'git', 'url': 'https://github.com/openugd/%s.git' % repo}}, indent=2))
    write(path, 'README.md', README.format(title=repo, name=name, version=version,
                                           git=',\n'.join('    ' + l for l in git_lines)))
    write(path, 'CHANGELOG.md', '# Changelog\n\n## [Unreleased]\n\n### Added\n- x\n\n## [1.0.0] - 2020-01-01\n')
    for rel in ('package.json', 'README.md', 'CHANGELOG.md', 'Runtime', 'Runtime/A.cs'):
        write(path, rel + '.meta', META % hashlib.md5((repo + rel).encode()).hexdigest())
    write(path, 'Runtime/A.cs', 'class A {}\n')
    write(path, '.gitignore', 'Library/\n')
    commit(path)
    return path


class ReleaseCheckTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='upm-tools-release-')
        self.lifetime = make_package(self.root, 'upm-lifetime', 'com.openugd.lifetime', '2.0.0', {}, [
            '"com.openugd.lifetime": "https://github.com/openugd/upm-lifetime.git#2.0.0"'])
        self.signal = make_package(self.root, 'upm-signal', 'com.openugd.signal', '2.0.0',
                                   {'com.openugd.lifetime': '2.0.0'}, [
            '"com.openugd.lifetime": "https://github.com/openugd/upm-lifetime.git#2.0.0"',
            '"com.openugd.signal": "https://github.com/openugd/upm-signal.git#2.0.0"'])

    def tearDown(self):
        shutil.rmtree(self.root)

    def run_check(self, *extra):
        out = os.path.join(self.root, 'report.json')
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            code = R.main(['--root', self.root, '--packages', 'upm-lifetime,upm-signal', '--version', '2.0.0',
                           '--json', out] + list(extra))      # a later --packages in extra wins
        with open(out) as f:
            return code, {r['repo']: r for r in json.load(f)}, text.getvalue()

    def test_ready_family_prints_tag_commands_in_layers(self):
        code, res, text = self.run_check()
        self.assertEqual(code, 0, text)
        self.assertTrue(all(r['ok'] for r in res.values()), text)
        self.assertIn('git -C %s tag -a 2.0.0 -m "com.openugd.lifetime 2.0.0" %s' % (
            self.lifetime, res['upm-lifetime']['sha']), text)
        self.assertLess(text.index('# layer 1'), text.index('upm-lifetime tag'))
        self.assertLess(text.index('# layer 2'), text.index('upm-signal tag'))
        self.assertIn('https://package.openupm.com/com.openugd.lifetime', text)

    def test_version_mismatch_is_refused(self):
        write(self.signal, 'package.json', read(self.signal, 'package.json')
              .replace('"version": "2.0.0"', '"version": "1.9.0"'))
        commit(self.signal)
        code, res, text = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(res['upm-signal']['checks']['version'])
        self.assertIn('# REFUSED upm-signal: the tag 2.0.0 would not equal package.json version 1.9.0', text)
        self.assertNotIn('upm-signal tag -a', text)

    def test_existing_tag_elsewhere_is_refused(self):
        sh(self.lifetime, 'git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'tag', '-a', '2.0.0', '-m', 'old')
        write(self.lifetime, 'Runtime/A.cs', 'class A { }\n')
        commit(self.lifetime)
        code, res, text = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(res['upm-lifetime']['checks']['tag'])
        # Its dependent is blocked, not tagged on top of a package that cannot be released.
        self.assertIn('# BLOCKED (dependency com.openugd.lifetime not ready) git -C %s tag' % self.signal, text)

    def test_blocking_follows_dependencies_transitively(self):
        # context -> signal -> lifetime: when lifetime cannot be tagged, signal is blocked by it, and so is context,
        # although its only direct dependency has no finding of its own.
        context = make_package(self.root, 'upm-context', 'com.openugd.context', '2.0.0',
                               {'com.openugd.signal': '2.0.0'}, [
            '"com.openugd.lifetime": "https://github.com/openugd/upm-lifetime.git#2.0.0"',
            '"com.openugd.signal": "https://github.com/openugd/upm-signal.git#2.0.0"',
            '"com.openugd.context": "https://github.com/openugd/upm-context.git#2.0.0"'])
        write(self.lifetime, 'Runtime/Dirty.cs', 'class D {}\n')
        code, res, text = self.run_check('--packages', 'upm-lifetime,upm-signal,upm-context')
        self.assertEqual(code, 1)
        self.assertTrue(res['upm-signal']['ok'] and res['upm-context']['ok'], text)
        self.assertFalse(res['upm-context']['ready'])
        self.assertIn('# BLOCKED (dependency com.openugd.lifetime not ready) git -C %s tag' % self.signal, text)
        self.assertIn('# BLOCKED (dependency com.openugd.signal not ready) git -C %s tag' % context, text)

    def test_readme_must_pin_every_form_and_list_dependencies(self):
        text = read(self.signal, 'README.md')
        text = text.replace('openupm add com.openugd.signal@2.0.0', 'openupm add com.openugd.signal')
        text = text.replace('upm-signal.git#2.0.0', 'upm-signal.git')
        text = text.replace('    "com.openugd.lifetime": "https://github.com/openugd/upm-lifetime.git#2.0.0",\n', '')
        write(self.signal, 'README.md', text)
        commit(self.signal)
        code, res, out = self.run_check()
        problems = ' | '.join(res['upm-signal']['checks']['readme'])
        self.assertEqual(code, 1)
        self.assertIn('does not pin a version', problems)
        self.assertIn('is not pinned (no #2.0.0)', problems)
        self.assertIn('does not list com.openugd.lifetime', problems)

    def test_changelog_and_dependency_minimum(self):
        write(self.signal, 'CHANGELOG.md', '# Changelog\n\n## [Unreleased]\n\n## [2.0.0]\n- x\n')
        pj = json.loads(read(self.signal, 'package.json'))
        pj['dependencies']['com.openugd.lifetime'] = '1.2.0'
        write(self.signal, 'package.json', json.dumps(pj))
        commit(self.signal)
        code, res, _ = self.run_check()
        checks = res['upm-signal']['checks']
        self.assertIn('one release, one section', checks['changelog'][0])
        self.assertIn('not a 2.x minimum', checks['family deps'][0])

    def test_meta_reads_head_and_clean_sees_the_worktree(self):
        write(self.lifetime, 'Runtime/B.cs', 'class B {}\n')
        commit(self.lifetime)                                   # committed without its .meta
        write(self.lifetime, 'Runtime/C.cs', 'class C {}\n')    # untracked: not in HEAD, but not clean
        code, res, _ = self.run_check()
        checks = res['upm-lifetime']['checks']
        self.assertEqual(checks['meta'], ['MISSING-IN-HEAD Runtime/B.cs.meta'])
        self.assertTrue(checks['clean'])


class InstallSectionTests(unittest.TestCase):
    def test_section_ends_at_same_level_heading_and_ignores_fenced_hashes(self):
        text = '# T\n\n## Install\n\n```sh\n# not a heading\nopenupm add x@1.0.0\n```\n\n### Git\n\nmore\n\n## Next\nno\n'
        line, sec = R.install_section(text)
        self.assertEqual(line, 3)
        self.assertIn('### Git', sec)
        self.assertIn('openupm add x@1.0.0', sec)
        self.assertNotIn('## Next', sec)

    def test_no_section(self):
        self.assertEqual(R.install_section('# T\n\n## Usage\n'), (None, None))


if __name__ == '__main__':
    unittest.main()
