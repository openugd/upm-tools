#!/usr/bin/env python3
"""Clone the package repos the gates check into one folder, the layout --root and $OPENUGD_ROOT expect.

The repos are config/family.json "packages" (the train) plus "outsideTrain" (checked by level 1 on request), each
cloned from https://github.com/<org>/<repo>.git at its default branch. --ref (a branch or tag of the train, such as
2.0.0) applies to the train only: the repos outside it are not released with it and stay on their default branch.
Shallow clones: level 1 and the linker gate read the working tree and `git ls-files`.

A repo already cloned under --dest is reused; with --ref it is moved to that ref first, without --ref it is left
as it is. Each line printed names the commit actually checked out.

    python3 ci/clone-packages.py --dest "$RUNNER_TEMP/packages"
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lib'))

from common import family_config  # noqa: E402


def parse_args(argv):
    ap = argparse.ArgumentParser(prog='clone-packages.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dest', required=True, help='folder to clone into, one subfolder per repo')
    ap.add_argument('--org', default='openugd', help='GitHub owner of the repos (default: openugd)')
    ap.add_argument('--ref', default='', help='branch or tag to check out in every train repo (default: its '
                                              'default branch)')
    ap.add_argument('--train-only', action='store_true', help='skip the "outsideTrain" repos')
    return ap.parse_args(argv)


def git(*args):
    if subprocess.call(['git'] + list(args)) != 0:
        raise SystemExit('git %s failed' % ' '.join(args))


def main(argv):
    a = parse_args(argv)
    cfg = family_config()
    repos = [(p['repo'], a.ref) for p in cfg['packages']]
    if not a.train_only:
        repos += [(p['repo'], '') for p in cfg.get('outsideTrain', [])]
    os.makedirs(a.dest, exist_ok=True)
    for repo, ref in repos:
        target = os.path.join(a.dest, repo)
        url = 'https://github.com/%s/%s.git' % (a.org, repo)
        note = ''
        exists = os.path.isdir(os.path.join(target, '.git'))
        if not exists and not ref:
            git('clone', '--quiet', '--depth', '1', url, target)
        elif ref:
            # fetch + detached checkout works for a branch and for an annotated tag alike, and on an existing clone
            # (`clone --branch <annotated tag> --depth 1` warns that the tag "is not a commit").
            if not exists:
                git('init', '--quiet', target)
                git('-C', target, 'remote', 'add', 'origin', url)
            git('-C', target, 'fetch', '--quiet', '--depth', '1', 'origin', ref)
            git('-C', target, '-c', 'advice.detachedHead=false', 'checkout', '--quiet', '--detach', 'FETCH_HEAD')
            note = '  (existing clone, moved to %s)' % ref if exists else ''
        else:
            note = '  (existing clone, not updated)'
        head = subprocess.check_output(['git', '-C', target, 'log', '-1', '--format=%h %s'], text=True).strip()
        print('%-22s %s%s' % (repo, head, note))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
