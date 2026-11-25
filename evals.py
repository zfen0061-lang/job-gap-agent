import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agent import analyze, build_report, demo_analysis_json, model_request, my_resume, normalized, read_api_key
from jd_sources import Job
from storage import save_analysis


@dataclass(frozen=True)
class EvalCase:
    name: str
    job: Job
    resume: str
    responses: tuple[str, ...]
    expected_score: float
    expected_gaps: dict[str, str]
    expected_retries: int


def eval_cases() -> tuple[EvalCase, ...]:
    python_skill = {
        'job_id': 1, 'name': 'Python', 'priority': 'required', 'status': 'matched',
        'jd_evidence': '要求熟练 Python', 'resume_evidence': '熟练 Python',
    }
    perfect = {
        'skills': [python_skill, {
            'job_id': 1, 'name': 'API 开发', 'priority': 'required', 'status': 'matched',
            'jd_evidence': '有 API 开发经验', 'resume_evidence': '独立开发过 API',
        }],
        'salary': [{'job_id': 1, 'amount': '200-300元/天'}],
        'match_percentage': 100,
        'gap_analysis': [],
    }
    recovery = {
        'skills': [python_skill, {
            'job_id': 1, 'name': 'pytest', 'priority': 'required', 'status': 'partial',
            'jd_evidence': '熟悉 pytest 回归测试', 'resume_evidence': '了解 pytest',
        }],
        'salary': [{'job_id': 1, 'amount': '180-220元/天'}],
        'match_percentage': 75,
        'gap_analysis': [{
            'skill': 'pytest', 'project': '为命令行应用增加回归测试',
            'deliverable': '交付覆盖正常输入与异常输入的 pytest 用例和测试结果。',
        }],
    }
    return (
        EvalCase(
            '完美匹配', Job('Python API 实习', '要求熟练 Python，有 API 开发经验。薪资 200-300元/天。'),
            '熟练 Python，独立开发过 API。',
            (json.dumps(perfect, ensure_ascii=False),), 100.0, {}, 0,
        ),
        EvalCase(
            '技能缺口', Job('AI Agent 应用研发实习', '要求熟练 Python，有 Agent Workflow 编排实操经验，能独立搭建 RAG 检索与生成链路。实习薪资 200-300元/天。'), my_resume,
            (demo_analysis_json(),), 33.3, {'Agent Workflow': 'missing', 'RAG': 'missing'}, 0,
        ),
        EvalCase(
            '乱码后纠错', Job('研发工具实习', '要求熟练 Python，熟悉 pytest 回归测试。薪资 180-220元/天。'),
            '熟练 Python，了解 pytest。',
            ('乱码@@ {', json.dumps(recovery, ensure_ascii=False)), 75.0, {'pytest': 'partial'}, 1,
        ),
    )


def run_evals(database: Path, live_model: Callable | None = None) -> tuple[int, int, int]:
    cases = eval_cases()
    saved = 0
    retries = 0
    for case in cases:
        calls = 0

        def request_model(messages: list[dict]) -> str | None:
            nonlocal calls
            calls += 1
            if calls > 1 and '未通过校验' not in messages[-1]['content']:
                raise ValueError('重试提示缺少上一次的校验错误。')
            if live_model is not None and (case.expected_retries == 0 or calls > 1):
                return live_model(messages)
            return case.responses[min(calls, len(case.responses)) - 1]

        try:
            jobs = [case.job]
            draft, attempts = analyze(jobs, case.resume, request_model)
            mode = 'DeepSeek 在线 Evals' if live_model is not None else '离线 Evals'
            report = build_report(draft, jobs, case.job.title, mode, attempts, [f'评估用例：{case.name}'])
            gaps = [skill for skill in draft.skills if skill.status != 'matched']
            gaps_match = len(gaps) == len(case.expected_gaps)
            for name, status in case.expected_gaps.items():
                gaps_match &= any(
                    skill.status == status and normalized(name) in normalized(f'{skill.name} {skill.jd_evidence}')
                    for skill in gaps
                )
            if report['match_percentage'] != case.expected_score or not gaps_match:
                observed = {skill.name: skill.status for skill in gaps}
                raise ValueError(f'匹配度或技能缺口与预期不同：{report["match_percentage"]}% / {observed}')
            if calls - 1 != case.expected_retries:
                raise ValueError(f'预期重试 {case.expected_retries} 次，实际重试 {calls - 1} 次。')
            record_id = save_analysis(database, report)
            saved += 1
            print(f'[通过] {case.name} · 匹配度 {report["match_percentage"]}% · SQLite #{record_id}')
        except (ValueError, RuntimeError, OSError) as error:
            print(f'[失败] {case.name} · {error}', file=sys.stderr)
        retries += max(0, calls - 1)

    print(f'一共处理 {len(cases)} 条，成功入库 {saved} 条，触发重试 {retries} 次。')
    print(f'Evals 数据库：{database.resolve()}')
    return len(cases), saved, retries


def main() -> int:
    parser = argparse.ArgumentParser(description='运行三条评估并保存通过的分析。')
    parser.add_argument('--live', action='store_true', help='调用 DeepSeek API，乱码用例的首次响应仍由测试注入')
    parser.add_argument('--db', type=Path, help='Evals SQLite 文件')
    args = parser.parse_args()
    request = None
    if args.live:
        try:
            api_key = read_api_key()
        except OSError as error:
            parser.error(f'无法读取项目的本地配置：{error}')
        if not api_key:
            parser.error('请在本机设置 DEEPSEEK_API_KEY。')
        request = model_request(api_key, os.getenv('DEEPSEEK_MODEL', 'deepseek-flash'))
    default_db = Path('reports/career_evals_live.sqlite3' if args.live else 'reports/career_evals.sqlite3')
    total, saved, _ = run_evals(args.db or default_db, request)
    return 0 if saved == total else 1


if __name__ == '__main__':
    raise SystemExit(main())
