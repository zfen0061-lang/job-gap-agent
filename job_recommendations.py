from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from agent import Analysis, analyze, build_report
from job_discovery import COMPANY_BY_HOST, discover_jobs
from report_features import CandidateProfile


RISK_ORDER = {'已核对的公开硬条件无冲突': 0, '关键信息待确认': 1, '存在明确硬条件冲突': 2}


def ranking_key(item: dict):
    report = item['report']
    return RISK_ORDER[report['job_check']['risk']], -report['match_percentage']


def rank_jobs(jobs, draft: Analysis, resume: str, profile: CandidateProfile, attempts: int) -> list[dict]:
    results = []
    for number, job in enumerate(jobs, 1):
        skills = [skill.model_copy(update={'job_id': 1}) for skill in draft.skills if skill.job_id == number]
        gaps = {skill.name.casefold() for skill in skills if skill.status != 'matched'}
        single = Analysis(
            skills=skills,
            salary=[salary.model_copy(update={'job_id': 1}) for salary in draft.salary if salary.job_id == number],
            match_percentage=0,
            gap_analysis=[gap for gap in draft.gap_analysis if gap.skill.casefold() in gaps],
            requirements=[item.model_copy(update={'job_id': 1}) for item in draft.requirements if item.job_id == number],
            resume_edits=[edit.model_copy(update={'job_id': 1}) for edit in draft.resume_edits if edit.job_id == number],
        )
        report = build_report(single, [job], job.title, '简历反向推荐 · 官网详情', attempts, [],
                              resume=resume, profile=profile, single_job=True)
        risk = RISK_ORDER[report['job_check']['risk']]
        score = report['match_percentage']
        stars = sum(score >= threshold for threshold in (0, 35, 55, 70, 85))
        matched = [skill for skill in skills if skill.status == 'matched']
        missing = [skill.name for skill in skills if skill.priority == 'required' and skill.status != 'matched']
        pending = [item for item in report['eligibility'][0]['conditions'] if item['jd_evidence'] and item['status'] != '符合']
        label = ('可优先准备投递', '先确认公开条件', '存在明确条件冲突')[risk]
        if risk == 0 and score < 55:
            label = '公开条件已核对，技术差距较大'
        results.append({
            'company': COMPANY_BY_HOST.get(urlsplit(job.url or '').hostname, ''),
            'label': label,
            'stars': 0 if risk == 2 else min(3, stars) if risk == 1 else stars,
            'match_evidence': [{'skill': item.name, 'resume': item.resume_evidence, 'jd': item.jd_evidence}
                               for item in matched[:2]],
            'missing': missing[:3],
            'pending': pending,
            'report': report,
        })
    return sorted(results, key=ranking_key)


def recommend_jobs(source, request, resume: str, profile: CandidateProfile) -> dict:
    limit = 10
    direction, jobs, notices = discover_jobs(source, resume, limit=limit + 3)
    if not jobs:
        return {'direction': direction, 'results': [], 'notices': notices}

    def evaluate(batch):
        try:
            draft, attempts = analyze(batch, resume, request)
            return rank_jobs(batch, draft, resume, profile, attempts), []
        except (ValueError, RuntimeError) as error:
            if len(batch) == 1:
                return [], [f'{batch[0].title} 未完成证据校验：{error}']
            cards = []
            notices = []
            for job in batch:
                single, errors = evaluate([job])
                cards.extend(single)
                notices.extend(errors)
            return cards, notices

    results = []
    initial = jobs[:limit]
    batches = [initial[index:index + 3] for index in range(0, len(initial), 3)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for cards, errors in pool.map(evaluate, batches):
            results.extend(cards)
            notices.extend(errors)
        if len(results) < limit and jobs[limit:]:
            replacements, errors = evaluate(jobs[limit:])
            results.extend(replacements)
            notices.extend(errors)
    if len(results) < limit:
        notices.append(f'本次有 {len(results)} 条岗位完成证据校验。')
    return {'direction': direction, 'results': sorted(results, key=ranking_key)[:limit], 'notices': notices}
