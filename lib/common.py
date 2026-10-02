"""Shared helpers: configuration, subprocess, family discovery, plain-text tables."""
import json
import os
import subprocess
import sys

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(TOOLS, 'config')


def load_json(path):
    with open(path, encoding='utf-8-sig') as f:
        return json.load(f)


def family_config():
    return load_json(os.path.join(CONFIG, 'family.json'))


def family_root(arg=None):
    return os.path.abspath(arg or os.environ.get('OPENUGD_ROOT') or family_config()['root'])


def selected_repos(arg):
    """--packages a,b,c (repo folder names) or the configured default set."""
    if arg:
        return [p.strip().rstrip('/') for p in arg.split(',') if p.strip()]
    return [p['repo'] for p in family_config()['packages']]


def env():
    e = dict(os.environ)
    e.update(DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1', DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',
             MSBUILDTERMINALLOGGER='off', DOTNET_CLI_UI_LANGUAGE='en')
    return e


def run(cmd, cwd=None, timeout=None, check=False, extra_env=None):
    """Run a command, capture stdout+stderr together; returns (exit code, text)."""
    e = env()
    e.update(extra_env or {})
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=e,
                       timeout=timeout)
    text = p.stdout.decode('utf-8', 'replace')
    if check and p.returncode != 0:
        sys.stderr.write(text)
        raise SystemExit('command failed (%d): %s' % (p.returncode, ' '.join(cmd)))
    return p.returncode, text


def git(repo, *args):
    code, out = run(['git', '-C', repo] + list(args))
    return code, out


def table(rows, headers):
    """Left-aligned text table."""
    rows = [[str(c) for c in r] for r in rows]
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    line = lambda cells: '  '.join(c.ljust(widths[i]) for i, c in enumerate(cells)).rstrip()
    out = [line(headers), line(['-' * w for w in widths])]
    out += [line(r) for r in rows]
    return '\n'.join(out)


def human_size(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return ('%.1f %s' % (n, unit)) if unit != 'B' else '%d B' % n
        n /= 1024.0


def dir_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def free_disk(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize
