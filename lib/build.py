"""Restore once, then build the project graph in dependency order, independent projects in parallel.

Each project is built on its own (`-p:BuildProjectReferences=false`), so its exit code and diagnostics
belong to it alone, and a failed project marks everything that depends on it BLOCKED instead of producing
a cascade of misleading errors.
"""
import concurrent.futures
import os
import re

from common import run
from projects import DOC_CODES

DIAG_RE = re.compile(r'^(?P<file>.*?)(?:\((?P<line>\d+),(?P<col>\d+)(?:,\d+,\d+)?\))?\s*:\s*'
                     r'(?P<sev>error|warning)\s+(?P<code>[A-Z]+\d+)\s*:\s*(?P<msg>.*?)(?:\s+\[(?P<proj>[^\]]+)\])?$')
# Unresolved hint paths are warnings to MSBuild, but for us they mean a broken reference set.
REFERENCE_FAILURES = ('MSB3245', 'MSB3243', 'MSB9008')


class Diag(object):
    __slots__ = ('file', 'line', 'col', 'sev', 'code', 'msg')

    def __init__(self, m):
        self.file = m.group('file').strip()
        self.line = int(m.group('line') or 0)
        self.col = int(m.group('col') or 0)
        self.sev = m.group('sev')
        self.code = m.group('code')
        self.msg = m.group('msg').strip()

    def key(self):
        return (self.file, self.line, self.col, self.code, self.msg)

    def __str__(self):
        loc = '%s(%d,%d)' % (self.file, self.line, self.col) if self.line else self.file
        return '%s: %s %s: %s' % (loc, self.sev, self.code, self.msg)


def parse_diagnostics(text):
    errors, warnings, seen = [], [], set()
    for line in text.splitlines():
        m = DIAG_RE.match(line.strip())
        if not m:
            continue
        d = Diag(m)
        if d.key() in seen:
            continue
        seen.add(d.key())
        if d.sev == 'warning' and d.code in REFERENCE_FAILURES:
            d.sev = 'error'
        (errors if d.sev == 'error' else warnings).append(d)
    return errors, warnings


def write_solution(path, nodes):
    lines = ['<Solution>'] + ['  <Project Path="%s" />' % n.csproj for n in nodes] + ['</Solution>', '']
    text = '\n'.join(lines)
    try:
        with open(path) as f:
            if f.read() == text:
                return
    except OSError:
        pass
    with open(path, 'w') as f:
        f.write(text)


def restore(solution, log_path):
    code, out = run(['dotnet', 'restore', solution, '-nologo', '-v', 'q'], timeout=900)
    with open(log_path, 'w') as f:
        f.write(out)
    return code, out


def _build_one(node, log_dir):
    # --no-incremental: a no-op incremental build re-emits no warnings, so documentation errors (which are
    # warnings promoted by this gate) would silently pass on a rerun into the same output folder.
    code, out = run(['dotnet', 'build', node.csproj, '--no-restore', '--no-incremental', '-nologo', '-v', 'q',
                     '-clp:NoSummary', '-p:BuildProjectReferences=false', '-nodeReuse:false'], timeout=900)
    node.log = os.path.join(log_dir, node.id.replace('/', '__') + '.log')
    with open(node.log, 'w') as f:
        f.write(out)
    node.errors, node.warnings = parse_diagnostics(out)
    built = code == 0 and os.path.exists(node.dll) and not node.errors
    if not built and not node.errors:
        node.reason = 'dotnet build exited %d without a parsable error; see %s' % (code, node.log)
    elif node.errors:
        node.reason = '%d compile error(s)' % len(node.errors)
    if node.docs_as_errors:
        # Documentation diagnostics are compiled as warnings, so the assembly is still produced and its
        # dependents still compile (a missing <summary> must not hide a real error downstream), and are
        # promoted to errors here: the assembly FAILs the gate exactly as with -warnaserror.
        promoted = [d for d in node.warnings if d.code in DOC_CODES]
        for d in promoted:
            d.sev = 'error'
        node.warnings = [d for d in node.warnings if d.code not in DOC_CODES]
        node.errors += promoted
        if built and promoted:
            node.reason = '%d documentation error(s)' % len(promoted)
    node.built = built
    return built


def build_graph(nodes, log_dir, jobs):
    """Build every node; sets node.status to PASS/FAIL/BLOCKED (canary expectations are judged later)."""
    os.makedirs(log_dir, exist_ok=True)
    pending = {n.id: n for n in nodes}
    done = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        running = {}
        while pending or running:
            for nid, n in list(pending.items()):
                prereq = n.deps + n.after
                failed = [d for d in prereq if done.get(d.id) is False]
                if failed:
                    n.status = 'BLOCKED'
                    n.reason = 'depends on %s' % ', '.join(sorted(d.id for d in failed))
                    done[nid] = False
                    del pending[nid]
                elif all(done.get(d.id) for d in prereq):
                    running[pool.submit(_build_one, n, log_dir)] = n
                    del pending[nid]
            if not running:
                if pending:      # a dependency outside the node set: should not happen
                    for nid, n in pending.items():
                        n.status, n.reason = 'BLOCKED', 'dependency not part of this run'
                        done[nid] = False
                    pending.clear()
                break
            finished, _ = concurrent.futures.wait(list(running), return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in finished:
                n = running.pop(fut)
                try:
                    built = fut.result()
                except Exception as e:  # timeout or OS error
                    built, n.reason = False, 'build crashed: %s' % e
                n.status = 'PASS' if built and not n.errors else 'FAIL'
                done[n.id] = built
