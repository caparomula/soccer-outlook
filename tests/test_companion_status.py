"""Independent status-oracle integrity, defect separation and mutation checks."""
from copy import deepcopy
import unittest

from tool.companion_p2_verify import assert_response
from tool.companion_status_verify import declared_corrections, event_from_case, invalid_requests, load_checks, parse_args


class StatusEvidence(unittest.TestCase):
    def test_portable_corpus_keeps_ordinary_and_defect_answers_separate(self):
        self.assertEqual(parse_args([]).browser, "both")
        checks, baseline = load_checks()
        self.assertEqual(len(baseline['cases']), 369)
        legacy = next(check for check in checks if check['id'].startswith('legacy:'))
        self.assertEqual(len(legacy['expected']['results']), 360)
        self.assertEqual(len(declared_corrections()), 9)
        defect = next(case for case in baseline['cases'] if case['id'] == 'synthetic:empty-terminal')
        self.assertEqual(defect['browser']['label'], 'FT')
        corrected = next(case for case in declared_corrections() if case['id'] == defect['id'])
        self.assertFalse(corrected['final'])
        self.assertEqual(corrected['label'], 'Status unknown')

    def test_literal_scores_preserve_numeric_zero_and_absence(self):
        checks, _ = load_checks()
        by_id = {check['id']:check for check in checks}
        zero = by_id['independent:score-shape-and-zero']['expected']['results'][0]
        self.assertEqual(zero['reported_scores'], {'home':0,'away':None})
        self.assertEqual(zero['scores'], {'home':0,'away':None})
        cancelled = by_id['independent:exception-before-label']['expected']['results'][0]
        self.assertEqual(cancelled['reported_scores'], {'home':0,'away':0})
        self.assertEqual(cancelled['scores'], {'home':None,'away':None})

    def test_oracle_rejects_final_invention_status_conflicts_and_score_coercion(self):
        checks, _ = load_checks(); by_id = {check['id']:check for check in checks}
        for group, index, changes in (
            ('unknown-terminal-not-a-final', 1, dict(state='finished', final=True, played=True, label='FT')),
            ('exception-before-label', 0, dict(state='finished', scores={'home':0,'away':0})),
            ('status-identity-conflicts', 0, dict(state='cancelled', void=True)),
            ('score-shape-and-zero', 0, dict(scores={'home':None,'away':None})),
            ('score-rejection-boundaries', 0, dict(scores={'home':1,'away':-1})),
            ('source-selection-without-field-merging', 3, dict(state='live', phase='in', played=True)),
            ('fixture-seam-unknown-and-conflict', 3, dict(state='finished', phase='post', final=True)),
            ('raw-live-display-is-not-status-identity', 1, dict(state='postponed', phase='post', played=False)),
            ('bound-normalized-state-survives-display-collision', 0, dict(state='finished', phase='post', final=True)),
            ('bound-normalized-state-survives-display-collision', 4, dict(state='live', phase='in', played=True)),
        ):
            with self.subTest(group=group):
                check = by_id['independent:' + group]; mutant = deepcopy(check['expected'])
                mutant['results'][index].update(changes)
                with self.assertRaises(AssertionError):
                    assert_response(check, mutant)

    def test_raw_source_fixture_mapping_preserves_exact_status_and_score_objects(self):
        _, baseline = load_checks()
        case = next(case for case in baseline['cases'] if case['id'] == 'synthetic:object-zero')
        original = deepcopy(case)
        event = event_from_case(case); comp = event['competitions'][0]
        self.assertEqual(event['id'], 'object-zero')
        self.assertEqual(comp['status'], case['input']['competition_status'])
        self.assertEqual(comp['competitors'][0]['score'], {'displayValue':'0'})
        self.assertIsNone(comp['competitors'][1]['score'])
        self.assertEqual(case, original)

    def test_invalid_frames_do_not_mislabel_arbitrary_source_json_as_protocol_error(self):
        checks, _ = load_checks()
        invalid = invalid_requests(checks)
        self.assertEqual(len(invalid), 8)
        missing = next(value for value in invalid if 'competition_status' not in (value['cases'][0] or {}))
        restored = deepcopy(missing['cases'][0])
        restored['competition_status'] = checks[0]['request']['cases'][0]['competition_status']
        self.assertEqual(restored, checks[0]['request']['cases'][0])


if __name__ == '__main__':
    unittest.main()
