"""Unit tests for the .meta check, on a throwaway git repository."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lib'))

import meta as M  # noqa: E402


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def touch(root, rel, text=''):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


def meta(guid):
    return 'fileFormatVersion: 2\nguid: %s\n' % guid


class MetaTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='upm-tools-meta-')
        sh(self.root, 'git', 'init', '-q')
        touch(self.root, 'package.json', '{}')
        touch(self.root, 'package.json.meta', meta('a' * 32))
        touch(self.root, 'Runtime.meta', meta('b' * 32))
        touch(self.root, 'Runtime/A.cs')
        touch(self.root, 'Runtime/A.cs.meta', meta('c' * 32))
        touch(self.root, 'Samples~/S/S.cs')          # hidden from Unity: needs no .meta
        touch(self.root, '.github/workflow.yml')      # hidden
        sh(self.root, 'git', 'add', '-A')

    def tearDown(self):
        shutil.rmtree(self.root)

    def problems(self):
        return sorted(M.check_package(self.root)[0])

    def test_clean_package(self):
        self.assertEqual(self.problems(), [])

    def test_missing_and_orphan(self):
        touch(self.root, 'Runtime/B.cs')
        touch(self.root, 'Runtime/Gone.cs.meta', meta('d' * 32))
        sh(self.root, 'git', 'add', '-A')
        self.assertEqual(self.problems(), [('MISSING', 'Runtime/B.cs.meta'), ('ORPHAN', 'Runtime/Gone.cs.meta')])

    def test_untracked_meta(self):
        touch(self.root, 'Runtime/C.cs')
        touch(self.root, 'Runtime/C.cs.meta', meta('e' * 32))
        sh(self.root, 'git', 'add', 'Runtime/C.cs')
        # Reported once: the git-side MISSING-IN-GIT for the same path is not repeated.
        self.assertEqual(self.problems(), [('UNTRACKED-META', 'Runtime/C.cs.meta')])

    def test_folder_meta_for_empty_folder_is_orphan_in_git(self):
        touch(self.root, 'Editor.meta', meta('f' * 32))
        os.makedirs(os.path.join(self.root, 'Editor'))
        sh(self.root, 'git', 'add', '-A')
        self.assertEqual(self.problems(), [('ORPHAN-IN-GIT', 'Editor.meta')])

    def test_duplicate_guid(self):
        other = tempfile.mkdtemp(prefix='upm-tools-meta2-')
        try:
            sh(other, 'git', 'init', '-q')
            touch(other, 'package.json', '{}')
            touch(other, 'package.json.meta', meta('a' * 32))
            sh(other, 'git', 'add', '-A')
            res = M.check_family([self.root, other])
            kinds = [k for problems in res.values() for k, _ in problems]
            self.assertEqual(kinds.count('DUPLICATE-GUID'), 2)
        finally:
            shutil.rmtree(other)


if __name__ == '__main__':
    unittest.main()
