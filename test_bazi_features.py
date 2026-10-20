"""Independent boundary checks for scoped Bazi structure features.

Calendar is a provenance stub here: date-to-pillar conversion has separate tests.
Full calculations use the validating base engine. Direct group fixtures exercise
member-set semantics without pretending to be dated natal charts.
"""
import copy
import json
from pathlib import Path
import unittest

from bazi_engine import calculate
from bazi_features import calculate_features, group_states, HIDDEN_STEMS


ROOT = Path(__file__).resolve().parent
PROFILES = {p['id']: p for p in json.loads(
    (ROOT / 'profiles.json').read_text(encoding='utf-8-sig'))['profiles']}
CALENDAR = {
    'atResearchTimezone': '2026-09-27T12:00:00+08:00',
    'policy': {'id': 'TEST_PROVENANCE_STUB_NOT_DATE_CONVERSION'},
}


def result(profile_id='BTC-BZ-V01', *, flow=None, profile=None):
    payload = {
        'schemaVersion': 1,
        'profile': copy.deepcopy(profile or PROFILES[profile_id]),
        'flow': flow or {'year': '甲子', 'month': '丙寅', 'day': '庚戌'},
    }
    relationships = calculate(payload)
    return calculate_features(relationships, CALENDAR)


def feature(output, feature_id):
    return next(x for x in output['features'] if x['id'] == feature_id)


def group(output, feature_id, pattern):
    return next(x for x in feature(output, feature_id)['evidence']
                if x['pattern'] == pattern)


def hidden(output, slot, stem):
    return next(x for x in output['hiddenStems']
                if x['slot'] == slot and x['stem'] == stem)


