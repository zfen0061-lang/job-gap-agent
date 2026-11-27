import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from ipaddress import ip_address
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from jd_sources import DeepSeekJobSource, ENGINEERING, INTERNSHIP, Job, canonical_url, job_from_page


COMPANIES = {
    '字节跳动': ('jobs.bytedance.com',), '腾讯': ('careers.tencent.com', 'join.qq.com', 'tencent.wd1.myworkdayjobs.com'),
    '百度': ('talent.baidu.com',), '阿里巴巴': ('talent.alibaba.com', 'campus.alibaba.com'),
    '美团': ('zhaopin.meituan.com', 'campus.meituan.com'), '小米': ('hr.xiaomi.com',),
    '京东': ('zhaopin.jd.com', 'campus.jd.com'), '网易': ('hr.163.com', 'campus.163.com'),
    '快手': ('zhaopin.kuaishou.cn', 'campus.kuaishou.cn'), '拼多多': ('careers.pddglobalhr.com', 'careers.pinduoduo.com'),
    '华为': ('career.huawei.com',), '滴滴': ('talent.didiglobal.com', 'campus.didiglobal.com'),
    '蚂蚁集团': ('talent.antgroup.com',), '小红书': ('job.xiaohongshu.com', 'campus.xiaohongshu.com'),
    '哔哩哔哩': ('jobs.bilibili.com',), '携程': ('careers.trip.com',),
    'OPPO': ('careers.oppo.com',), 'vivo': ('hr.vivo.com',),
    '联想': ('talent.lenovo.com.cn', 'jobs.lenovo.com'), '大疆': ('we.dji.com',),
    '米哈游': ('jobs.mihoyo.com',), '科大讯飞': ('campus.iflytek.com',),
    '360': ('campus.360.cn',), '比亚迪': ('job.byd.com',),
}
COMPANY_BY_HOST = {host: company for company, hosts in COMPANIES.items() for host in hosts}
TRACKING_KEYS = {'spm', 'ref', 'referral', 'referral_code', 'from', 'source'}
PRIVATE_KEYS = {'token', 'access_token', 'auth', 'session', 'sessionid', 'password', 'signature'}
DETAIL_ROUTE = re.compile(
    r'(?i)(?:job|position|post|vacancy|recruit)[/_-]?(?:detail|description|info)'
    r'|/(?:jobs?|positions?|vacancies|detail)/(?:\d{4,}|[\w-]{6,})(?=$|[/#?])'
)
DETAIL_ID = {'jobid', 'jobcode', 'positionid', 'postid', 'vacancyid', 'recruitid'}
TECH_TERMS = ('Python', 'Java', 'Go', 'RAG', 'LangChain', 'Pydantic', 'LLM', 'DeepSeek',
              'Playwright', 'pytest', 'Docker', 'TypeScript', 'MCP', 'SQL', 'Git')
BAIDU_STATE_VALUE = re.compile(r'"(?:\\.|[^"\\])*"|(?<=:)\s*undefined(?=\s*[,}])')


def public_link(link: str) -> str | None:
    try:
        parsed = urlsplit(link.strip())
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or not host or parsed.netloc.casefold() != host.casefold():
            return None
    except ValueError:
        return None
    if not re.fullmatch(r'[a-z0-9.-]+\.[a-z][a-z0-9-]+', host, re.I):
        return None
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        return None
    if host.endswith(('.local', '.localhost', '.internal', '.test', '.invalid')):
        return None
    params = parse_qsl(parsed.query, keep_blank_values=True)
    fragment_params = parse_qsl(parsed.fragment.partition('?')[-1]) if '?' in parsed.fragment else []
    if any(key.casefold() in PRIVATE_KEYS for key, _ in params + fragment_params):
        return None
    retained = [(key, val) for key, val in params if not key.casefold().startswith('utm_') and key.casefold() not in TRACKING_KEYS]
    return urlunsplit(('https', host.lower(), parsed.path.rstrip('/') or '/', urlencode(retained),
                       parsed.fragment if parsed.fragment.startswith('/') else ''))


def official_detail(link: str) -> str | None:
    url = public_link(link)
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.hostname not in COMPANY_BY_HOST:
        return None
    exact = canonical_url(url)
    if exact:
        return exact
    if parsed.hostname in ('jobs.bytedance.com', 'careers.tencent.com', 'talent.baidu.com'):
        return None
    fragment_path, _, fragment_query = parsed.fragment.partition('?')
    ids = parse_qsl(parsed.query) + parse_qsl(fragment_query)
    if any(
        key.casefold() in DETAIL_ID and re.fullmatch(r'[\w-]{5,}', value) for key, value in ids
    ):
        return url
    last_segment = (fragment_path or parsed.path).rstrip('/').rsplit('/', 1)[-1].casefold()
    if last_segment in ('home', 'index', 'search', 'campus', 'intern', 'internship', 'careers', 'job-list', 'list'):
        return None
    if DETAIL_ROUTE.search(parsed.path) or DETAIL_ROUTE.search(fragment_path):
        return url
    return None


