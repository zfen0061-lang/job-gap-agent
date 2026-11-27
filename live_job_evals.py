import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field, TypeAdapter

from agent import analyze, build_report, model_request, read_api_key
from jd_sources import DeepSeekJobSource, canonical_url
from report_features import CandidateProfile, StrictModel, keyword_match


class Case(StrictModel):
    url: str
    jd_term: str = Field(min_length=1, max_length=80)
    cohort: int | None = Field(default=None, ge=2000, le=2100)


def evaluate(case: Case, source: DeepSeekJobSource, provider) -> dict:
    result = {'url': case.url, 'title': '', 'fetched': False, 'passed': False, 'attempts': 0, 'checks': {}}
    try:
        job = source.fetch_job(case.url)
        result.update(title=job.title, fetched=True, retrieval=job.retrieval)
        result['checks']['expected_term_in_source'] = bool(keyword_match(case.jd_term, job.text))
        resume = '熟悉 Python，使用 Pydantic 校验结构化输出。了解 RAG。'
        draft, attempts = analyze([job], resume, provider)
        result['attempts'] = attempts
        profile = CandidateProfile(degree='bachelor', graduation_year=case.cohort + 1 if case.cohort else None)
        report = build_report(draft, [job], job.title, '真实岗位评估', attempts, [],
                              resume=resume, profile=profile, single_job=True)
        result['checks']['expected_term_in_evidence'] = any(
            keyword_match(case.jd_term, skill['jd_evidence'] or '') for skill in report['skills']
        )
        if case.cohort:
            cohort = next(item for item in report['eligibility'][0]['conditions'] if item['label'] == '毕业届次')
            result['checks']['cohort_conflict'] = (
                cohort['status'] == '不符合' and f'{case.cohort} 届' in cohort['required']
                and str(case.cohort) in cohort['jd_evidence']
            )
        result['passed'] = all(result['checks'].values())
    except (ValueError, RuntimeError, OSError) as error:
        result['error'] = str(error)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='逐条读取招聘官网岗位并运行真实模型评估。')
    parser.add_argument('--cases', type=Path, default=Path(__file__).with_name('examples') / 'live_jobs.json')
    parser.add_argument('--out', type=Path, default=Path('reports/live-job-evals.json'))
    args = parser.parse_args()
    cases = TypeAdapter(list[Case]).validate_json(args.cases.read_text(encoding='utf-8-sig'))
    urls = [canonical_url(case.url) for case in cases]
    if not urls or None in urls or len(set(urls)) != len(urls):
        parser.error('请提供不重复的招聘官网详情页链接。')
    key = read_api_key()
    if not key:
        parser.error('需要 DEEPSEEK_API_KEY。')
    source = DeepSeekJobSource(key)
    model = os.getenv('DEEPSEEK_MODEL', 'deepseek-flash')
    provider = model_request(key, model)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda case: evaluate(case, source, provider), cases))
    fetched = sum(row['fetched'] for row in results)
    passed = sum(row['passed'] for row in results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'model': model,
        'total': len(results), 'fetched': fetched, 'passed': passed, 'results': results,
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for row in results:
        failed = ', '.join(check for check, ok in row['checks'].items() if not ok)
        detail = row.get('error') or failed or '指定检查通过'
        print(f'{"通过" if row["passed"] else "未通过"} · {row["title"] or row["url"]} · {detail}')
    print(f'共 {len(results)} 条，成功抓取 {fetched} 条，通过规定检查 {passed} 条。')
    return int(passed != len(results))


if __name__ == '__main__':
    raise SystemExit(main())
