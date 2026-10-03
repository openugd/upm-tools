"""Unit tests for the OpenUPM-style packing (lib/pack.py), on a throwaway git repository."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lib'))

import pack as P  # noqa: E402


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def touch(root, rel, text='x'):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


class PackTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='upm-tools-pack-repo-')
        self.out = tempfile.mkdtemp(prefix='upm-tools-pack-out-')
        sh(self.root, 'git', 'init', '-q')
        touch(self.root, 'package.json', '{"name": "com.example.pkg", "version": "2.0.0", "displayName": "Pkg", '
                                         '"samples": [{"displayName": "Demo", "path": "Samples~/Demo"}]}')
        touch(self.root, 'package.json.meta')
        touch(self.root, 'Runtime.meta')
        touch(self.root, 'Runtime/A.cs', 'committed')
        touch(self.root, 'Runtime/A.cs.meta')
        touch(self.root, 'Samples~/Demo/Demo.cs')
        touch(self.root, 'Samples~/Demo/Demo.cs.meta')
        touch(self.root, 'Samples~/Demo/Notes.md')        # no .meta: reported for the sample, not an error
        touch(self.root, '.gitignore', '[Bb]uild/\n')
        sh(self.root, 'git', 'add', '-A')
        sh(self.root, 'git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'init')

    def tearDown(self):
        shutil.rmtree(self.root)
        shutil.rmtree(self.out)

    def test_packs_the_commit_only(self):
        touch(self.root, 'Runtime/A.cs', 'uncommitted edit')
        touch(self.root, 'Runtime/Untracked.cs')
        info = P.pack(self.root, self.out, use_npm=False)
        self.assertEqual(os.path.basename(info['tgz']), 'com.example.pkg-2.0.0.tgz')
        self.assertIn('npm not used', info['method'])     # --no-npm, not "npm not found"
        self.assertNotIn('Runtime/Untracked.cs', info['files'])
        self.assertIn('Runtime/A.cs', info['files'])
        self.assertFalse(any(f.startswith('.git/') for f in info['files']))
        self.assertEqual(len(info['dirty']), 2)
        self.assertEqual(info['dropped'], [])
        self.assertEqual(info['extra'], [])
        import tarfile
        with tarfile.open(info['tgz']) as t:
            body = t.extractfile('package/Runtime/A.cs').read().decode()
        self.assertEqual(body, 'committed')

    @unittest.skipUnless(shutil.which('npm'), 'npm not installed')
    def test_npm_rules_drop_tracked_files_and_are_reported(self):
        # A tracked folder that .gitignore names: a clone has it, but npm (no .npmignore) uses .gitignore and
        # leaves it out, so the published package would lack it.
        touch(self.root, 'Editor/Build/Tool.cs')
        sh(self.root, 'git', 'add', '-f', 'Editor/Build/Tool.cs')
        sh(self.root, 'git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'forced')
        info = P.pack(self.root, self.out)
        self.assertEqual(info['method'], 'npm pack')
        self.assertIn('Editor/Build/Tool.cs', info['dropped'])
        self.assertIn('.gitignore', info['dropped_hidden'])
        self.assertNotIn('.gitignore', info['dropped'])

    def test_extract_sample_reports_assets_without_meta(self):
        info = P.pack(self.root, self.out, use_npm=False)
        dst = os.path.join(self.out, 'Assets', 'Samples', 'Pkg', '2.0.0', 'Demo')
        files, no_meta = P.extract_sample(info['tgz'], 'Samples~/Demo', dst)
        self.assertEqual(sorted(files), ['Demo.cs', 'Demo.cs.meta', 'Notes.md'])
        self.assertEqual(no_meta, ['Notes.md'])
        self.assertTrue(os.path.isfile(os.path.join(dst, 'Demo.cs')))
        self.assertEqual(P.extract_sample(info['tgz'], 'Samples~/Missing', dst + '-x'), ([], []))


if __name__ == '__main__':
    unittest.main()
