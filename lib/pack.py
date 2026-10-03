"""Pack a package checkout the way OpenUPM publishes a tag: the committed tree of HEAD, packed by npm.

OpenUPM clones the repository at the tag and publishes it with npm, so a package contains exactly what git tracks
at that commit, minus what npm's own rules leave out (.npmignore, or .gitignore when there is no .npmignore, the
"files" field of package.json, and npm's fixed exclusions). This module reproduces that without a tag:

  1. export HEAD with `git archive` into a scratch folder: tracked files only, never .git, never anything
     untracked or uncommitted;
  2. pack that folder with `npm pack` when npm is installed, so npm's rules apply; otherwise write the tarball
     itself, every exported file under the package/ prefix, and say so;
  3. compare the tarball with the commit: files git tracks that the tarball lacks would not be published (dot-names
     aside: npm always leaves out .gitignore and .npmignore, and Unity never imports a dot-name).

Nothing here writes to the checkout.
"""
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile


def _git(repo, *args):
    p = subprocess.run(['git', '-C', repo] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError('git %s failed in %s: %s' % (' '.join(args), repo,
                                                        p.stderr.decode('utf-8', 'replace').strip()))
    return p.stdout


def head_files(repo):
    """Repo-relative paths of every file in HEAD's tree (what a clone of the commit contains)."""
    out = _git(repo, 'ls-tree', '-r', '-z', '--name-only', 'HEAD').decode('utf-8')
    return [x for x in out.split('\0') if x]


def _attribute_notes(export_dir):
    """git archive honours export-ignore/export-subst; a clone (what OpenUPM uses) does not. LFS pointers are not
    the files. Report any of that rather than silently differing from a clone."""
    notes = []
    for d, dirs, files in os.walk(export_dir):
        if '.gitattributes' in files:
            with open(os.path.join(d, '.gitattributes'), encoding='utf-8', errors='replace') as f:
                text = f.read()
            rel = os.path.relpath(os.path.join(d, '.gitattributes'), export_dir)
            for word, why in (('export-ignore', 'git archive dropped files a clone would have'),
                              ('export-subst', 'git archive rewrote files a clone would not'),
                              ('filter=lfs', 'LFS files are pointers in this export')):
                if word in text:
                    notes.append('%s uses %s: %s' % (rel, word, why))
    return notes


def _npm():
    return shutil.which('npm')


def _pack_with_npm(npm, src, dest):
    env = dict(os.environ, npm_config_update_notifier='false', npm_config_fund='false', npm_config_audit='false')
    p = subprocess.run([npm, 'pack', '--json', '--ignore-scripts', '--pack-destination', dest], cwd=src,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if p.returncode != 0:
        raise RuntimeError('npm pack failed: %s' % p.stderr.decode('utf-8', 'replace').strip()[-2000:])
    data = json.loads(p.stdout.decode('utf-8'))
    entry = data[0] if isinstance(data, list) else data
    warnings = [l.strip() for l in p.stderr.decode('utf-8', 'replace').splitlines()
                if l.strip().startswith('npm WARN')]
    return os.path.join(dest, entry['filename']), warnings


def _pack_with_tarfile(src, path):
    """A gzip tarball with every file under package/, as npm lays it out."""
    with tarfile.open(path, 'w:gz') as t:
        for d, dirs, files in os.walk(src):
            dirs.sort()
            for f in sorted(files):
                full = os.path.join(d, f)
                info = t.gettarinfo(full, 'package/' + os.path.relpath(full, src).replace(os.sep, '/'))
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                with open(full, 'rb') as fh:
                    t.addfile(info, fh)
    return path


def tarball_files(path):
    """Paths inside the tarball, relative to its package/ folder (files only)."""
    with tarfile.open(path, 'r:gz') as t:
        names = [m.name for m in t.getmembers() if m.isfile()]
    out = []
    for n in names:
        if not n.startswith('package/'):
            raise RuntimeError('%s: entry %s is not under package/' % (path, n))
        out.append(n[len('package/'):])
    return sorted(out)


def pack(repo, dest, use_npm=True):
    """Pack the HEAD of `repo` into `dest`/<name>-<version>.tgz. Returns a dict describing what was packed."""
    os.makedirs(dest, exist_ok=True)
    sha = _git(repo, 'rev-parse', 'HEAD').decode().strip()
    dirty = [l for l in _git(repo, 'status', '--porcelain', '--untracked-files=normal').decode(
        'utf-8', 'replace').splitlines() if l.strip()]
    tracked = head_files(repo)
    pj = json.loads(_git(repo, 'show', 'HEAD:package.json').decode('utf-8-sig'))
    name, version = pj['name'], pj['version']
    scratch = tempfile.mkdtemp(prefix='upm-tools-pack-')
    try:
        export = os.path.join(scratch, 'package')
        os.makedirs(export)
        archive = _git(repo, 'archive', '--format=tar', 'HEAD')
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as t:
            if hasattr(tarfile, 'data_filter'):
                t.extractall(export, filter='data')
            else:
                t.extractall(export)
        notes = _attribute_notes(export)
        npm = _npm() if use_npm else None
        final = os.path.join(dest, '%s-%s.tgz' % (name, version))
        if os.path.exists(final):
            os.remove(final)
        if npm:
            produced, warnings = _pack_with_npm(npm, export, scratch)
            shutil.move(produced, final)
            method = 'npm pack'
            notes += warnings
        else:
            _pack_with_tarfile(export, final)
            method = 'git archive (npm not found: npm\'s ignore rules not applied)'
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    files = tarball_files(final)
    in_tar = set(files)
    # npm always leaves out .gitignore/.npmignore, and Unity never imports a dot-name: only the rest matters.
    missing = [f for f in tracked if f not in in_tar]
    hidden = lambda f: any(part.startswith('.') for part in f.split('/'))
    dropped = sorted(f for f in missing if not hidden(f))
    dropped_hidden = sorted(f for f in missing if hidden(f))
    extra = sorted(f for f in files if f not in set(tracked))
    if any(f == '.git' or f.startswith('.git/') for f in files):
        raise RuntimeError('%s contains .git' % final)
    return {'repo': os.path.basename(repo.rstrip('/')), 'path': repo, 'name': name, 'version': version,
            'sha': sha, 'tgz': final, 'size': os.path.getsize(final), 'files': files, 'method': method,
            'dropped': dropped, 'dropped_hidden': dropped_hidden, 'extra': extra, 'dirty': dirty, 'notes': notes,
            'samples': pj.get('samples') or [], 'displayName': pj.get('displayName', name)}


def extract_sample(tgz, sample_path, dst):
    """Copy package/<sample_path>/ out of the tarball into dst, as the Package Manager's Import button copies a
    sample out of the installed package. Returns (files copied, assets in the sample without a .meta)."""
    prefix = 'package/' + sample_path.strip('/') + '/'
    copied = []
    with tarfile.open(tgz, 'r:gz') as t:
        for m in t.getmembers():
            if not m.isfile() or not m.name.startswith(prefix):
                continue
            rel = m.name[len(prefix):]
            if not rel or rel.startswith('/') or '..' in rel.split('/'):
                continue
            out = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with t.extractfile(m) as src, open(out, 'wb') as f:
                shutil.copyfileobj(src, f)
            copied.append(rel)
    present = set(copied)
    folders = {'/'.join(r.split('/')[:i]) for r in copied for i in range(1, len(r.split('/')))}
    no_meta = sorted(a for a in (present | folders) if not a.endswith('.meta') and a + '.meta' not in present
                     and not any(part.startswith('.') for part in a.split('/')))
    return copied, no_meta
