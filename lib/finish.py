#!/usr/bin/env python3
"""finish: land the v2 work branches in the author's main checkouts by fast-forward, and nothing else.

For each package repo, the host project and the extra entries in config/family.json, find the main
checkout that owns the worktree under --root (git rev-parse --git-common-dir), then show what
`git merge --ff-only <source>` into <target> would do there: commits, files changed, whether it is a
fast-forward, and whether the checkout is clean and on <target>.

Dry run by default. With --apply it merges only where all of these hold: the main checkout has no staged
or unstaged changes to tracked files, it has <target> checked out, and <target> is an ancestor of
<source>. (git itself still refuses if an untracked file would be overwritten.) It never pushes, never
switches branches, never creates or moves any other ref; it prints the push commands for later.
Exit status: 0 nothing blocked, 1 at least one repo cannot be fast-forwarded as things stand.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import family_config, family_root, git, table  # noqa: E402


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='finish.sh', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apply', action='store_true', help='perform the fast-forwards that are safe')
    ap.add_argument('--root', help='worktree folder (default: $OPENUGD_ROOT or config/family.json "root")')
    ap.add_argument('--only', help='comma-separated repo folders to consider (default: all configured)')
    ap.add_argument('--log', type=int, default=15, help='commits to list per repo (default 15)')
    return ap.parse_args(argv)


def entries():
    cfg = family_config()
    out = [dict(repo=p['repo'], source=p.get('source', cfg['sourceBranch']), target=p['target'])
           for p in cfg['packages']]
    out += [dict(repo=p['repo'], source=p.get('source', cfg['sourceBranch']), target=p['target'])
            for p in cfg.get('finishExtra', [])]
    return out


def held():
    """Branches finish.sh must never land; it only prints why."""
    return family_config().get('held', [])


def main_checkout(worktree):
    code, common = git(worktree, 'rev-parse', '--git-common-dir')
    if code != 0:
        return None
    common = common.strip()
    if not os.path.isabs(common):
        common = os.path.normpath(os.path.join(worktree, common))
    return os.path.dirname(common) if os.path.basename(common) == '.git' else None


def rev(repo, ref):
    code, out = git(repo, 'rev-parse', '--verify', '--quiet', 'refs/heads/' + ref)
    return out.strip() if code == 0 else None


def inspect(e, root):
    wt = os.path.join(root, e['repo'])
    r = dict(e, worktree=wt, main=None, action='', detail='', commits=[], stat='', ahead=0, behind=0)
    if not os.path.isdir(wt):
        r['action'], r['detail'] = 'skip', 'no worktree at %s' % wt
        return r
    main = main_checkout(wt)
    r['main'] = main
    if not main or not os.path.isdir(main):
        r['action'], r['detail'] = 'skip', 'cannot find the main checkout of %s' % wt
        return r
    src, dst = rev(main, e['source']), rev(main, e['target'])
    if not src or not dst:
        r['action'] = 'blocked'
        r['detail'] = 'branch %s does not exist' % (e['source'] if not src else e['target'])
        return r
    _, counts = git(main, 'rev-list', '--left-right', '--count', '%s...%s' % (e['target'], e['source']))
    r['behind'], r['ahead'] = (int(x) for x in counts.split())
    _, log = git(main, 'log', '--oneline', '--no-decorate', '%s..%s' % (e['target'], e['source']))
    r['commits'] = [l for l in log.splitlines() if l.strip()]
    _, stat = git(main, 'diff', '--shortstat', e['target'], e['source'])
    r['stat'] = stat.strip()
    _, branch = git(main, 'branch', '--show-current')
    r['on'] = branch.strip() or '(detached)'
    _, dirty = git(main, 'status', '--porcelain', '--untracked-files=no')
    r['dirty'] = [l for l in dirty.splitlines() if l.strip()]
    _, untracked = git(main, 'status', '--porcelain', '--untracked-files=normal')
    r['untracked'] = sum(1 for l in untracked.splitlines() if l.startswith('??'))
    ff = git(main, 'merge-base', '--is-ancestor', e['target'], e['source'])[0] == 0
    if r['ahead'] == 0:
        r['action'], r['detail'] = 'none', 'up to date'
    elif not ff:
        r['action'] = 'blocked'
        r['detail'] = 'not a fast-forward: %s has %d commit(s) that %s lacks' % (e['target'], r['behind'],
                                                                                 e['source'])
    elif r['on'] != e['target']:
        r['action'] = 'blocked'
        r['detail'] = 'main checkout is on %s, not %s (switch it yourself, then rerun)' % (r['on'], e['target'])
    elif r['dirty']:
        r['action'], r['detail'] = 'blocked', 'main checkout has %d uncommitted change(s)' % len(r['dirty'])
    else:
        r['action'], r['detail'] = 'fast-forward', '%d commit(s); %s' % (r['ahead'], r['stat'] or 'no file changes')
    return r


def main(argv):
    a = parse_args(argv)
    root = family_root(a.root)
    only = set(x.strip() for x in a.only.split(',')) if a.only else None
    results = [inspect(e, root) for e in entries() if not only or e['repo'] in only]
    print('FINISH  %s  root %s\n' % ('APPLY' if a.apply else 'dry run (pass --apply to merge)', root))
    rows = [[r['repo'], r['main'] or '-', r.get('on', '-'), '%s <- %s' % (r['target'], r['source']),
             r['ahead'], r['behind'], 'no' if r.get('dirty') else 'yes' if r['main'] else '-', r['action'],
             r['detail']] for r in results]
    print(table(rows, ['repo', 'main checkout', 'on', 'target <- source', 'ahead', 'behind', 'clean',
                       'action', 'detail']))
    for r in results:
        if r['commits']:
            print('\n%s: git -C %s merge --ff-only %s   (%s)' % (r['repo'], r['main'], r['source'], r['stat']))
            for c in r['commits'][:a.log]:
                print('    ' + c)
            if len(r['commits']) > a.log:
                print('    ... %d more' % (len(r['commits']) - a.log))
    failed = False
    if a.apply:
        print()
        for r in results:
            if r['action'] != 'fast-forward':
                continue
            code, out = git(r['main'], 'merge', '--ff-only', r['source'])
            print('%s: %s' % (r['repo'], 'merged' if code == 0 else 'merge refused:\n' + out))
            failed = failed or code != 0
    for h in held():
        print('\nHELD, never merged by this script: %s %s -> %s\n    %s' % (h['repo'], h['branch'], h['target'], h['reason']))
    print('\nPush later, yourself (this script never pushes):')
    for r in results:
        if not r['main'] or not r['ahead']:
            continue
        code, remotes = git(r['main'], 'remote')
        remotes = remotes.split()
        if not remotes:
            print('  %-26s no remote configured; create the GitHub repository first' % r['repo'])
        else:
            remote = 'origin' if 'origin' in remotes else remotes[0]
            print('  git -C %s push %s %s' % (r['main'], remote, r['target']))
    blocked = any(r['action'] == 'blocked' for r in results)
    return 1 if blocked or failed else 0


def entry():
    try:
        return main(sys.argv[1:])
    except KeyboardInterrupt:
        return 130
    except SystemExit as e:
        if isinstance(e.code, str):     # a tooling problem reported with a message
            sys.stderr.write(e.code + '\n')
            return 2
        raise


if __name__ == '__main__':
    sys.exit(entry())
