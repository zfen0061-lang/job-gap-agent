import json
import unittest
from unittest.mock import Mock

from agent import Analysis, analyze, build_report, compare_resumes, demo_analysis_json, evidence_fragments, jd_text, markdown_report, my_resume, offline_request
from evals import run_revision_evals
from jd_sources import Job
from report_features import CandidateProfile, HardRequirement, eligibility_report, resume_suggestions, resume_text_changed, rewrite_resume_line, validate_requirement


class NewReportTests(unittest.TestCase):
    def setUp(self):
        self.job = Job('AI Agent 实习', jd_text)
        self.draft = Analysis.model_validate_json(demo_analysis_json())

    def test_rewrite_rephrases_a_grounded_project_sentence(self):
        raw = self.draft.model_dump()
        raw['resume_edits'] = [{'job_id': 1, 'skill': 'Python', 'action': 'promote'}]
        raw['skills'][0]['resume_evidence'] = '使用 Python 采集配置记录并去重。'
        resume = raw['skills'][0]['resume_evidence']
        result, _ = analyze([self.job], resume, lambda _: json.dumps(raw))
        items = resume_suggestions(resume, [self.job], result.skills, result.resume_edits)
        self.assertEqual(items[0]['original_text'], resume)
        self.assertEqual(items[0]['suggested_text'], '借助 Python 对配置记录进行采集和去重。')
        self.assertEqual(items[0]['supporting_fact'], resume)
        self.assertNotIn('RAG', [item['skill'] for item in items])

    def test_model_cannot_supply_an_unverified_rewrite(self):
        raw = self.draft.model_dump()
        raw['resume_edits'] = [{'job_id': 1, 'skill': 'Python', 'action': 'emphasize', 'suggested_text': '上线了 100 个 RAG Agent'}]
        with self.assertRaisesRegex(ValueError, '3 次输出.*suggested_text'):
            analyze([self.job], my_resume, lambda _: json.dumps(raw))
        self.assertTrue(run_revision_evals())

    def test_preserve_mode_does_not_display_an_identical_rewrite(self):
        resume = '熟练 Python。'
        report = build_report(self.draft, [self.job], self.job.title, '测试', 1, [], resume=resume)
        item = report['resume_suggestions'][0]
        self.assertIsNone(item['suggested_text'])
        for suggested in (None, '**熟练 Python。**', '熟练 Python！'):
            item['suggested_text'] = suggested
            section = markdown_report(report).split('## 简历修改建议', 1)[1].split('## 优先补强的技能', 1)[0]
            self.assertIn('可保留的原句「熟练 Python。」', section)
            self.assertNotIn('建议写法', section)
            self.assertEqual(section.count('熟练 Python。'), 1)

    def test_rewrites_keep_skills_and_qualification_levels(self):
        source = 'AI 与大模型 熟悉 Prompt Engineering 与结构化输出，了解 RAG、ReAct、LLM API 接入及 Evals。'
        suggested, _ = rewrite_resume_line(source)
        self.assertEqual(suggested, 'AI 与大模型 熟悉的方向包括 Prompt Engineering 与结构化输出。对 RAG、ReAct、LLM API 接入及 Evals 有基础了解。')
        self.assertTrue(resume_text_changed(source, suggested))
        source = '工程与可靠性 异常降级、幂等、断网恢复与人工核对。使用 Git、pytest、Docker。'
        suggested, _ = rewrite_resume_line(source)
        self.assertEqual(suggested, '工程工具包括 Git、pytest、Docker。可靠性相关能力包括异常降级、幂等、断网恢复与人工核对。')
        self.assertEqual(rewrite_resume_line('先完成原型。随后使用 Git 提交。')[0], None)
        self.assertFalse(resume_text_changed('熟悉 Git。了解 RAG。', '了解 RAG；熟悉 Git。'))

    def test_markdown_shows_summary_and_rewrites_before_reference_details(self):
        draft = self.draft.model_copy(deep=True)
        original = '使用 Python 采集配置记录并去重。'
        draft.skills[0].resume_evidence = original
        report = build_report(draft, [self.job], self.job.title, '测试', 1, [], resume=original)
        markdown = markdown_report(report)
        sections = ['结果速览', '简历修改建议', '优先补强的技能', '岗位硬条件明细', '岗位来源与证据']
        positions = [markdown.index(f'## {section}') for section in sections]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('**简历** 1 条改写建议', markdown)
        self.assertIn(f'**原句**\n\n> {original}', markdown)
        self.assertIn('**建议写法**\n\n> 借助 Python 对配置记录进行采集和去重。', markdown)
        self.assertNotIn('**支撑事实**', markdown)

    def test_eligibility_checks_known_values_and_leaves_missing_values_pending(self):
        draft, attempts = analyze([self.job], my_resume, offline_request())
        profile = CandidateProfile(degree='bachelor', graduation_year=2028, cities=['深圳市'], days_per_week=3)
        report = build_report(draft, [self.job], self.job.title, '测试', attempts, [], resume=my_resume, profile=profile)
        result = report['eligibility'][0]
        statuses = {item['label']: item['status'] for item in result['conditions']}
        self.assertEqual(statuses, {'学历': '符合', '毕业届次': '符合', '工作地点': '符合', '每周出勤': '不符合', '实习时长': '待确认'})
        self.assertEqual(result['status'], '不符合已披露条件')
        self.assertEqual(report['match_percentage'], 33.3)
        no_conditions = eligibility_report([self.job], [], [evidence_fragments(jd_text)], profile)
        self.assertEqual(no_conditions[0]['status'], '待确认')

    def test_requirement_rejects_a_mismatched_number_and_range_upper_bound(self):
        duration = HardRequirement(job_id=1, kind='months', values=['6'], jd_evidence_id=1)
        with self.assertRaisesRegex(ValueError, '必须可在'):
            validate_requirement(duration, '连续实习 3–6 个月。')
        validate_requirement(duration.model_copy(update={'values': ['3']}), '连续实习 3–6 个月。')
        with self.assertRaises(ValueError):
            validate_requirement(duration.model_copy(update={'values': ['3']}), '最多实习 3 个月。')

    def test_compare_uses_identical_rubric_and_rejects_changed_ids(self):
        revised = '了解 Python。实现过带引用的 RAG 问答。'
        before = self.draft.model_dump()
        requested = []

        def request(messages):
            payload = json.loads(messages[1]['content'])
            requested.append(payload['rubric'])
            self.assertNotIn('profile', payload)
            assessments = [
                {'skill_id': 1, 'status': 'partial', 'resume_evidence': '了解 Python'},
                {'skill_id': 2, 'status': 'missing', 'resume_evidence': None},
                {'skill_id': 99 if len(messages) == 2 else 3, 'status': 'matched', 'resume_evidence': '实现过带引用的 RAG 问答'},
            ]
            return json.dumps({'assessments': assessments})

        result = compare_resumes(self.draft, my_resume, revised, request)
        self.assertEqual((result['before'], result['after'], result['delta']), (33.3, 50.0, 16.7))
        self.assertEqual(result['model_attempts'], 2)
        self.assertEqual(requested[0], requested[1])
        self.assertEqual([item['skill'] for item in result['changes']], ['Python', 'RAG'])
        self.assertEqual(before, self.draft.model_dump())

    def test_identical_resumes_do_not_call_the_model_again(self):
        request = Mock()
        result = compare_resumes(self.draft, my_resume, my_resume, request)
        self.assertEqual(result['delta'], 0.0)
        self.assertEqual(result['changes'], [])
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