def fetch_public_job(source: DeepSeekJobSource, link: str) -> Job:
    url = public_link(link)
    if not url:
        raise ValueError('请提供可公开访问的 HTTPS 职位链接，或者直接粘贴 JD 正文。')
    if canonical_url(url):
        if url.startswith('https://talent.baidu.com/jobs/detail/INTERN/'):
            detail = baidu_state(url).get('detailData') or {}
            if detail.get('isValid') is True:
                job = baidu_row(detail.get('postInfo'), url)
                if job:
                    return job
            if detail.get('isValid') is False:
                raise ValueError('官网显示该岗位已失效。可粘贴 JD 正文继续分析。')
        return source.fetch_job(url)
    try:
        page = source.post('fetch', {'url': url})
    except RuntimeError as error:
        raise RuntimeError(f'这个链接暂时读不到公开正文。{error} 请粘贴 JD 正文继续。') from None
    if isinstance(page, dict):
        job = job_from_page(url, str(page.get('title') or '').strip(), str(page.get('content') or ''), 'public-fetch')
        if job:
            return job
    raise ValueError('这个链接没有返回可识别的职位正文。请打开原页面并粘贴 JD 正文。')


def resume_direction(resume: str) -> tuple[str, tuple[str, ...]]:
    # Only fixed vocabulary enters the search query. Resume text is never sent to Search.
    captured = tuple(term for term in TECH_TERMS if re.search(r'(?<![\w])' + re.escape(term) + r'(?![A-Za-z0-9_])',
                                                              resume, re.I | re.ASCII))
    if re.search(r'Agent|智能体|大模型|RAG|LLM', resume, re.I):
        return 'AI Agent 应用研发实习', captured
    if re.search(r'pytest|测试开发|自动化测试|Playwright', resume, re.I):
        return '测试开发实习', captured
    return '软件研发实习', captured


def search_queries(direction: str, terms: tuple[str, ...]) -> list[str]:
    primary_hosts = [hosts[0] for hosts in COMPANIES.values()]
    word = next((item for item in terms if item in ('Python', 'Java', 'TypeScript')), '')
    query = f'{direction} {word}'.strip()
    groups = []
    for start in range(0, len(primary_hosts), 3):
        domains = ' OR '.join(f'site:{host}' for host in primary_hosts[start:start + 3])
        groups.append(f'{query} {domains}')
    return groups


def tencent_json(endpoint: str, parameters: dict) -> dict:
    url = f'https://careers.tencent.com/tencentcareer/api/post/{endpoint}?' + urlencode(parameters)
    request = Request(url, headers={'Accept': 'application/json', 'Referer': 'https://careers.tencent.com/search.html',
                                   'User-Agent': 'Mozilla/5.0'})
    try:
        with urlopen(request, timeout=8) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return {}
    data = payload.get('Data') if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


def tencent_internships(direction: str) -> list[Job]:
    keyword = 'Agent' if 'Agent' in direction else '测试' if '测试' in direction else '开发'
    common = {'language': 'zh-cn'}
    rows = tencent_json('Query', {**common, 'keyword': keyword, 'attrId': '3', 'pageIndex': 1, 'pageSize': 5}).get('Posts', [])
    jobs = []
    for row in rows[:4] if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        post_id = str(row.get('PostId') or '')
        if not INTERNSHIP.search(str(row.get('RecruitPostName') or '')) or not re.fullmatch(r'\d{6,25}', post_id):
            continue
        detail = tencent_json('ByPostId', {**common, 'postId': post_id})
        title = str(detail.get('RecruitPostName') or '')
        if not detail or not INTERNSHIP.search(title):
            continue
        source_url = public_link(str(detail.get('PostURL') or ''))
        if not source_url or urlsplit(source_url).hostname != 'tencent.wd1.myworkdayjobs.com':
            source_url = f'https://careers.tencent.com/jobdesc.html?postId={post_id}'
        text = '\n'.join((f'工作地点：{detail.get("LocationName", "")}', f'岗位职责：{detail.get("Responsibility", "")}',
                          f'任职要求：{detail.get("Requirement", "")}'))
        job = job_from_page(source_url, title, text, 'tencent-json')
        if job:
            jobs.append(job)
    return jobs


