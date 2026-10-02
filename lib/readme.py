"""Extract the C# code fences of a package README that declare a type, so they can be compiled.

A fence is compiled when its info string is csharp, cs or c# and its body declares a class, struct,
interface, enum or record. Fragments without a type declaration (a few statements, a signature) are not
compilable on their own and are skipped. To exclude a fence that does declare a type, put this HTML
comment on the nearest non-blank line above its opening fence (it does not render on GitHub or OpenUPM):

    <!-- upm-tools: no-compile -->

Anything after "no-compile" inside the comment is free text, e.g. a reason.
"""
import re

FENCE_RE = re.compile(r'^( {0,3})(`{3,}|~{3,})\s*([^\s`]*)')
CSHARP = ('csharp', 'cs', 'c#')
OPT_OUT_RE = re.compile(r'^\s*<!--\s*upm-tools:\s*no-compile\b.*-->\s*$', re.I)
TYPE_DECL_RE = re.compile(
    r'^[ \t]*(?:\[[^\]\n]*\][ \t]*)*(?:(?:public|internal|private|protected|sealed|static|abstract|partial|'
    r'readonly|unsafe|new|ref)[ \t]+)*(?:class|struct|interface|enum|record)[ \t]+[A-Za-z_]\w*', re.M)


class Fence(object):
    def __init__(self, line, lang, body, opted_out):
        self.line = line              # 1-based line of the opening fence
        self.lang = lang
        self.body = body
        self.opted_out = opted_out

    @property
    def declares_type(self):
        return bool(TYPE_DECL_RE.search(self.body))


def fences(text):
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        m = FENCE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        marker, lang = m.group(2), m.group(3).lower()
        start = i
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        opted_out = j >= 0 and bool(OPT_OUT_RE.match(lines[j]))
        body = []
        i += 1
        while i < len(lines):
            close = re.match(r'^ {0,3}(`{3,}|~{3,})\s*$', lines[i])
            if close and close.group(1)[0] == marker[0] and len(close.group(1)) >= len(marker):
                break
            body.append(lines[i])
            i += 1
        out.append(Fence(start + 1, lang, '\n'.join(body) + '\n', opted_out))
        i += 1
    return out


def compilable(text):
    """(fence, verdict) for every C# fence: verdict is 'compile', 'opted-out' or 'no type declaration'."""
    result = []
    for f in fences(text):
        if f.lang not in CSHARP:
            continue
        if f.opted_out:
            result.append((f, 'opted-out'))
        elif not f.declares_type:
            result.append((f, 'no type declaration'))
        else:
            result.append((f, 'compile'))
    return result
