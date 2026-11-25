import json
import re
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


BASE_URL = 'https://api.deepseek.com'
SEARCH_SCOPES = (
    'talent.baidu.com/jobs/detail',
    'jobs.bytedance.com/campus/position',
    'careers.tencent.com',
)
OFFICIAL_DOMAINS = {scope.split('/')[0] for scope in SEARCH_SCOPES}
JD_HEADING = re.compile(r'任职要求|岗位要求|职位要求|职责要求|任职资格|岗位职责|工作职责|职位描述|qualifications|responsibilities', re.I)
NOISE = re.compile(r'该职位已下线|职位已关闭|职位已失效|该岗位已结束招聘|no longer accepting applications|job is no longer available', re.I)
BAIDU_TITLE = re.compile(r'(?m)^[ \t]*(?:#{1,6}[ \t]*)?([^\n#]{3,100}?[（(]J\d+[）)])[ \t]*$')
BYTEDANCE_TITLE = re.compile(r'(?m)^\s*(?:#{1,6}\s*)?([^\n]{3,100})\s*\n+(?=[^\n]{0,150}职位\s*ID\s*[:：])')
INTERNSHIP = re.compile(r'实习|\bintern(?:ship)?\b|ByteIntern', re.I)
ENGINEERING = re.compile(r'应用研发|研发|开发|工程|算法|后端|前端|测试|评测|engineer|developer', re.I)


@dataclass(frozen=True)
class Job:
    title: str
    text: str
    url: str | None = None


def canonical_url(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme != 'https' or parsed.netloc.lower() not in OFFICIAL_DOMAINS:
        return None
    path = parsed.path.rstrip('/')
    params = []
    if parsed.hostname == 'talent.baidu.com':
        if not re.fullmatch(r'/jobs/detail/(?:INTERN|CAMPUS|SOCIAL)/[0-9a-f-]{36}', path, re.I):
            return None
    elif parsed.hostname == 'jobs.bytedance.com':
        if not re.fullmatch(r'/(?:campus/)?position/\d{10,25}/detail', path):
            return None
    else:
        if not re.fullmatch(r'/(?:[a-z]{2}-[a-z]{2}/)?jobdesc\.html', path, re.I):
            return None
        position_ids = [value for key, value in parse_qsl(parsed.query) if key.lower() == 'postid']
        if len(position_ids) != 1 or not re.fullmatch(r'\d{6,25}', position_ids[0]):
            return None
        params = [('postId', position_ids[0])]
        path = '/jobdesc.html'
    return urlunsplit(('https', parsed.netloc.lower(), path, urlencode(params), ''))


def load_jobs(path: Path, limit: int) -> list[Job]:
    rows = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(rows, list):
        raise ValueError('JD 文件应为 JSON 数组。')
    jobs = {}
    for row in rows:
        if not isinstance(row, dict) or any(not isinstance(row.get(key), str) for key in ('title', 'text', 'url')):
            raise ValueError('每条 JD 都需要字符串 title、text、url。')
        title, body, url = (row[key].strip() for key in ('title', 'text', 'url'))
        parsed = urlsplit(url)
        if not title or not body or parsed.scheme != 'https' or not parsed.hostname:
            raise ValueError('JD 标题、正文和 HTTPS 来源网址不能为空。')
        jobs.setdefault(canonical_url(url) or url, Job(title, body, url))
    return list(jobs.values())[:limit]


class DeepSeekJobSource:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def post(self, endpoint: str, payload: dict) -> dict:
        request = Request(
            f'{BASE_URL}/v1/tools/{endpoint}',
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f'DeepSeek {endpoint} HTTP {exc.code}。') from None
        except (URLError, TimeoutError, ValueError):
            raise RuntimeError(f'DeepSeek {endpoint} 请求失败或返回了无效 JSON。') from None

    def search(self, target: str, limit: int) -> tuple[list[Job], list[str]]:
        candidates: dict[str, deque] = {}
        notices = []
        search_errors = []
        for scope in SEARCH_SCOPES:
            query = f'{target} 职位详情 site:{scope}'
            try:
                response = self.post('search', {'query': query, 'count': 35, 'need_content': True})
            except RuntimeError as error:
                search_errors.append(str(error))
                continue
            for result in response.get('results', []):
                source_url = canonical_url(result.get('url') or '')
                if source_url:
                    candidates.setdefault(urlsplit(source_url).hostname, deque()).append((source_url, result))
        if len(search_errors) == len(SEARCH_SCOPES):
            raise RuntimeError(search_errors[0])
        notices.extend(search_errors)

        jobs = []
        seen_urls = set()
        seen_content = set()
        fetches = 0
        is_internship = bool(INTERNSHIP.search(target))
        is_agent = bool(re.search(r'agent|智能体', target, re.I))
        is_engineering = bool(ENGINEERING.search(target))
        while any(candidates.values()) and len(jobs) < limit:
            for queue in candidates.values():
                if not queue or len(jobs) >= limit:
                    continue
                url, result = queue.popleft()
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                title = str(result.get('title') or '').strip()
                body = str(result.get('content') or '').strip()
                if NOISE.search(body):
                    continue
                if (len(body) < 100 or not JD_HEADING.search(body)) and fetches < limit + 4:
                    fetches += 1
                    try:
                        page = self.post('fetch', {'url': url})
                    except RuntimeError:
                        notices.append(f'网页读取失败，已跳过 {url}')
                        continue
                    body = str(page.get('content') or '').strip()
                    title = str(page.get('title') or title).strip()
                if not title or len(body) < 100 or not JD_HEADING.search(body) or NOISE.search(body):
                    continue
                host = urlsplit(url).hostname
                title_pattern = {'talent.baidu.com': BAIDU_TITLE, 'jobs.bytedance.com': BYTEDANCE_TITLE}.get(host)
                if title_pattern:
                    extracted_title = title_pattern.search(body)
                    if extracted_title:
                        title = extracted_title.group(1).strip()
                heading = JD_HEADING.search(body)
                body = body[max(0, heading.start() - 220):][:6000].strip()
                if is_internship and '/INTERN/' not in url.upper() and not INTERNSHIP.search(title):
                    continue
                if is_agent and not re.search(r'agent|智能体|大模型|llm', f'{title} {body[:1800]}', re.I):
                    continue
                if is_engineering and not ENGINEERING.search(title):
                    continue
                fingerprint = re.sub(r'\W+', '', body).casefold()
                if fingerprint not in seen_content:
                    seen_content.add(fingerprint)
                    jobs.append(Job(title, body, url))
        if len(jobs) < limit:
            notices.append(f'期望 {limit} 条，实际纳入 {len(jobs)} 条，覆盖率与缺口按实际样本计算。')
        return jobs, notices


SNAPSHOT_DATE = date(2026, 11, 25)
