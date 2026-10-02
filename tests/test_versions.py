"""Unit tests for the asmdef rule evaluation (versionDefines, defineConstraints, Unity define ladder)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lib'))

import versions as V  # noqa: E402


class SemverTests(unittest.TestCase):
    def test_prerelease_sorts_before_release(self):
        self.assertLess(V.parse_semver('1.0.0-preview.1'), V.parse_semver('1.0.0'))
        self.assertLess(V.parse_semver('1.0.0-pre.2'), V.parse_semver('1.0.0-pre.10'))
        self.assertLess(V.parse_semver('1.0.0-alpha'), V.parse_semver('1.0.0-alpha.1'))

    def test_missing_parts_are_zero(self):
        self.assertEqual(V.parse_semver('2.0'), V.parse_semver('2.0.0'))


class UnityVersionTests(unittest.TestCase):
    def test_release_types_order(self):
        order = ['2023.3.0a1', '2023.3.0b4', '2023.3.0f1', '2023.3.0p2', '6000.0.41f1']
        keys = [V.parse_unity_version(x) for x in order]
        self.assertEqual(keys, sorted(keys))

    def test_bare_bound_sorts_before_every_build(self):
        self.assertLess(V.parse_unity_version('2023.1'), V.parse_unity_version('2023.1.0a1'))


class ExpressionTests(unittest.TestCase):
    def test_minimum(self):
        self.assertTrue(V.satisfies('com.unity.ugui', '2.0.0', '2.0.0'))
        self.assertFalse(V.satisfies('com.unity.ugui', '1.0.0', '2.0.0'))
        self.assertTrue(V.satisfies('com.unity.textmeshpro', '3.0.9', '3.0.0'))

    def test_intervals(self):
        self.assertTrue(V.satisfies('p', '1.5.0', '[1.0,2.0)'))
        self.assertFalse(V.satisfies('p', '2.0.0', '[1.0,2.0)'))
        self.assertTrue(V.satisfies('p', '2.0.0', '[1.0,2.0]'))
        self.assertFalse(V.satisfies('p', '1.0.0', '(1.0,2.0]'))
        self.assertTrue(V.satisfies('p', '9.0.0', '[1.0,)'))
        self.assertTrue(V.satisfies('p', '1.2.3', '[1.2.3]'))
        self.assertFalse(V.satisfies('p', '1.2.4', '[1.2.3]'))

    def test_unity_ranges_from_ugui_tmp_asmdef(self):
        # Unity.TextMeshPro.asmdef in com.unity.ugui 2.0.0
        self.assertFalse(V.satisfies('Unity', '6000.0.41f1', '[2022.3.16f1,2023.1)'))
        self.assertTrue(V.satisfies('Unity', '6000.0.41f1', '2023.3.0b4'))
        self.assertTrue(V.satisfies('Unity', '2022.3.20f1', '[2022.3.16f1,2023.1)'))

    def test_empty_expression_means_present(self):
        self.assertTrue(V.satisfies('p', '0.0.1', ''))


class VersionDefinesTests(unittest.TestCase):
    TMP_SUB_ASSEMBLY = {  # the shape decision 5 prescribes for com.openugd.corelib.widgets.tmp
        'defineConstraints': ['OPENUGD_WIDGETS_TMP'],
        'versionDefines': [
            {'name': 'com.unity.textmeshpro', 'expression': '3.0.0', 'define': 'OPENUGD_WIDGETS_TMP'},
            {'name': 'com.unity.ugui', 'expression': '2.0.0', 'define': 'OPENUGD_WIDGETS_TMP'},
        ]}

    def test_defined_through_ugui_2(self):
        d = V.version_defines(self.TMP_SUB_ASSEMBLY, {'com.unity.ugui': '2.0.0', 'Unity': '6000.0.41f1'})
        self.assertEqual(d, ['OPENUGD_WIDGETS_TMP'])
        self.assertTrue(V.constraints_hold(self.TMP_SUB_ASSEMBLY['defineConstraints'], d))

    def test_absent_without_either_package(self):
        d = V.version_defines(self.TMP_SUB_ASSEMBLY, {'com.unity.ugui': '1.0.0'})
        self.assertEqual(d, [])
        self.assertFalse(V.constraints_hold(self.TMP_SUB_ASSEMBLY['defineConstraints'], d))

    def test_unparsable_expression_is_unsatisfied(self):
        data = {'versionDefines': [{'name': 'p', 'expression': 'not-a-version', 'define': 'X'}]}
        self.assertEqual(V.version_defines(data, {'p': '1.0.0'}), [])


class ConstraintTests(unittest.TestCase):
    def test_and_or_not(self):
        self.assertTrue(V.constraints_hold(['A', '!B'], {'A'}))
        self.assertFalse(V.constraints_hold(['A', '!B'], {'A', 'B'}))
        self.assertTrue(V.constraints_hold(['A || B'], {'B'}))
        self.assertFalse(V.constraints_hold(['A || B'], set()))
        self.assertTrue(V.constraints_hold([], set()))


class LadderTests(unittest.TestCase):
    def test_6000_0(self):
        s = V.unity_version_symbols('6000.0.41f1')
        for sym in ('UNITY_6000_0_41', 'UNITY_6000_0', 'UNITY_6000', 'UNITY_2022_3_OR_NEWER',
                    'UNITY_2023_3_OR_NEWER', 'UNITY_6000_0_OR_NEWER', 'UNITY_5_3_OR_NEWER'):
            self.assertIn(sym, s)
        self.assertNotIn('UNITY_6000_1_OR_NEWER', s)
        self.assertNotIn('UNITY_2020_4_OR_NEWER', s)

    def test_6000_3(self):
        s = V.unity_version_symbols('6000.3.3f1')
        self.assertIn('UNITY_6000_3_OR_NEWER', s)
        self.assertIn('UNITY_6000_1_OR_NEWER', s)


if __name__ == '__main__':
    unittest.main()
