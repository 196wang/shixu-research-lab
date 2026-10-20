"""Persistence and evidence-contract checks for the new feature snapshot."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from app_server import calculate, encode, load, Store


class V07IntegrationTests(unittest.TestCase):
    def test_saved_snapshot_includes_features_and_keeps_them_after_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'records.sqlite3')
            initial = store.add({'profile': 'ETH-BAZI-B', 'at': '2002-03-10T18:30:09+08:00',
                                 'note': 'isolated test', 'judgement': 'observation'})
            snapshot = initial['snapshot']
            self.assertTrue(snapshot['bazi']['features']['treasury']['natalStorageOpposedByFlowDay'])
            self.assertIsNone(snapshot['bazi']['features']['treasury']['openingJudgement'])
            body = copy.deepcopy(snapshot)
            digest = body.pop('snapshotSha256')
            self.assertEqual(digest, hashlib.sha256(encode(body).encode()).hexdigest())
            reviewed = store.review(initial['id'], {'outcome': 'ambiguous', 'note': 'isolated test'})
            self.assertEqual(reviewed['snapshot'], snapshot)

    def test_catalog_and_computation_reference_same_six_rules(self):
        catalog = load('definition_catalog.json')
        result = calculate('BTC-BZ-V01', '2002-03-10T18:30:09+08:00')
        features = result['bazi']['features']
        self.assertEqual({f['id'] for f in catalog['featureDefinitions']}, {f['id'] for f in features['features']})
        self.assertEqual(set(features['sourceRefs']), {s['id'] for s in catalog['sources']})
        self.assertFalse(catalog['nextStudy']['protocol']['scoringRun'])
        self.assertFalse(catalog['nextStudy']['protocol']['independentHoldout'])

    def test_eth_boundary_comparison_does_not_replace_profiles(self):
        event = next(e for e in load('boundary_comparison.json') if e['label'].startswith('ETH'))
        profiles = {p['id']: p for p in load('profiles.json')['profiles']}
        self.assertEqual(event['midnight']['day'], '丁未')
        self.assertEqual(event['zi23'], profiles['ETH-BAZI-A']['pillars'])
        self.assertFalse(event['sourceSettingConfirmed'])
        self.assertEqual(profiles['BTC-BZ-V01']['pillars']['day'], '戊申')


if __name__ == '__main__':
    unittest.main(verbosity=2)
