import json
import unittest
from unittest.mock import Mock, patch

from agent import analyze, jd_text, my_resume, offline_request
from jd_sources import Job
from job_discovery import baidu_internships, discover_jobs, fetch_public_job, official_detail, public_link, tencent_internships
from job_recommendations import rank_jobs
from report_features import CandidateProfile
from resume_review import review_resume, same_facts


class DiscoveryTests(unittest.TestCase):
    def test_public_url_rules_and_single_fetch(self):
        self.assertIsNone(public_link('https://127.0.0.1/job/detail'))
        self.assertIsNone(public_link('https://example.com/job?access_token=secret'))
        link = 'https://www.zhipin.com/job_detail/abc123.html'
        self.assertIsNone(official_detail(link))
        self.assertIsNone(official_detail('https://jobs.bytedance.com/campus/position'))
        provider = Mock()
        provider.post.return_value = {'title': 'Agent 实习生', 'content': '任职要求：' + jd_text}
        self.assertEqual(fetch_public_job(provider, link).url, link)
        provider.post.assert_called_once_with('fetch', {'url': link})

    def test_auto_search_deduplicates_and_excludes_nonofficial_pages(self):
        provider = Mock()
        url = 'https://talent.baidu.com/jobs/detail/INTERN/162291c8-43e8-4cc0-9c96-3fb0a7ebb720'
        provider.post.return_value = {'results': [
            {'url': url, 'title': 'Agent 实习生', 'content': '任职要求：' + jd_text},
            {'url': url + '?utm_source=a', 'title': 'Agent 实习生', 'content': '任职要求：' + jd_text},
            {'url': 'https://example.net/job/detail/123456', 'title': 'Agent 实习生', 'content': jd_text},
        ]}
        with patch('job_discovery.tencent_internships', return_value=[]), patch('job_discovery.baidu_internships', return_value=[]):
            _, jobs, _ = discover_jobs(provider, '做过 Python Agent。私人履历识别串。')
        self.assertEqual([job.url for job in jobs], [url])
        self.assertTrue(all(len(call.args[1]['query']) <= 128 and '私人' not in call.args[1]['query']
                            for call in provider.post.call_args_list))

    def test_tencent_json_keeps_origin_url_and_location(self):
        link = 'https://tencent.wd1.myworkdayjobs.com/Tencent_Careers/job/Example/Research-Intern_R1'
        with patch('job_discovery.tencent_json', side_effect=[
            {'Posts': [{'PostId': '1234567890', 'RecruitPostName': 'Agent Research Intern'}]},
            {'RecruitPostName': 'Agent Research Intern', 'PostURL': link, 'LocationName': '伦敦',
             'Responsibility': jd_text, 'Requirement': '熟悉 Python。'},
        ]):
            jobs = tencent_internships('AI Agent 应用研发实习')
        self.assertEqual((jobs[0].url, jobs[0].retrieval), (link, 'tencent-json'))
        self.assertIn('伦敦', jobs[0].text)

    def test_baidu_state_produces_official_internship_detail(self):
        with patch('job_discovery.baidu_state', return_value={'listData': {'listDetailData': [{
            'name': '智能体应用研发', 'postId': 'e3cec5b8-b7a3-4946-99fc-b292b749cd53',
            'workPlace': '北京市', 'workContent': jd_text, 'serviceCondition': '熟悉 Python。'
        }]}}):
            job, = baidu_internships('AI Agent 应用研发实习')
        self.assertEqual(job.retrieval, 'baidu-page-json')
        self.assertIn('/jobs/detail/INTERN/', job.url)
        self.assertIn('北京市', job.text)

    def test_clear_conflict_has_zero_stars_even_with_skills(self):
        job = Job('Agent 实习', jd_text)
        draft, attempts = analyze([job], my_resume, offline_request())
        cards = rank_jobs([job], draft, my_resume, CandidateProfile(graduation_year=2029), attempts)
        self.assertEqual(cards[0]['stars'], 0)
        self.assertIn('Python', cards[0]['match_evidence'][0]['skill'])


class ResumeReviewTests(unittest.TestCase):
    def test_preserves_metrics_and_qualifiers(self):
        line = '使用 Python 验证至少 23 项行为，参与 GitLab CI。'
        self.assertTrue(same_facts(line, '通过 Python 验证至少 23 项行为，并参与 GitLab CI。'))
        self.assertFalse(same_facts(line, '通过 Python 验证 30 项行为，主导 GitLab CI。'))
        self.assertFalse(same_facts(line, '通过 Python 和 Rust 验证至少 23 项行为，参与 GitLab CI。'))

    def test_multiline_edit_keeps_offsets_and_original_number(self):
        resume = '项目经历\n1. 使用 Python 对公开岗位去重，\n测试了 10 条记录。\n教育背景'
        provider = lambda _: json.dumps({'edits': [{'block_id': 1, 'revision': '用 Python 对公开岗位去重，并测试了 10 条记录。'}]})
        result = review_resume(resume, 'AI 应用实习', '', provider)
        edit = result['edits'][0]
        self.assertEqual((edit['start_line'], edit['end_line']), (1, 3))
        self.assertTrue(edit['suggested_text'].startswith('1. '))
