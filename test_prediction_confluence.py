import unittest
from prediction_confluence import describe_confluence


def item(identity, family='BAZI', kind='up', target='2026-10-10', **extra):
    return {'id': identity, 'rootId': identity, 'version': 1, 'symbol': 'BTCUSDT',
            'kind': kind, 'targetDate': target, 'horizonDays': 1,
            'createdAtUtc': '2026-09-27T05:00:00+00:00', 'prospectiveEligible': True,
            'rules': [{'id': 'R1', 'family': family, 'version': '1', 'direction': kind}], **extra}


def result(records):
    return describe_confluence(records, '2026-09-28T00:00:00+00:00')['rows']


class ConfluenceTests(unittest.TestCase):
    def test_cross_family_same_direction_and_conflict_coexist(self):
        rows = result([item('a'), item('b', 'GANN'), item('c', 'ASTROLOGY', 'down')])
        self.assertTrue(rows[0]['crossFamilyAgreement'])
        self.assertEqual(rows[0]['conflictingIds'], ['c'])
        self.assertTrue(rows[0]['hasConflict'])

    def test_duplicate_rule_versions_and_manual_are_not_independent(self):
        a, b, c = item('a'), item('b'), item('c', 'MANUAL')
        b['rules'][0]['version'] = '2'
        rows = result([a, b, c])
        self.assertEqual(rows[0]['distinctRuleCount'], 2)
        self.assertEqual(rows[0]['duplicateReferencesIgnored'], 1)
        self.assertFalse(rows[0]['crossFamilyAgreement'])
        self.assertFalse(rows[0]['sameFamilyAgreement'])

    def test_neutral_time_agreement_never_votes_direction(self):
        rows = result([item('a'), item('b', 'GANN', 'time'), item('c', 'ASTROLOGY', 'time')])
        self.assertFalse(rows[0]['crossFamilyAgreement'])
        self.assertEqual(set(rows[0]['timingOnlyIds']), {'b', 'c'})
        self.assertTrue(rows[1]['crossFamilyTimingOnly'])
        self.assertFalse(rows[1]['crossFamilyAgreement'])

    def test_no_old_version_fallback_for_invalidated_or_late_revision(self):
        for flag in ({'invalidated': True}, {'lateRevision': True}, {'prospectiveEligible': False}):
            rows = result([item('a'), item('b', rootId='a', version=2, **flag)])
            self.assertEqual(rows, [])

    def test_windows_are_pairwise_no_transitive_cluster(self):
        rows = result([item('a', 'GANN', 'time', '2026-10-10'),
                       item('b', 'ASTROLOGY', 'time', '2026-10-16'),
                       item('c', 'BAZI', 'time', '2026-10-22')])
        self.assertEqual(rows[0]['sameKindIds'], ['b'])
        self.assertEqual(rows[2]['sameKindIds'], ['b'])
        self.assertEqual(set(rows[1]['sameKindIds']), {'a', 'c'})

    def test_asset_future_created_and_incompatible_direction_excluded(self):
        a, b = item('a'), item('b', 'GANN')
        b['rules'][0]['direction'] = 'time'
        rows = result([a, b, item('c', 'ASTROLOGY', symbol='ETHUSDT'),
                       item('d', 'ASTROLOGY', createdAtUtc='2026-10-01T00:00:00+00:00')])
        self.assertEqual(len(rows), 3)
        self.assertFalse(rows[0]['crossFamilyAgreement'])
        self.assertEqual(rows[0]['distinctTheoryFamilies'], ['BAZI'])

    def test_disjoint_partners_cannot_manufacture_simultaneous_agreement(self):
        rows = result([item('a', rules=[], horizonDays=30), item('b', 'GANN'),
                       item('c', 'ASTROLOGY', target='2026-11-08')])
        self.assertEqual(rows[0]['distinctTheoryFamilies'], ['ASTROLOGY', 'GANN'])
        self.assertFalse(rows[0]['crossFamilyAgreement'])
        self.assertEqual(rows[0]['agreementWindows'], [])


if __name__ == '__main__':
    unittest.main()
