import json
import unittest
from unittest.mock import Mock

from agent import demo_analysis_json, evidence_fragments, jd_text
from jd_sources import Job
from live_job_evals import Case, evaluate


class LiveJobEvaluationsTests(unittest.TestCase):
    def setUp(self):
        self.case = Case(url='https://jobs.bytedance.com/campus/position/7644766035905136901/detail',
                         jd_term='Python', cohort=2027)
        self.body = jd_text.replace('面向2027届、2028届。', '面向2027届。')

    def test_fetch_error_is_distinct_from_model_evaluation_failure(self):
        source = Mock()
        source.fetch_job.side_effect = RuntimeError('网页正文读取失败')
        provider = Mock()
        result = evaluate(self.case, source, provider)
        self.assertFalse(result['fetched'])
        self.assertFalse(result['passed'])
        self.assertIn('读取失败', result['error'])
        provider.assert_not_called()

    def test_report_without_expected_cohort_is_detected_as_a_miss(self):
        source = Mock()
        source.fetch_job.return_value = Job('测试岗位', self.body, self.case.url)
        raw = json.loads(demo_analysis_json())
        raw['skills'][0]['resume_evidence'] = '熟悉 Python'
        result = evaluate(self.case, source, lambda _: json.dumps(raw))
        self.assertTrue(result['fetched'])
        self.assertFalse(result['passed'])
        self.assertTrue(result['checks']['expected_term_in_evidence'])
        self.assertFalse(result['checks']['cohort_conflict'])

    def test_recognizes_grounded_cohort_conflict(self):
        source = Mock()
        source.fetch_job.return_value = Job('测试岗位', self.body, self.case.url)
        raw = json.loads(demo_analysis_json())
        raw['skills'][0]['resume_evidence'] = '熟悉 Python'
        evidence_id = next(i for i, piece in enumerate(evidence_fragments(self.body), 1) if '2027届' in piece)
        raw['requirements'] = [{'job_id': 1, 'kind': 'graduation_year', 'values': ['2027'], 'jd_evidence_id': evidence_id}]
        result = evaluate(self.case, source, lambda _: json.dumps(raw))
        self.assertTrue(result['passed'])
        self.assertTrue(result['checks']['cohort_conflict'])
