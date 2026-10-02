"""Version parsing and the two asmdef rule languages Unity evaluates before it compiles an assembly.

* ``versionDefines``: ``{"name": <package or "Unity">, "expression": <range>, "define": <symbol>}``. The symbol
  is defined for that one assembly when the named package is present in the project at a version that
  satisfies the range. Range syntax (Unity manual, "Assembly definition file format"):
  ``"1.2.3"`` means x >= 1.2.3; ``"[1.2,3.4)"`` and ``"(1.2,3.4]"`` are intervals with an optional open end;
  ``"[1.2.3]"`` means exactly 1.2.3; an empty expression means "any version".
* ``defineConstraints``: every entry must hold. An entry is one or more terms joined by ``||``; a term is
  ``SYMBOL`` or ``!SYMBOL``. The symbols a constraint can see are the global scripting defines plus the
  assembly's own ``versionDefines``.
"""
import re

_UNITY_RE = re.compile(r'^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:([a-z]+)(\d+))?$')
# Unity release types in ascending order: experimental, alpha, beta, release candidate, final (and the
# China-specific 'c' builds, which are finals), patch.
_UNITY_TYPE_RANK = {'x': 0, 'a': 1, 'b': 2, 'rc': 3, 'f': 4, 'c': 4, 'p': 5}


def parse_unity_version(text):
    """'6000.0.41f1' -> (6000, 0, 41, 4, 1). A bound written without a release type ('2023.1') sorts
    before every real build of that version, so '[x,2023.1)' excludes 2023.1.0a1."""
    m = _UNITY_RE.match(text.strip())
    if not m:
        raise ValueError('not a Unity version: %r' % text)
    major, minor, patch, kind, num = m.groups()
    rank = _UNITY_TYPE_RANK.get(kind, -1) if kind else -1
    return (int(major), int(minor or 0), int(patch or 0), rank, int(num or 0))


def _prerelease_key(pre):
    # SemVer 2.0.0 section 11: numeric identifiers sort numerically and before alphanumeric ones.
    out = []
    for part in pre.split('.'):
        out.append((0, int(part), '') if part.isdigit() else (1, 0, part))
    return tuple(out)


def parse_semver(text):
    """'1.0.0-preview.2' -> comparable key. A missing minor/patch counts as 0; a release sorts after
    every pre-release of the same version."""
    text = text.strip().split('+', 1)[0]
    core, _, pre = text.partition('-')
    nums = core.split('.')
    if not all(n.isdigit() for n in nums) or not 1 <= len(nums) <= 3:
        raise ValueError('not a semantic version: %r' % text)
    nums = [int(n) for n in nums] + [0] * (3 - len(nums))
    return (tuple(nums), (1,) if not pre else (0,) + _prerelease_key(pre))


def version_key(package, text):
    return parse_unity_version(text) if package == 'Unity' else parse_semver(text)


def satisfies(package, version, expression):
    """True when ``version`` of ``package`` satisfies a versionDefines ``expression``."""
    expr = (expression or '').strip()
    if not expr:
        return True
    v = version_key(package, version)
    if expr[0] in '[(' and expr[-1] in '])':
        inner = expr[1:-1]
        if ',' not in inner:
            return v == version_key(package, inner)
        lo, hi = (s.strip() for s in inner.split(',', 1))
        if lo:
            k = version_key(package, lo)
            if v < k or (expr[0] == '(' and v == k):
                return False
        if hi:
            k = version_key(package, hi)
            if v > k or (expr[-1] == ')' and v == k):
                return False
        return True
    return v >= version_key(package, expr)


def version_defines(asmdef_data, package_versions):
    """Symbols an asmdef's versionDefines produce for the given {package: version} map. Unity's own
    version is looked up under the name 'Unity'."""
    out = []
    for vd in asmdef_data.get('versionDefines') or []:
        name, define = vd.get('name'), vd.get('define')
        if not name or not define or name not in package_versions:
            continue
        try:
            if satisfies(name, package_versions[name], vd.get('expression', '')):
                out.append(define)
        except ValueError:
            # Unity treats an expression it cannot parse as unsatisfied (and logs an error in the
            # inspector); mirror that rather than crash.
            continue
    return out


def constraints_hold(constraints, defines):
    """Evaluate asmdef defineConstraints against a set of defined symbols."""
    defined = set(defines)
    for entry in constraints or []:
        ok = False
        for term in entry.split('||'):
            term = term.strip()
            if not term:
                continue
            if term.startswith('!'):
                ok = term[1:].strip() not in defined
            else:
                ok = term in defined
            if ok:
                break
        if not ok:
            return False
    return True


_HISTORIC = ['5_3', '5_4', '5_5', '5_6'] + ['%d_%d' % (y, m) for y in range(2017, 2023) for m in range(1, 5)
                                            if not (y in (2020, 2021, 2022) and m == 4)] + \
            ['2023_1', '2023_2', '2023_3']


def unity_version_symbols(unity_version):
    """The UNITY_* symbols Unity defines for an editor version, e.g. for 6000.0.41f1: UNITY_6000_0_41,
    UNITY_6000_0, UNITY_6000 and every UNITY_x_y_OR_NEWER from 5.3 up to 6000.0."""
    major, minor, patch, _, _ = parse_unity_version(unity_version)
    syms = ['UNITY_%d_%d_%d' % (major, minor, patch), 'UNITY_%d_%d' % (major, minor), 'UNITY_%d' % major]
    syms += ['UNITY_%s_OR_NEWER' % h for h in _HISTORIC]
    if major >= 6000:
        syms += ['UNITY_6000_%d_OR_NEWER' % m for m in range(0, minor + 1)]
    return syms
