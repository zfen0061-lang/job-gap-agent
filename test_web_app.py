import io
import unittest
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

from agent import demo_analysis_json, jd_text, my_resume
from jd_sources import Job
from web_app import create_app


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app().test_client()

    def test_demo_contains_a_reviewable_rewrite_without_a_key(self):
        with patch('web_app.read_api_key', side_effect=AssertionError('demo must run offline')):
            with self.client.get('/') as page:
                self.assertIn('投递前体检', page.get_data(as_text=True))
            demo = self.client.get('/api/demo')
        result = demo.get_json()
        self.assertEqual(demo.status_code, 200)
        self.assertEqual(demo.headers['Cache-Control'], 'no-store')
        self.assertEqual(result['report']['job_check']['risk'], '存在明确硬条件冲突')
        self.assertTrue(any(item['suggested_text'] for item in result['report']['resume_suggestions']))

    def test_upload_previews_text_and_explains_scanned_pdf(self):
        text = self.client.post('/api/extract', data={'resume': (io.BytesIO('熟悉 Python。'.encode()), 'resume.txt')})
        self.assertEqual(text.get_json(), {'text': '熟悉 Python。'})
        pdf = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(pdf)
        pdf.seek(0)
        response = self.client.post('/api/extract', data={'resume': (pdf, 'scanned.pdf')})
        self.assertEqual(response.status_code, 400)
        self.assertIn('文字层', response.get_json()['error'])

    def test_fetch_error_preserves_manual_jd_as_a_separate_path(self):
        url = 'https://jobs.bytedance.com/campus/position/7644766035905136901/detail'
        with patch('web_app.read_api_key', return_value='test'), patch(
            'web_app.DeepSeekJobSource.fetch_job', side_effect=[RuntimeError('读取超时'), Job('岗位名称', jd_text, url)],
        ):
            error = self.client.post('/api/job', json={'url': url})
            fetched = self.client.post('/api/job', json={'url': url})
        self.assertEqual(error.status_code, 502)
        self.assertEqual(fetched.get_json(), {'title': '岗位名称', 'text': jd_text, 'url': url})

    def test_manual_jd_produces_a_report_without_sending_profile_to_the_model(self):
        def mock_model(messages):
            self.assertNotIn('2029', messages[1]['content'])
            return demo_analysis_json()

        with patch('web_app.read_api_key', return_value='test'), patch('web_app.model_request', return_value=mock_model):
            response = self.client.post('/api/check', json={
                'title': '手工导入的岗位', 'jd': jd_text, 'resume': my_resume, 'profile': {'graduation_year': 2029},
            })
        self.assertEqual(response.status_code, 200)
        report = response.get_json()['report']
        self.assertEqual(report['target'], '手工导入的岗位')
        self.assertEqual(report['jd_count'], 1)
        self.assertIn('keywords', report['job_check'])

    def test_export_returns_a_pdf_with_selectable_approved_text(self):
        resume = '示例候选人\n专业技能\n使用 Python <工具> 与 Pydantic。\n离线 Evals 评测。'
        response = self.client.post('/api/export', json={'resume': resume})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertTrue(response.headers['Content-Disposition'].startswith('attachment'))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        reader = PdfReader(io.BytesIO(response.data))
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        for phrase in ('示例候选人', '专业技能', 'Python', '工具', 'Pydantic', 'Evals'):
            self.assertIn(phrase, text)

    def test_export_rejects_empty_and_oversize_content(self):
        for resume in ('  ', 'A' * 16001):
            with self.subTest(size=len(resume)):
                self.assertEqual(self.client.post('/api/export', json={'resume': resume}).status_code, 400)
