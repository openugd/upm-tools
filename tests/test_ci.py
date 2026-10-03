"""Unit tests for the CI helpers: ci/fetch-editor.py (xar table of contents, cpio subset) and ci/clone-packages.py."""
import contextlib
import gzip
import importlib.util
import io
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), os.path.join(TOOLS, 'ci', name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


F = load('fetch-editor')
C = load('clone-packages')


def xar(files, heap_prefix=b''):
    """A minimal xar archive: header, zlib-compressed TOC, heap. files: (path, offset, length, encoding)."""
    def entry(i, name, children, data):
        d = ''
        if data:
            offset, length, encoding = data
            d = ('<data><offset>%d</offset><length>%d</length><size>%d</size><encoding style="%s"/></data>'
                 % (offset, length, length, encoding))
        return '<file id="%d"><name>%s</name>%s%s</file>' % (i, name, d, children)

    tree = {}
    for path, offset, length, encoding in files:
        node = tree
        parts = path.split('/')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = (offset, length, encoding)

    counter = [0]

    def render(node):
        out = ''
        for name, value in node.items():
            counter[0] += 1
            if isinstance(value, dict):
                out += entry(counter[0], name, render(value), None)
            else:
                out += entry(counter[0], name, '', value)
        return out

    toc = ('<?xml version="1.0" encoding="UTF-8"?><xar><toc>%s</toc></xar>' % render(tree)).encode()
    packed = zlib.compress(toc)
    header = struct.pack('>4sHHQQI', b'xar!', 28, 1, len(packed), len(toc), 1)
    return header + packed + heap_prefix


class PayloadRangeTests(unittest.TestCase):
    def serve(self, blob):
        F.fetch_range = lambda url, start, end: blob[start:end + 1]

    def tearDown(self):
        F.fetch_range = self._fetch

    def setUp(self):
        self._fetch = F.fetch_range

    def test_finds_the_nested_payload_and_adds_the_heap_offset(self):
        blob = xar([('Distribution', 0, 10, 'application/x-gzip'),
                    ('Unity.pkg.tmp/Bom', 10, 20, 'application/x-gzip'),
                    ('Unity.pkg.tmp/Payload', 30, 1000, 'application/octet-stream')])
        self.serve(blob)
        header_size, toc_len = 28, struct.unpack('>Q', blob[8:16])[0]
        start, end, length = F.payload_range('https://example/Unity.pkg')
        self.assertEqual((start, end, length), (header_size + toc_len + 30, header_size + toc_len + 1029, 1000))

    def test_refuses_a_compressed_payload_entry(self):
        self.serve(xar([('Unity.pkg.tmp/Payload', 0, 10, 'application/x-gzip')]))
        with self.assertRaises(SystemExit):
            F.payload_range('https://example/Unity.pkg')

    def test_refuses_an_archive_without_payload(self):
        self.serve(xar([('Distribution', 0, 10, 'application/x-gzip')]))
        with self.assertRaises(SystemExit):
            F.payload_range('https://example/Unity.pkg')

    def test_refuses_what_is_not_xar(self):
        self.serve(b'PK\x03\x04' + b'\0' * 64)
        with self.assertRaises(SystemExit):
            F.payload_range('https://example/Unity.pkg')


class PatternTests(unittest.TestCase):
    def test_folders_take_their_whole_subtree_and_files_stay_exact(self):
        p = F.patterns()
        self.assertTrue(all(x.startswith('./Unity/Unity.app/Contents/') for x in p))
        self.assertIn('./Unity/Unity.app/Contents/Managed/*', p)
        self.assertIn('./Unity/Unity.app/Contents/Resources/Scripting/Managed/*', p)
        self.assertIn('./Unity/Unity.app/Contents/Info.plist', p)
        self.assertIn('./Unity/Unity.app/Contents/Resources/PackageManager/Editor/com.unity.ext.nunit-*', p)


@unittest.skipUnless(shutil.which('cpio') and shutil.which('curl') and shutil.which('gzip'), 'needs cpio, curl, gzip')
class ExtractTests(unittest.TestCase):
    """The real pipeline (curl -r | gzip -dc | cpio -i) on a fake installer: a gzip cpio stream between junk."""

    KEEP = ['Info.plist',
            'Managed/UnityEngine/UnityEngine.CoreModule.dll',
            'Resources/PackageManager/ProjectTemplates/libcache/com.unity.template.x/ScriptAssemblies/A.dll',
            'Resources/PackageManager/Editor/com.unity.ext.nunit-2.0.5.tgz',
            'Resources/PackageManager/BuiltInPackages/com.unity.ugui/package.json',
            'Resources/Scripting/il2cpp/build/deploy/UnityLinker']
    DROP = ['PlaybackEngines/MacStandaloneSupport/Whatever.dll',
            'Resources/PackageManager/ProjectTemplates/libcache/com.unity.template.x/Other/B.dll',
            'Resources/PackageManager/Editor/com.unity.other-1.0.0.tgz',
            'Frameworks/Big.framework/Big',
            'MacOS/Unity']

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='upm-tools-ci-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_streams_the_range_and_keeps_only_the_subset(self):
        src = os.path.join(self.tmp, 'src')
        for rel in self.KEEP + self.DROP:
            path = os.path.join(src, 'Unity', 'Unity.app', 'Contents', rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(rel)
        listing = subprocess.check_output(['find', './Unity'], cwd=src)
        archive = subprocess.run(['cpio', '-o', '--quiet'], cwd=src, input=listing, stdout=subprocess.PIPE,
                                 check=True).stdout
        payload = gzip.compress(archive)
        prefix, suffix = b'x' * 777, b'y' * 333
        pkg = os.path.join(self.tmp, 'Unity.pkg')
        with open(pkg, 'wb') as f:
            f.write(prefix + payload + suffix)
        work = os.path.join(self.tmp, 'work')
        os.makedirs(work)
        F.extract('file://' + pkg, len(prefix), len(prefix) + len(payload) - 1, work)
        contents = os.path.join(work, 'Unity', 'Unity.app', 'Contents')
        for rel in self.KEEP:
            self.assertTrue(os.path.isfile(os.path.join(contents, rel)), rel)
        for rel in self.DROP:
            self.assertFalse(os.path.exists(os.path.join(contents, rel)), rel)


class ClonePackagesTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._call, self._out = C.subprocess.call, C.subprocess.check_output
        C.subprocess.call = lambda cmd: self.calls.append(cmd) or 0
        C.subprocess.check_output = lambda cmd, text=True: 'abc1234 subject'
        self.dest = tempfile.mkdtemp(prefix='upm-tools-clone-')

    def tearDown(self):
        C.subprocess.call, C.subprocess.check_output = self._call, self._out
        shutil.rmtree(self.dest, ignore_errors=True)

    def commands(self, repo):
        """The git subcommands run for one repo, in order (clone, or init/remote/fetch/checkout)."""
        target = os.path.join(self.dest, repo)
        out = []
        for c in self.calls:
            if c[1] == 'clone' and c[-1] == target:
                out.append(('clone', c))
            elif c[1] == 'init' and c[-1] == target:
                out.append(('init', c))
            elif c[1] == '-C' and c[2] == target:
                out.append((next(x for x in c[3:] if not x.startswith('-') and '=' not in x), c))
        return out

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            C.main(['--dest', self.dest] + list(args))
        return out.getvalue()

    def test_clones_the_train_and_the_repos_outside_it_shallow_from_the_org(self):
        self.run_main()
        for repo in ('upm-lifetime', 'upm-configuration'):
            (kind, cmd), = self.commands(repo)
            self.assertEqual(kind, 'clone')
            self.assertIn('--depth', cmd)
            self.assertEqual(cmd[-2], 'https://github.com/openugd/%s.git' % repo)

    def test_ref_is_fetched_into_the_train_only(self):
        self.run_main('--ref', '2.0.0')
        kinds = [k for k, _ in self.commands('upm-lifetime')]
        self.assertEqual(kinds, ['init', 'remote', 'fetch', 'checkout'])
        fetch = dict(self.commands('upm-lifetime'))['fetch']
        self.assertEqual(fetch[-2:], ['origin', '2.0.0'])
        self.assertIn('--depth', fetch)
        self.assertEqual([k for k, _ in self.commands('upm-configuration')], ['clone'])

    def test_train_only_skips_the_repos_outside_it(self):
        self.run_main('--train-only')
        self.assertEqual(self.commands('upm-configuration'), [])
        self.assertTrue(self.commands('upm-ui'))

    def test_an_existing_clone_moves_to_the_ref_and_otherwise_stays(self):
        os.makedirs(os.path.join(self.dest, 'upm-lifetime', '.git'))
        printed = self.run_main('--ref', '2.0.0', '--train-only')
        self.assertEqual([k for k, _ in self.commands('upm-lifetime')], ['fetch', 'checkout'])
        self.assertIn('moved to 2.0.0', printed)
        self.calls[:] = []
        printed = self.run_main('--train-only')
        self.assertEqual(self.commands('upm-lifetime'), [])
        self.assertIn('not updated', printed)

if __name__ == '__main__':
    unittest.main()
