""".meta coverage for package roots, checked against both the working tree and what git would publish.

OpenUPM publishes a git tag, so the git index is what users get. Unity imports every file and folder in a
package except hidden ones (leading '.', trailing '~', 'cvs', '*.tmp'); in an immutable (registry or
tarball) package an asset without a .meta is ignored with "has no meta file, but it's in an immutable
folder", and a .meta whose asset is gone produces a warning.

Problems reported:
  MISSING        an asset or folder in the working tree has no .meta next to it
  ORPHAN         a .meta in the working tree whose asset does not exist
  UNTRACKED-META a .meta in the working tree that git does not track (it would not be published)
  MISSING-IN-GIT a tracked asset whose .meta is not tracked
  ORPHAN-IN-GIT  a tracked .meta whose asset is not tracked (an empty folder or a forgotten file):
                 a clean clone, and therefore the published package, gets an orphan .meta
  DUPLICATE-GUID two .meta files in the family share a GUID
"""
import os
import subprocess

from asmdefs import read_guid, unity_ignores


def _hidden(rel):
    return any(unity_ignores(part) for part in rel.split('/'))


def _git_files(root, *args):
    p = subprocess.run(['git', '-C', root, 'ls-files', '-z'] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode('utf-8', 'replace').strip())
    return [x for x in p.stdout.decode('utf-8').split('\0') if x]


def _empty_dirs(root):
    """Folders with no files below them at all: Unity imports (and needs a .meta for) them, git cannot
    track them."""
    out = set()
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x != '.git' and not unity_ignores(x)]
        if not files and not dirs and d != root:
            rel = os.path.relpath(d, root).replace(os.sep, '/')
            parts = rel.split('/')
            out.update('/'.join(parts[:i]) for i in range(1, len(parts) + 1))
    return out


def _check_set(files, label_missing, label_orphan, root, exists=None, extra_folders=()):
    """files: set of repo-relative file paths. Folders are implied by the files they contain."""
    problems = []
    visible = {f for f in files if not _hidden(f)}
    metas = {f for f in visible if f.endswith('.meta')}
    assets = visible - metas
    folders = set()
    for f in assets | metas:
        parts = f.split('/')[:-1]
        for i in range(1, len(parts) + 1):
            folders.add('/'.join(parts[:i]))
    # A folder that holds only .meta files is real in the working tree, but not in git.
    if exists is not None:
        folders = {d for d in folders if exists(d)}
    folders |= set(extra_folders)
    for a in sorted(assets | folders):
        if a + '.meta' not in metas:
            problems.append((label_missing, a + '.meta'))
    for m in sorted(metas):
        target = m[:-5]
        if target not in assets and target not in folders:
            problems.append((label_orphan, m))
    return problems


def check_package(root):
    """Returns (problems, guids) for one package root that is also a git work tree root."""
    problems = []
    tracked = set(_git_files(root, '--cached'))
    tracked_present = {f for f in tracked if os.path.lexists(os.path.join(root, f))}
    untracked = set(_git_files(root, '--others', '--exclude-standard'))
    working = tracked_present | untracked
    isdir = lambda rel: os.path.isdir(os.path.join(root, rel))
    problems += _check_set(working, 'MISSING', 'ORPHAN', root, exists=isdir, extra_folders=_empty_dirs(root))
    problems += [('UNTRACKED-META', f) for f in sorted(untracked) if f.endswith('.meta') and not _hidden(f)]
    git_problems = _check_set(tracked, 'MISSING-IN-GIT', 'ORPHAN-IN-GIT', root)
    # Do not repeat in git terms what the working-tree check already says.
    reported = {p for _, p in problems}
    problems += [p for p in git_problems if p[1] not in reported]
    guids = {}
    for f in sorted(working):
        if f.endswith('.meta') and not _hidden(f):
            g = read_guid(os.path.join(root, f))
            if g:
                guids.setdefault(g, []).append(os.path.join(os.path.basename(root), f))
    return problems, guids


def check_family(roots):
    results = {}
    all_guids = {}
    for root in roots:
        try:
            problems, guids = check_package(root)
        except RuntimeError as e:
            problems, guids = [('GIT-ERROR', str(e))], {}
        results[root] = problems
        for g, files in guids.items():
            all_guids.setdefault(g, []).extend(files)
    dups = {g: f for g, f in all_guids.items() if len(f) > 1}
    for g, files in sorted(dups.items()):
        for f in files:
            root = next(r for r in roots if os.path.basename(r) == f.split('/', 1)[0])
            results[root].append(('DUPLICATE-GUID', '%s (guid %s, also in %s)' % (
                f.split('/', 1)[1], g, ', '.join(x for x in files if x != f))))
    return results
