import json
import os
from io import BytesIO
from pathlib import Path

from flask import Flask, request, send_file, send_from_directory
from pydantic import Field, ValidationError
from werkzeug.exceptions import RequestEntityTooLarge

from agent import analyze, build_report, jd_text, model_request, offline_request, read_api_key, read_resume_pdf
from jd_sources import JD_TEXT_FALLBACK, DeepSeekJobSource, Job
from job_discovery import fetch_public_job, public_link
from job_recommendations import recommend_jobs
from report_features import CandidateProfile, StrictModel
from resume_pdf import render_resume_pdf
from resume_review import review_resume


WEB_DIR = Path(__file__).with_name('web')
TEXT_LIMIT = 16_000
MAX_UPLOAD_SIZE = 10 * 1024 * 1024


class JobLink(StrictModel):
    url: str = Field(min_length=1, max_length=2048)


class CheckInput(StrictModel):
    title: str = Field(default='目标岗位', max_length=160, min_length=1)
    jd: str = Field(min_length=1, max_length=TEXT_LIMIT)
    resume: str = Field(min_length=1, max_length=TEXT_LIMIT)
    source_url: str | None = Field(default=None, max_length=2048)
    profile: CandidateProfile = Field(default_factory=CandidateProfile)


class ExportInput(StrictModel):
    resume: str = Field(min_length=1, max_length=TEXT_LIMIT)


class RecommendInput(ExportInput):
    profile: CandidateProfile = Field(default_factory=CandidateProfile)


class ReviewInput(ExportInput):
    target: str = Field(default='技术研发实习', max_length=160)
    jd: str = Field(default='', max_length=4000)


def required_key() -> str:
    key = read_api_key()
    if not key:
        raise ValueError('请先在项目的 .env 中配置 DeepSeek API Key，也可以直接试用样例。')
    return key


def create_app() -> Flask:
    app = Flask(__name__, static_folder=WEB_DIR, static_url_path='/assets')
    app.config.update(MAX_CONTENT_LENGTH=MAX_UPLOAD_SIZE + 1024 * 1024, TRUSTED_HOSTS=['127.0.0.1', 'localhost'])

    @app.after_request
    def prevent_resume_caching(response):
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_):
        return {'error': '文件过大，请上传不超过 10 MB 的简历。'}, 413

    @app.errorhandler(ValidationError)
    def invalid_fields(error):
        fields = ['.'.join(map(str, entry['loc'])) for entry in error.errors(include_input=False)]
        return {'error': '请检查表单中的字段（' + '、'.join(fields) + '）。'}, 400

    @app.errorhandler(ValueError)
    def invalid_input(error):
        return {'error': str(error)}, 400

    @app.errorhandler(RuntimeError)
    def service_error(error):
        return {'error': str(error)}, 502

    @app.get('/')
    def index():
        return send_from_directory(WEB_DIR, 'index.html')

    @app.post('/api/extract')
    def extract():
        file = request.files.get('resume')
        if file is None or not file.filename:
            raise ValueError('请选择 PDF 或 UTF-8 TXT 简历。')
        size = file.stream.seek(0, 2)
        file.stream.seek(0)
        if size > MAX_UPLOAD_SIZE:
            raise RequestEntityTooLarge()
        suffix = Path(file.filename).suffix.casefold()
        if suffix == '.pdf':
            text = read_resume_pdf(file.stream)
        elif suffix == '.txt':
            try:
                text = file.stream.read().decode('utf-8-sig').strip()
            except UnicodeDecodeError:
                raise ValueError('TXT 编码无法读取，请保存为 UTF-8 后重新上传。') from None
        else:
            raise ValueError('请选择 PDF 或 UTF-8 TXT 简历。')
        if not 1 <= len(text) <= TEXT_LIMIT:
            raise ValueError('简历内容需为 1–16000 个字符。')
        return {'text': text}

    @app.post('/api/job')
    def fetch():
        payload = JobLink.model_validate(request.get_json())
        source = DeepSeekJobSource(required_key())
        try:
            job = fetch_public_job(source, payload.url)
        except (ValueError, RuntimeError) as error:
            message = str(error)
            if JD_TEXT_FALLBACK in message:
                message = message.replace(JD_TEXT_FALLBACK, '你可以直接粘贴岗位正文，接着做体检。')
            raise type(error)(message) from None
        return {'title': job.title, 'text': job.text, 'url': job.url}

    @app.post('/api/check')
    def check():
        payload = CheckInput.model_validate(request.get_json())
        source = public_link(payload.source_url) if payload.source_url else None
        job = Job(payload.title, payload.jd, source)
        provider = model_request(required_key(), os.getenv('DEEPSEEK_MODEL', 'deepseek-flash'))
        draft, attempts = analyze([job], payload.resume, provider)
        mode = '用户核对后的单岗 JD'
        report = build_report(draft, [job], job.title, mode, attempts, [], resume=payload.resume,
                              profile=payload.profile, single_job=True)
        return {'report': report}

    @app.post('/api/recommend')
    def recommend():
        payload = RecommendInput.model_validate(request.get_json())
        key = required_key()
        provider = model_request(key, os.getenv('DEEPSEEK_MODEL', 'deepseek-flash'))
        return recommend_jobs(DeepSeekJobSource(key), provider, payload.resume, payload.profile)

    @app.post('/api/review')
    def review():
        payload = ReviewInput.model_validate(request.get_json())
        provider = model_request(required_key(), os.getenv('DEEPSEEK_MODEL', 'deepseek-flash'))
        return review_resume(payload.resume, payload.target, payload.jd, provider)

    @app.get('/api/demo')
    def demo():
        resume = '林晓\n求职方向  AI Agent 应用研发实习\n\n技能\n熟悉 Python，了解 API。\n\n项目\n使用 Python 采集配置记录并去重。'
        job = Job('AI Agent 应用研发实习生（示例）', jd_text)
        sample_request = offline_request()

        def request_demo(messages):
            raw = sample_request(messages)
            if len(messages) == 2:
                return raw
            sample = json.loads(raw)
            sample['skills'][0]['resume_evidence'] = '熟悉 Python，了解 API。'
            sample['resume_edits'] = [{'job_id': 1, 'skill': 'Python', 'action': 'emphasize'}]
            return json.dumps(sample, ensure_ascii=False)

        draft, attempts = analyze([job], resume, request_demo)
        profile = CandidateProfile(degree='bachelor', graduation_year=2028, cities=['深圳'], days_per_week=5, months=2)
        report = build_report(draft, [job], job.title, '离线样例', attempts, [], resume=resume,
                              profile=profile, single_job=True)
        return {'resume': resume, 'jd': jd_text, 'profile': profile.model_dump(), 'report': report}

    @app.post('/api/export')
    def export():
        payload = ExportInput.model_validate(request.get_json())
        pdf = BytesIO(render_resume_pdf(payload.resume))
        return send_file(pdf, mimetype='application/pdf', as_attachment=True, download_name='定制简历.pdf')

    return app


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=8767, load_dotenv=False)