def baidu_state(url: str) -> dict:
    request = Request(url, headers={'Accept': 'text/html', 'User-Agent': 'Mozilla/5.0'})
    try:
        with urlopen(request, timeout=8) as response:
            html = response.read(1_500_000).decode('utf-8')
        marker = re.search(r'<script[^>]*>\s*window\.__USE_SSR__\s*=\s*true;\s*window\.__INITIAL_DATA__\s*=\s*', html)
        if not marker:
            return {}
        raw = html[marker.end():].split('</script>', 1)[0]
        repaired = BAIDU_STATE_VALUE.sub(lambda match: match[0] if match[0].startswith('"') else 'null', raw)
        payload, _ = json.JSONDecoder().raw_decode(repaired)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def baidu_row(row, expected_url: str | None = None) -> Job | None:
    if not isinstance(row, dict):
        return None
    url = canonical_url('https://talent.baidu.com/jobs/detail/INTERN/' + str(row.get('postId') or ''))
    if not url or (expected_url and url != expected_url):
        return None
    title = str(row.get('name') or '')
    if not ENGINEERING.search(title):
        return None
    text = '\n'.join((f'工作地点：{row.get("workPlace", "")}', f'岗位职责：{row.get("workContent", "")}',
                      f'任职要求：{row.get("serviceCondition", "")}'))
    return job_from_page(url, title, text, 'baidu-page-json')


def baidu_internships(direction: str) -> list[Job]:
    keyword = 'Agent' if 'Agent' in direction else '测试' if '测试' in direction else '开发'
    parameters = {'recruitType': 'INTERN', 'keyWord': keyword, 'postType': '1'}
    rows = (baidu_state('https://talent.baidu.com/jobs/list?' + urlencode(parameters)).get('listData') or {}).get('listDetailData', [])
    return [job for row in rows[:12] if (job := baidu_row(row))] if isinstance(rows, list) else []


def discover_jobs(source: DeepSeekJobSource, resume: str, limit: int = 10) -> tuple[str, list[Job], list[str]]:
    direction, terms = resume_direction(resume)
    notices = []
    candidates = {}

    def query(text):
        try:
            payload = source.post('search', {'query': text, 'count': 16, 'need_content': True})
        except RuntimeError as error:
            return [], str(error)
        return (payload.get('results', []) if isinstance(payload, dict) else []), ''

    def add(rows):
        for row in rows:
            if not isinstance(row, dict):
                continue
            link = official_detail(str(row.get('url') or ''))
            if not link or link in candidates:
                continue
            title = str(row.get('title') or '').strip()
            body = str(row.get('content') or '')
            if not INTERNSHIP.search(title + ' ' + link):
                continue
            candidates[link] = (title, body)

    with ThreadPoolExecutor(max_workers=4) as pool:
        official = [pool.submit(adapter, direction) for adapter in (tencent_internships, baidu_internships)]
        for rows, error in pool.map(query, search_queries(direction, terms)):
            add(rows)
            if error:
                notices.append(error)
        available = sum(bool(job_from_page(url, title, body)) for url, (title, body) in candidates.items())
        if available < limit:
            rows, error = query(direction + ' 招聘官网 职位详情')
            add(rows)
            if error:
                notices.append(error)

        def resolve(entry):
            url, (title, body) = entry
            job = job_from_page(url, title, body, 'official-search')
            if job:
                return job
            try:
                return fetch_public_job(source, url)
            except (ValueError, RuntimeError):
                return None

        # Consider usable results first and cap extra network calls for thin results.
        ready, thin = [], []
        for entry in candidates.items():
            url, (title, body) = entry
            job = job_from_page(url, title, body, 'official-search')
            if job:
                ready.append(job)
            else:
                thin.append(entry)
        if len(ready) < limit:
            ready.extend(job for job in pool.map(resolve, thin[:limit + 2]) if job)
        ready = [job for task in official for job in task.result()] + ready

    def relevance(job):
        title = job.title.casefold()
        body = job.text.casefold()
        topical = bool(re.search(r'agent|智能体|大模型|llm', title)) if 'Agent' in direction else bool(ENGINEERING.search(title))
        return (topical, sum(term.casefold() in body for term in terms))

    ranked = sorted(ready, key=relevance, reverse=True)
    jobs, surplus = [], []
    companies = Counter()
    fingerprints = set()
    seen_urls = set()
    for job in ranked:
        signature = re.sub(r'\W+', '', job.text).casefold()
        if signature in fingerprints or job.url in seen_urls:
            continue
        fingerprints.add(signature)
        seen_urls.add(job.url)
        company = COMPANY_BY_HOST.get(urlsplit(job.url or '').hostname, '')
        if companies[company] >= 3:
            surplus.append(job)
        else:
            jobs.append(job)
            companies[company] += 1
    jobs = (jobs + surplus)[:limit]
    if len(jobs) < limit:
        notices.append(f'本次找到 {len(jobs)} 条可用官方详情，目标为 {limit} 条。')
    return direction, jobs, list(dict.fromkeys(notices))
