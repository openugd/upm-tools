"""Unit tests for README fence extraction and the opt-out marker."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lib'))

import readme as R  # noqa: E402

DOC = '''# Title

```csharp
var x = Lifetime.Eternal;
```

```csharp
public sealed class A { }
```

<!-- upm-tools: no-compile (pseudo-code) -->

```cs
public class B : Missing { }
```

```json
{"class": "x"}
```

````c#
[Serializable] internal struct C { }
```
still inside
````
'''


class FenceTests(unittest.TestCase):
    def test_verdicts(self):
        got = [(f.line, verdict) for f, verdict in R.compilable(DOC)]
        self.assertEqual(got, [(3, 'no type declaration'), (7, 'compile'), (13, 'opted-out'), (21, 'compile')])

    def test_longer_fence_contains_shorter(self):
        last = R.compilable(DOC)[-1][0]
        self.assertIn('still inside', last.body)

    def test_marker_must_be_nearest_non_blank_line(self):
        doc = '<!-- upm-tools: no-compile -->\ntext\n\n```csharp\nclass D { }\n```\n'
        self.assertEqual(R.compilable(doc)[0][1], 'compile')


if __name__ == '__main__':
    unittest.main()