class BaziFeatureBoundaryTests(unittest.TestCase):
    def test_all_four_registered_profiles_run_and_keep_judgements_unknown(self):
        for identity in ('BTC-BZ-V01', 'ETH-BAZI-A', 'ETH-BAZI-B', 'PERSON-BZ-H01'):
            with self.subTest(profile=identity):
                out = result(identity)
                self.assertEqual(out['scope']['flowSlots'],
                                 ['flow.year', 'flow.month', 'flow.day'])
                self.assertEqual(len(out['features']), 6)
                self.assertIsNone(out['treasury']['openingJudgement'])
                self.assertIsNone(out['treasury']['favourableJudgement'])
                self.assertIsNone(out['marketDirection'])
                self.assertIs(out['marketValidated'], False)

    def test_btc_abstract_water_store_does_not_fabricate_natal_chen(self):
        out = result(flow={'year': '甲辰', 'month': '丙寅', 'day': '庚戌'})
        treasury = out['treasury']
        self.assertEqual(treasury['wealthElement'], '水')
        self.assertEqual(treasury['abstractStorageBranch'], '辰')
        self.assertEqual(treasury['natalStoragePositions'], [])
        self.assertEqual(treasury['flowStoragePositions'], ['flow.year'])
        self.assertIs(treasury['natalStoragePresent'], False)
        self.assertEqual(treasury['flowDayOppositionEdges'], [])
        self.assertIs(feature(out, 'BZ-S06')['value'], False)

    def test_eth_b_real_natal_wei_opposed_by_chou_flow_day(self):
        out = result('ETH-BAZI-B', flow={'year': '甲子', 'month': '丙寅', 'day': '己丑'})
        treasury = out['treasury']
        self.assertEqual(treasury['wealthElement'], '木')
        self.assertEqual(treasury['abstractStorageBranch'], '未')
        self.assertEqual(treasury['natalStoragePositions'], ['natal.day'])
        self.assertEqual(treasury['flowDayOppositionEdges'],
                         [{'pattern': '丑未', 'slots': ['natal.day', 'flow.day']}])
        self.assertIs(feature(out, 'BZ-S06')['value'], True)
        self.assertIsNone(treasury['openingJudgement'])

    def test_exact_stem_exposure_does_not_merge_ren_with_gui(self):
        out = result(flow={'year': '甲子', 'month': '丙寅', 'day': '壬辰'})
        self.assertEqual(hidden(out, 'natal.year', '癸')['flowVisibleAt'], [])
        self.assertEqual(hidden(out, 'natal.month', '癸')['flowVisibleAt'], [])
        self.assertEqual(hidden(out, 'natal.day', '壬')['flowVisibleAt'], ['flow.day'])
        self.assertEqual(hidden(out, 'natal.day', '壬')['tenGod'], '偏财')
        self.assertEqual(hidden(out, 'natal.year', '癸')['tenGod'], '正财')

    def test_gui_exposure_is_recorded_at_its_actual_flow_slot(self):
        out = result(flow={'year': '甲子', 'month': '丙寅', 'day': '癸巳'})
        self.assertEqual(hidden(out, 'natal.year', '癸')['natalVisibleAt'], [])
        self.assertEqual(hidden(out, 'natal.year', '癸')['flowVisibleAt'], ['flow.day'])
        self.assertEqual(hidden(out, 'natal.day', '壬')['flowVisibleAt'], [])

    def test_natal_exposure_and_day_master_match_retain_positions(self):
        out = result()
        self.assertEqual(hidden(out, 'natal.hour', '辛')['natalVisibleAt'], ['natal.hour'])
        self.assertEqual(hidden(out, 'natal.day', '戊')['natalVisibleAt'],
                         ['natal.year', 'natal.day'])

    def test_sanhe_flow_day_can_be_necessary_member(self):
        fixture = {
            'id': 'SYNTHETIC_VALID_PILLARS_NOT_BIRTH_EVENT', 'entityType': 'asset',
            'pillars': {'year': '甲子', 'month': '丙寅', 'day': '甲午', 'hour': '乙丑'},
        }
        out = result(profile=fixture, flow={'year': '甲子', 'month': '丙寅', 'day': '戊戌'})
        fire = group(out, 'BZ-S03', '寅午戌')
        self.assertIs(fire['complete'], True)
        self.assertIs(fire['natalAlreadyComplete'], False)
        self.assertIs(fire['flowDayNecessary'], True)
        self.assertEqual(fire['members']['戌'], ['flow.day'])

    def test_sanhe_already_in_natal_is_not_completed_by_flow_day(self):
        fixture = {
            'id': 'SYNTHETIC_VALID_PILLARS_NOT_BIRTH_EVENT', 'entityType': 'asset',
            'pillars': {'year': '甲子', 'month': '丙寅', 'day': '甲午', 'hour': '甲戌'},
        }
        out = result(profile=fixture, flow={'year': '甲子', 'month': '丙寅', 'day': '戊戌'})
        fire = group(out, 'BZ-S03', '寅午戌')
        self.assertIs(fire['complete'], True)
        self.assertIs(fire['natalAlreadyComplete'], True)
        self.assertIs(fire['flowDayNecessary'], False)
        self.assertIs(fire['completedWithFlow'], False)

    def test_duplicate_branch_never_replaces_missing_third_member(self):
        tokens = [{'slot': slot, 'branch': branch} for slot, branch in (
            ('natal.year', '寅'), ('natal.month', '寅'), ('natal.day', '午'),
            ('natal.hour', '午'), ('flow.year', '子'), ('flow.month', '丑'),
            ('flow.day', '寅'))]
        fire = next(x for x in group_states(tokens, '三合') if x['pattern'] == '寅午戌')
        self.assertIs(fire['complete'], False)
        self.assertIs(fire['flowDayNecessary'], False)
        self.assertEqual(fire['missingBranches'], ['戌'])
        self.assertEqual(len(fire['members']['寅']), 3)

    def test_hour_cannot_complete_water_group_or_change_any_feature(self):
        base = {'year': '甲子', 'month': '丙寅', 'day': '己卯'}
        without = result(flow=base)
        with_hour = result(flow={**base, 'hour': '戊辰'})
        self.assertEqual(without, with_hour)
        water = group(with_hour, 'BZ-S03', '申子辰')
        self.assertIs(water['complete'], False)
        self.assertEqual(water['missingBranches'], ['辰'])
        self.assertIn('flow.hour', with_hour['scope']['excluded'])

    def test_luck_is_excluded_from_exposure_and_groups(self):
        base = {'year': '甲子', 'month': '丙寅', 'day': '己卯'}
        self.assertEqual(result(flow=base), result(flow={**base, 'luck': '癸巳'}))

    def test_earth_wealth_has_unknown_storage_instead_of_false(self):
        fixture = {
            'id': 'SYNTHETIC_VALID_PILLARS_NOT_BIRTH_EVENT', 'entityType': 'asset',
            'pillars': {'year': '甲子', 'month': '丙寅', 'day': '甲辰', 'hour': '甲戌'},
        }
        out = result(profile=fixture)
        treasury = out['treasury']
        self.assertEqual(treasury['wealthElement'], '土')
        self.assertIsNone(treasury['abstractStorageBranch'])
        self.assertIsNone(treasury['natalStoragePresent'])
        self.assertIsNone(treasury['natalStorageOpposedByFlowDay'])
        self.assertEqual(feature(out, 'BZ-S05')['status'], 'unknown')
        self.assertEqual(feature(out, 'BZ-S06')['status'], 'unknown')

    def test_union_and_opposition_survive_together(self):
        out = result('ETH-BAZI-B', flow={'year': '甲子', 'month': '丙寅', 'day': '己丑'})
        self.assertIs(feature(out, 'BZ-S01')['value'], True)
        self.assertIs(feature(out, 'BZ-S02')['value'], True)
        self.assertEqual(feature(out, 'BZ-S01')['evidence'],
                         [{'pattern': '子丑', 'slots': ['natal.month', 'flow.day']}])
        self.assertEqual(feature(out, 'BZ-S02')['evidence'],
                         [{'pattern': '丑未', 'slots': ['natal.day', 'flow.day']}])

    def test_repeated_natal_zis_produce_two_located_clashes(self):
        out = result(flow={'year': '甲子', 'month': '丙寅', 'day': '庚午'})
        clashes = feature(out, 'BZ-S02')['evidence']
        self.assertEqual(clashes, [
            {'pattern': '子午', 'slots': ['natal.year', 'flow.day']},
            {'pattern': '子午', 'slots': ['natal.month', 'flow.day']},
        ])
        self.assertIsNone(feature(out, 'BZ-S02')['marketDirection'])

    def test_pinned_si_hidden_stem_order_and_no_weight_fields(self):
        self.assertEqual(HIDDEN_STEMS['巳'], '丙庚戊')
        out = result('ETH-BAZI-B')
        self.assertEqual([x['stem'] for x in out['hiddenStems'] if x['slot'] == 'natal.year'],
                         ['丙', '庚', '戊'])
        self.assertTrue(all('weight' not in x for x in out['hiddenStems']))

    def test_incomplete_flow_is_rejected_instead_of_partial_evaluation(self):
        relations = calculate({'schemaVersion': 1, 'profile': PROFILES['BTC-BZ-V01'],
                               'flow': {'day': '庚戌'}})
        with self.assertRaisesRegex(ValueError, '完整原局四柱'):
            calculate_features(relations, CALENDAR)

    def test_source_inputs_are_not_mutated(self):
        relations = calculate({'schemaVersion': 1, 'profile': PROFILES['BTC-BZ-V01'],
                               'flow': {'year': '甲子', 'month': '丙寅', 'day': '庚戌'}})
        original = copy.deepcopy(relations)
        calendar = copy.deepcopy(CALENDAR)
        calculate_features(relations, calendar)
        self.assertEqual(relations, original)
        self.assertEqual(calendar, CALENDAR)


if __name__ == '__main__':
    unittest.main(verbosity=2)
