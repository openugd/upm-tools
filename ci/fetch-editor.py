#!/usr/bin/env python3
"""Fetch the part of a macOS Unity editor that the gates read, without installing Unity or a licence.

The macOS editor installer (Unity-<version>.pkg, about 5 GB) is a xar archive whose Unity.pkg.tmp/Payload is
one gzip-compressed cpio stream, stored uncompressed in the xar heap. This script reads the xar table of contents
with an HTTP range request, then streams only that byte range through `gzip -dc | cpio -i` and keeps only the
paths the tools use (see SUBSET): the engine and editor reference DLLs, netstandard, the editor's Roslyn and .NET
runtime, Mono, UnityLinker, the built-in packages, the project-template script assemblies, the NUnit and Test
Framework tarballs and the module lists. That is about 2.3 GB on disk for 6000.0, against 8.7 GB for a full
install, and nothing of the 5 GB download is written to disk.

The result is <dest>/<version>/Unity.app, the layout the tools expect under the Hub editors folder. Point
$OPENUGD_UNITY_EDITORS at <dest> (the tools' own tests need that), or pass the app to level1.sh --unity or
linker-gate.sh --editor. level2.sh and il2cpp-smoke.sh need a real install and a licence; this is not one.

    python3 ci/fetch-editor.py --version 6000.0.41f1 --changeset 46e447368a18 --dest "$RUNNER_TEMP/unity-editors"
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zlib

URL = 'https://download.unity3d.com/download_unity/{changeset}/{installer}/Unity-{version}.pkg'
INSTALLERS = {'arm64': 'MacEditorInstallerArm64', 'x86_64': 'MacEditorInstaller'}
PAYLOAD = 'Unity.pkg.tmp/Payload'
APP = './Unity/Unity.app/Contents/'

# Paths inside Unity.app/Contents that the tools read (lib/unity.py, lib/modules.py, lib/linker_gate.py). The
# scripting folders sit in Contents in 6000.0 and in Contents/Resources/Scripting from 6000.3; both are listed.
# bsdcpio patterns: '*' also matches '/'.
SCRIPTING = ['Managed/', 'NetStandard/', 'NetCoreRuntime/', 'DotNetSdkRoslyn/', 'MonoBleedingEdge/', 'il2cpp/']
SUBSET = (['Info.plist',
           'Resources/modules.asset',
           'Resources/editor_modules.asset',
           'Resources/PackageManager/BuiltInPackages/',
           'Resources/PackageManager/Editor/com.unity.ext.nunit-*',
           # Manifests of the non-built-in packages the module check walks (test-framework, its performance
           # add-on). A family dependency on another non-built-in Unity package needs its tarball added here:
           # without it level 1 notes that the editor ships no manifest and does not count that package's modules.
           'Resources/PackageManager/Editor/com.unity.test-framework*',
           'Resources/PackageManager/ProjectTemplates/libcache/*/ScriptAssemblies/',
           'PlaybackEngines/MacStandaloneSupport/Variations/mono/Managed/']
          + SCRIPTING + ['Resources/Scripting/' + s for s in SCRIPTING])

# Present after a good extraction, for both layouts (the first existing alternative counts).
SENTINELS = [
    ['Info.plist'],
    ['Managed/UnityEngine/UnityEngine.CoreModule.dll',
     'Resources/Scripting/Managed/UnityEngine/UnityEngine.CoreModule.dll'],
    ['NetStandard/ref/2.1.0/netstandard.dll', 'Resources/Scripting/NetStandard/ref/2.1.0/netstandard.dll'],
    ['DotNetSdkRoslyn/csc.dll', 'Resources/Scripting/DotNetSdkRoslyn/csc.dll'],
    ['NetCoreRuntime/dotnet', 'Resources/Scripting/NetCoreRuntime/dotnet'],
    ['MonoBleedingEdge/bin/mono', 'Resources/Scripting/MonoBleedingEdge/bin/mono'],
    ['il2cpp/build/deploy/UnityLinker', 'Resources/Scripting/il2cpp/build/deploy/UnityLinker'],
    ['Resources/PackageManager/BuiltInPackages/com.unity.ugui/package.json'],
    ['Resources/modules.asset'],
]


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='fetch-editor.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', required=True, help='editor version, e.g. 6000.0.41f1')
    ap.add_argument('--changeset', required=True,
                    help='the version\'s changeset (from the Unity Hub deep link, or UnityBuildNumber in an installed '
                         'Unity.app/Contents/Info.plist)')
    ap.add_argument('--dest', required=True, help='editors folder; the editor goes to <dest>/<version>/Unity.app')
    ap.add_argument('--arch', default='arm64' if os.uname().machine == 'arm64' else 'x86_64',
                    choices=sorted(INSTALLERS), help='installer architecture (default: this machine\'s)')
    ap.add_argument('--force', action='store_true', help='replace an existing <dest>/<version>')
    ap.add_argument('--attempts', type=int, default=3, help='tries of the streamed extraction (default 3)')
    return ap.parse_args(argv)


def fetch_range(url, start, end):
    req = urllib.request.Request(url, headers={'Range': 'bytes=%d-%d' % (start, end)})
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status != 206:
            raise SystemExit('%s: the server ignored the Range header (HTTP %d)' % (url, r.status))
        return r.read()


def payload_range(url):
    """(first byte, last byte) of Unity.pkg.tmp/Payload in the xar file, from its table of contents."""
    head = fetch_range(url, 0, 27)
    magic, header_size, _version, toc_len, _toc_raw, _alg = struct.unpack('>4sHHQQI', head)
    if magic != b'xar!':
        raise SystemExit('%s is not a xar archive' % url)
    toc = zlib.decompress(fetch_range(url, header_size, header_size + toc_len - 1))
    heap = header_size + toc_len

    def walk(element, prefix):
        for f in element.findall('file'):
            path = prefix + f.findtext('name')
            data = f.find('data')
            if path == PAYLOAD and data is not None:
                return data
            found = walk(f, path + '/')
            if found is not None:
                return found
        return None

    data = walk(ET.fromstring(toc).find('toc'), '')
    if data is None:
        raise SystemExit('%s has no %s' % (url, PAYLOAD))
    style = data.find('encoding').get('style')
    if style != 'application/octet-stream':
        raise SystemExit('%s: %s is stored as %s, expected a raw gzip stream' % (url, PAYLOAD, style))
    offset, length = int(data.findtext('offset')), int(data.findtext('length'))
    return heap + offset, heap + offset + length - 1, length


class ExtractError(Exception):
    pass


def patterns():
    """cpio patterns for SUBSET: a folder ('.../') takes everything under it."""
    return [APP + p + ('*' if p.endswith('/') else '') for p in SUBSET]


def extract(url, start, end, workdir):
    # No curl --retry: a retry restarts the range while gzip has already read part of it. main() retries the whole
    # extraction instead, in a fresh folder. A stalled transfer (under 100 KB/s for 2 minutes) fails, so it is retried.
    curl = subprocess.Popen(['curl', '-sSfL', '--connect-timeout', '30', '--speed-limit', '102400',
                             '--speed-time', '120', '-r', '%d-%d' % (start, end), url], stdout=subprocess.PIPE)
    gunzip = subprocess.Popen(['gzip', '-dc'], stdin=curl.stdout, stdout=subprocess.PIPE)
    curl.stdout.close()
    cpio = subprocess.Popen(['cpio', '-idm', '--quiet'] + patterns(), stdin=gunzip.stdout, cwd=workdir)
    gunzip.stdout.close()
    codes = (cpio.wait(), gunzip.wait(), curl.wait())
    if any(codes):
        raise ExtractError('cpio %d, gzip %d, curl %d' % codes)


def main(argv):
    a = parse_args(argv)
    if a.attempts < 1:
        raise SystemExit('--attempts must be at least 1')
    url = URL.format(changeset=a.changeset, installer=INSTALLERS[a.arch], version=a.version)
    target = os.path.join(os.path.abspath(a.dest), a.version)
    if os.path.exists(target):
        if not a.force:
            print('%s exists; nothing to do (--force replaces it)' % target)
            return 0
        shutil.rmtree(target)
    start, end, length = payload_range(url)
    print('fetch-editor: %s\n  payload bytes %d-%d (%.1f GB, streamed; only the subset is written)'
          % (url, start, end, length / 1e9), flush=True)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    for attempt in range(1, a.attempts + 1):
        work = tempfile.mkdtemp(prefix='fetch-editor-', dir=os.path.dirname(target))
        try:
            extract(url, start, end, work)
            break
        except ExtractError as e:
            shutil.rmtree(work, ignore_errors=True)
            if attempt == a.attempts:
                raise SystemExit('extraction failed %d time(s); last: %s' % (attempt, e))
            print('fetch-editor: attempt %d failed (%s); starting over' % (attempt, e), flush=True)
        except BaseException:   # interrupted, or curl/gzip/cpio could not start: leave no partial folder behind
            shutil.rmtree(work, ignore_errors=True)
            raise
    try:
        app = os.path.join(work, 'Unity', 'Unity.app')
        contents = os.path.join(app, 'Contents')
        missing = [alts[0] for alts in SENTINELS if not any(os.path.exists(os.path.join(contents, p)) for p in alts)]
        if missing:
            raise SystemExit('the extracted editor lacks: %s' % ', '.join(missing))
        os.makedirs(target)
        os.rename(app, os.path.join(target, 'Unity.app'))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(target) for f in fs
               if not os.path.islink(os.path.join(d, f)))
    print('fetch-editor: %s/Unity.app (%.1f GB)' % (target, size / 1e9))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
