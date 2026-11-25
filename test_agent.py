import io
import json
import os
import sqlite3
import sys
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from agent import Job, analyze, build_report, demo_analysis_json, jd_text, main, model_request, my_resume, read_api_key
from evals import eval_cases, run_evals
from jd_sources import DeepSeekJobSource, canonical_url, load_jobs


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.jobs = [Job('模拟岗位', jd_text)]
        self.valid = demo_analysis_json()

    def test_retries_invalid_json_and_corrects_score_locally(self):
        draft = json.loads(self.valid)
        draft['match_percentage'] = 100

        def reply(messages):
            if len(messages) == 2:
                return '不是 JSON'
            self.assertIn('Invalid JSON', messages[-1]['content'])
            return json.dumps(draft)

        accepted, attempts = analyze(self.jobs, my_resume, reply)
        report = build_report(accepted, self.jobs, 'Agent 实习', '离线测试', attempts, [])
        self.assertEqual(attempts, 2)
        self.assertEqual(report['match_percentage'], 33.3)
        self.assertEqual(report['salary'][0]['amount'], '200-300元/天')

    def test_invalid_citations_stop_after_three_calls(self):
        draft = json.loads(self.valid)
        draft['skills'][0]['resume_evidence'] = '部署过生产 Agent'
        call = Mock(return_value=json.dumps(draft))
        with self.assertRaisesRegex(ValueError, '3 次输出.*resume_evidence'):
            analyze(self.jobs, my_resume, call)
        self.assertEqual(call.call_count, 3)

    def test_numbered_jd_evidence_is_checked_and_resolved_to_source_text(self):
        draft = json.loads(self.valid)
        draft['skills'][0]['jd_evidence_id'] = 999
        draft['skills'][0]['jd_evidence'] = None

        def reply(messages):
            data = json.loads(messages[1]['content'])
            fragments = data['jd_text'][0]['evidence_fragments']
            if len(messages) > 2:
                self.assertIn('jd_evidence_id', messages[-1]['content'])
                draft['skills'][0]['jd_evidence_id'] = int(next(k for k, text in fragments.items() if '要求熟练 Python' in text))
            return json.dumps(draft)

        result, attempts = analyze(self.jobs, my_resume, reply)
        self.assertEqual(attempts, 2)
        self.assertIn('要求熟练 Python', result.skills[0].jd_evidence)
        self.assertIn(result.skills[0].jd_evidence, jd_text)

    def test_requires_evidence_for_every_job(self):
        call = Mock(return_value=self.valid)
        with self.assertRaisesRegex(ValueError, '检查 JD 2'):
            analyze(self.jobs * 2, my_resume, call)

    def test_gap_and_salary_from_ten_distinct_source_documents(self):
        jobs = load_jobs(Path(__file__).with_name('sample_jobs.json'), 10)
        self.assertEqual(len({job.url for job in jobs}), 10)
        draft = {
            'skills': [{'job_id': i, 'name': '岗位技能', 'priority': 'required', 'status': 'missing',
                        'jd_evidence': job.text.split('。')[0], 'resume_evidence': None}
                       for i, job in enumerate(jobs, 1)],
            'salary': [{'job_id': i, 'amount': None} for i in range(1, 11)],
            'match_percentage': 0,
            'gap_analysis': [{'skill': '岗位技能', 'project': '测试项目', 'deliverable': '验证记录'}],
        }
        result, attempts = analyze(jobs, my_resume, lambda _: json.dumps(draft))
        report = build_report(result, jobs, '批量数据测试', '测试', attempts, [])
        self.assertEqual(report['gap_analysis'][0]['jd_count'], 10)
        self.assertEqual(report['gap_analysis'][0]['required_gap_count'], 10)
        self.assertEqual(len(report['sources']), 10)

    def test_sdk_request_uses_deepseek_and_json_mode(self):
        sdk = ModuleType('openai')
        sdk.APIError = type('APIError', (Exception,), {})
        create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.valid))]))
        sdk.OpenAI = Mock(return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
        with patch.dict(sys.modules, {'openai': sdk}):
            analyze(self.jobs, my_resume, model_request('test-key', 'deepseek-flash'))
        self.assertEqual(sdk.OpenAI.call_args.kwargs['base_url'], 'https://api.deepseek.com')
        self.assertEqual(sdk.OpenAI.call_args.kwargs['max_retries'], 0)
        self.assertEqual(create.call_args.kwargs['response_format'], {'type': 'json_object'})


class ConfigTests(unittest.TestCase):
    def test_project_config_and_environment_precedence(self):
        with TemporaryDirectory() as directory, patch('agent.__file__', str(Path(directory) / 'agent.py')):
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}):
                self.assertEqual(read_api_key(), '')
                (Path(directory) / '.env').write_text('DEEPSEEK_API_KEY="test-local"\n', encoding='utf-8-sig')
                self.assertEqual(read_api_key(), 'test-local')
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-session'}):
                self.assertEqual(read_api_key(), 'test-session')


class SearchTests(unittest.TestCase):
    def test_official_listing_urls_are_rejected_and_referrals_are_removed(self):
        self.assertIsNone(canonical_url('https://jobs.bytedance.com/campus/position?referral_code=abc'))
        self.assertIsNone(canonical_url('https://careers.tencent.com/search.html?keyword=agent'))
        detail = 'https://jobs.bytedance.com/campus/position/7644766035905136901/detail'
        self.assertEqual(canonical_url(detail + '?referral_code=abc'), detail)

    def test_filters_promotions_deduplicates_fetches_and_reports_shortfall(self):
        source = DeepSeekJobSource('test-key')
        body = ('AI Agent 开发实习生（J10001）\n岗位职责：开发 Python 工具并维护 Agent。\n'
                '任职要求：具有服务开发、工具链维护和调试经验，了解函数调用与检索增强，能编写用例并分析评估失败原因。'
                '可参与应用服务实现、项目文档和团队代码复核。')
        baidu = 'https://talent.baidu.com/jobs/detail/INTERN/162291c8-43e8-4cc0-9c96-3fb0a7ebb720'
        bytedance = 'https://jobs.bytedance.com/campus/position/7644766035905136901/detail'
        source.post = Mock(side_effect=[
            {'results': [{'url': baidu + '?utm_source=test', 'title': '百度招聘', 'content': body},
                         {'url': baidu, 'title': '重复项', 'content': body},
                         {'url': 'https://example.com/job/agent', 'title': '聚合页', 'content': body},
                         {'url': bytedance, 'title': 'Agent 实习生', 'content': ''}]},
            {'results': []},
            {'results': [{'url': 'https://careers.tencent.com/search.html?keyword=agent', 'title': '招聘列表', 'content': body}]},
            {'title': 'Agent 实习生', 'content': body.replace('J10001', 'J10002').replace('Python 工具', 'TypeScript 工具')},
        ])
        jobs, notices = source.search('AI Agent 实习', 10)
        self.assertEqual([job.url for job in jobs], [baidu, bytedance])
        self.assertEqual(source.post.call_args.args, ('fetch', {'url': bytedance}))
        self.assertIn('实际纳入 2 条', notices[-1])


class StorageTests(unittest.TestCase):
    def test_normal_cli_persists_the_exported_report(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(sys, 'argv', ['agent.py', '--offline', '--out-dir', directory]), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            exported = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            with closing(sqlite3.connect(output / 'career_analysis.sqlite3')) as connection:
                rows = connection.execute('SELECT match_percentage, model_attempts, report_json FROM career_analysis').fetchall()
            self.assertEqual(len(rows), 1)
            score, attempts, payload = rows[0]
            self.assertEqual((score, attempts), (33.3, 2))
            self.assertEqual(json.loads(payload), exported)

    def test_evals_retries_and_keeps_history(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'eval.sqlite3'
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(run_evals(database), (3, 3, 1))
                self.assertEqual(run_evals(database), (3, 3, 1))
            self.assertIn('一共处理 3 条，成功入库 3 条，触发重试 1 次。', output.getvalue())
            with closing(sqlite3.connect(database)) as connection:
                rows = connection.execute('SELECT match_percentage, model_attempts FROM career_analysis ORDER BY id').fetchall()
            self.assertEqual(rows, [(100.0, 1), (33.3, 1), (75.0, 2)] * 2)

    def test_evals_counts_failed_retries_without_inserting_a_failed_response(self):
        cases = eval_cases()
        broken = replace(cases[2], responses=('乱码',))
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'eval.sqlite3'
            with patch('evals.eval_cases', return_value=(*cases[:2], broken)), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(run_evals(database), (3, 2, 2))
            with closing(sqlite3.connect(database)) as connection:
                rows = connection.execute('SELECT target FROM career_analysis').fetchall()
            self.assertEqual(rows, [(cases[0].job.title,), (cases[1].job.title,)])

    def test_live_evals_calls_model_once_per_case_and_injects_one_failure(self):
        request = Mock(side_effect=[case.responses[-1] for case in eval_cases()])
        with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            database = Path(directory) / 'live.sqlite3'
            self.assertEqual(run_evals(database, request), (3, 3, 1))
            with closing(sqlite3.connect(database)) as connection:
                rows = connection.execute('SELECT DISTINCT mode FROM career_analysis').fetchall()
            self.assertEqual(rows, [('DeepSeek 在线 Evals',)])
        self.assertEqual(request.call_count, 3)
        self.assertIn('未通过校验', request.call_args.args[0][-1]['content'])


if __name__ == '__main__':
    unittest.main()
