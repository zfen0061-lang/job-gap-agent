import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from jd_sources import Job

if TYPE_CHECKING:
    from agent import Skill


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)


Degree = Literal['associate', 'bachelor', 'master', 'doctor']
Condition = Literal['degree', 'graduation_year', 'cities', 'days_per_week', 'months']
DEGREES = {'associate': '专科', 'bachelor': '本科', 'master': '硕士', 'doctor': '博士'}
DEGREE_WORDS = {
    'associate': r'专科|大专|associate', 'bachelor': r'本科|学士|bachelor',
    'master': r'硕士|master', 'doctor': r'博士|ph\.?d\.?|doctoral',
}
PREFERENCE = re.compile(r'优先|加分|\bpreferred\b|\bnice[ -]to[ -]have\b', re.I)
CONDITION_LABELS = {
    'degree': '学历', 'graduation_year': '毕业届次', 'cities': '工作地点',
    'days_per_week': '每周出勤', 'months': '实习时长',
}
CHINESE_NUMBERS = dict(zip('一二三四五六七八九', range(1, 10)))
NUMBER_PATTERN = r'(?<![\d.])(\d{1,2}|[一二三四五六七八九十])'
SKILL_HEADING = re.compile(r'^(?:AI\s*与大模型|工程与可靠性|编程与采集|技术能力|核心技能|编程语言|工程工具)(?:[：:｜|]\s*|\s+)')
PROGRAMMING_LANGUAGES = {'python', 'typescript', 'javascript', 'java', 'go', 'c', 'c++', 'c#', 'rust', 'kotlin', 'swift'}
DATABASES = {'sqlite', 'postgresql', 'mysql', 'sql server', 'mongodb', 'redis'}
RAG_TERMS = ('RAG', '检索增强生成', 'Retrieval Augmented Generation', 'Retrieval-Augmented Generation')
PROMPT_TERMS = ('Prompt Engineering', 'Prompt 工程', '提示词工程')
MULTI_AGENT = re.compile(r'multi[\s-]*agents?|多\s*agents?|多(?:个)?智能体', re.I)
KEYWORD_ALIASES = (
    RAG_TERMS,
    ('Agent Workflow', '智能体工作流', '智能体编排'),
    PROMPT_TERMS,
    ('LLM', '大语言模型'),
    ('Structured Output', '结构化输出'),
    ('Function Calling', '函数调用'),
    ('Continuous Integration', '持续集成', 'CI'),
)
KEYWORD_VARIANTS = {term.casefold(): group for group in KEYWORD_ALIASES for term in group}


class CandidateProfile(StrictModel):
    degree: Degree | None = None
    graduation_year: int | None = Field(default=None, ge=2000, le=2100)
    cities: list[str] = Field(default_factory=list, max_length=25)
    days_per_week: int | None = Field(default=None, ge=1, le=7)
    months: int | None = Field(default=None, ge=1, le=36)
    months_at_least: bool = False


class HardRequirement(StrictModel):
    job_id: int = Field(ge=1)
    kind: Condition
    values: list[str] = Field(min_length=1, max_length=25)
    jd_evidence_id: int = Field(ge=1)


class ResumeEdit(StrictModel):
    job_id: int = Field(ge=1)
    skill: str = Field(min_length=1, max_length=60)
    action: Literal['emphasize', 'promote']


def read_profile(path: Path) -> CandidateProfile:
    return CandidateProfile.model_validate_json(path.read_text(encoding='utf-8-sig'))


def normalize_city(city: str) -> str:
    return city.strip().removesuffix('市').casefold()


def validate_requirement(requirement: HardRequirement, quote: str) -> None:
    kind, values = requirement.kind, requirement.values
    if kind == 'degree':
        clauses = re.split(r'[，,；;。！？]', quote)
        valid = len(values) == 1 and values[0] in DEGREES and any(
            re.search(DEGREE_WORDS[values[0]], clause, re.I) and not PREFERENCE.search(clause)
            for clause in clauses
        )
        valid = valid and not re.search(r'仅限|只接受|学历不限|only accepts', quote, re.I)
    elif kind == 'graduation_year':
        valid = all(re.fullmatch(r'20\d{2}', year) and re.search(rf'(?<!\d)(?:{year}|{year[2:]}\s*届)(?!\d)', quote) for year in values)
        valid = valid and bool(re.search(r'届|class of', quote, re.I))
    elif kind == 'cities':
        valid = all(normalize_city(city) and normalize_city(city) in quote.casefold() for city in values)
    else:
        limit, unit = (7, r'天|日|days?') if kind == 'days_per_week' else (36, r'个?月|months?')
        ranges = re.compile(rf'{NUMBER_PATTERN}\s*(?:[-–—~～至到]|\bto\b)\s*{NUMBER_PATTERN}\s*(?:{unit})', re.I)
        # The lower endpoint is the threshold for an advertised range.
        amounts = [match.group(1) for match in ranges.finditer(quote)]
        amounts.extend(re.findall(rf'{NUMBER_PATTERN}\s*(?:{unit})', ranges.sub('', quote), re.I))
        numbers = {int(n) if n.isascii() else CHINESE_NUMBERS.get(n, 10) for n in amounts}
        valid = len(values) == 1 and values[0].isascii() and values[0].isdigit() and 1 <= int(values[0]) <= limit and int(values[0]) in numbers
        valid = valid and not re.search(r'至多|不超过|最多|at most|up to', quote, re.I)
        if kind == 'days_per_week':
            valid = valid and bool(re.search(r'每周|一周|weekly|per week|/\s*周', quote, re.I))
    if not valid:
        raise ValueError(f'JD {requirement.job_id} 的 {kind}（{values}）必须可在所选条件原文核实。')


def requirement_result(requirement: HardRequirement, profile: CandidateProfile) -> tuple[str, str, str]:
    kind, values = requirement.kind, requirement.values
    current = getattr(profile, kind)
    if kind == 'degree':
        expected = f'{DEGREES[values[0]]}及以上'
        supplied = DEGREES.get(current, '')
        met = current is not None and list(DEGREES).index(current) >= list(DEGREES).index(values[0])
    elif kind == 'graduation_year':
        expected = ' / '.join(f'{year} 届' for year in values)
        supplied = f'{current} 届' if current is not None else ''
        met = str(current) in values
    elif kind == 'cities':
        expected = ' / '.join(values)
        supplied = ' / '.join(current)
        met = bool({normalize_city(city) for city in current} & {normalize_city(city) for city in values})
    else:
        unit = '天/周' if kind == 'days_per_week' else '个月'
        expected = f'至少 {values[0]} {unit}'
        supplied = f'{current} {unit}' if current is not None else ''
        met = current is not None and current >= int(values[0])
        if kind == 'months' and current is not None and profile.months_at_least:
            supplied = f'至少 {supplied}'
            if not met:
                return '待确认', expected, supplied
    return ('符合' if met else '不符合') if supplied else '待确认', expected, supplied


def eligibility_report(jobs: list[Job], requirements: list[HardRequirement], fragments: list[list[str]], profile: CandidateProfile | None) -> list[dict]:
    results = []
    known = {(item.job_id, item.kind): item for item in requirements}
    profile = profile or CandidateProfile()
    for job_id, job in enumerate(jobs, 1):
        conditions = []
        for kind, label in CONDITION_LABELS.items():
            requirement = known.get((job_id, kind))
            if requirement:
                status, expected, supplied = requirement_result(requirement, profile)
                evidence = fragments[job_id - 1][requirement.jd_evidence_id - 1]
            else:
                status, expected, supplied, evidence = '待确认', 'JD 暂无可核对条件', '', ''
            conditions.append({'label': label, 'status': status, 'required': expected, 'candidate': supplied, 'jd_evidence': evidence})
        statuses = {item['status'] for item in conditions if item['jd_evidence']}
        if '不符合' in statuses:
            status = '不符合已披露条件'
        elif statuses == {'符合'}:
            status = '符合已核对条件'
        else:
            status = '待确认'
        results.append({'job_id': job_id, 'title': job.title, 'status': status, 'conditions': conditions})
    return results


def keyword_match(term: str, text: str) -> str | None:
    pattern = r'\s+'.join(re.escape(word) for word in term.split())
    if not pattern:
        return None
    if term[0].isascii() and term[0].isalnum():
        pattern = r'(?<![A-Za-z0-9_])' + pattern
    if term[-1].isascii() and term[-1].isalnum():
        pattern += r'(?![A-Za-z0-9_])'
    match = re.search(pattern, text, re.I)
    return match.group() if match else None


def validate_named_skill_evidence(name: str, quote: str | None) -> None:
    if not quote:
        return
    terms = KEYWORD_VARIANTS.get(name.casefold())
    if terms == RAG_TERMS or re.match(r'^RAG(?:$|[/ （(])', name, re.I):
        explicit = any(keyword_match(term, quote) for term in RAG_TERMS)
        workflow = all(re.search(pattern, quote, re.I) for pattern in (
            r'文档|知识库|向量|语义|document|knowledge base|vector',
            r'检索|召回|retriev',
            r'生成|问答|generation|answer',
        ))
        if not explicit and not workflow:
            raise ValueError('RAG 的简历证据需体现 RAG、同义词或文档检索生成链路。请改用对应原文，或标为 missing。')
    if terms == PROMPT_TERMS:
        if not (keyword_match('Prompt', quote) or '提示词' in quote):
            raise ValueError('Prompt 工程的简历证据需体现 Prompt 或提示词。请改用对应原文，或标为 missing。')
    if MULTI_AGENT.search(name) and not MULTI_AGENT.search(quote):
        raise ValueError('Multi-Agent 技能需要多智能体原文证据，单个 Agent 项目名称不足以佐证。请改用对应原文，或标为 missing。')


def keyword_check(skills: list['Skill'], resume: str) -> list[dict]:
    results = []
    for skill in skills:
        name = skill.name.strip()
        variants = KEYWORD_VARIANTS.get(name.casefold(), (name,))
        term = next((hit for variant in variants if (hit := keyword_match(variant, skill.jd_evidence or ''))), None)
        if not term:
            continue
        exact = keyword_match(term, resume)
        alias = None if exact else next((hit for variant in variants if (hit := keyword_match(variant, resume))), None)
        results.append({
            'skill': skill.name,
            'priority': skill.priority,
            'keyword': term,
            'resume_keyword': exact or alias,
            'status': '原词出现' if exact else '别名出现' if alias else '未找到对应词',
            'jd_evidence': skill.jd_evidence,
            'resume_evidence': skill.resume_evidence,
        })
    return results


def resume_text_changed(original: str, suggested: str | None) -> bool:
    if not suggested:
        return False
    before = unicodedata.normalize('NFKC', original).casefold()
    after = unicodedata.normalize('NFKC', suggested).casefold()
    return Counter(char for char in before if char.isalnum()) != Counter(char for char in after if char.isalnum())


def rewrite_resume_line(source: str) -> tuple[str | None, str]:
    heading = SKILL_HEADING.match(source)
    prefix = heading.group() if heading else ''
    body = source[len(prefix):]
    if re.search(r'虽然|但是|不过|仅|除了|并非|没有|尚未|计划|打算', body):
        return None, '保留已有证据'

    technical = re.fullmatch(r'主要技术能力\s+(?P<tools>[^。]+)。大模型应用含(?P<work>[^。]+)。?', source)
    if technical:
        return f'大模型应用实践涉及{technical["work"]}。使用 {technical["tools"]}。', '先写大模型应用经验，再列对应工具'

    overview = re.fullmatch(
        r'面向(?P<audience>[^，。]+)，解决(?P<problem>[^，。]+)，(?P<role>独立完成|完成|负责)(?P<work>[^。]+)。',
        source,
    )
    if overview:
        return (f'{overview["role"]}{overview["work"]}。功能面向{overview["audience"]}，解决{overview["problem"]}。',
                '先写实际职责，再交代用户和问题')

    proficiency = re.fullmatch(r'熟悉\s*(?P<familiar>[^。！？]+?)[，,]\s*了解\s*(?P<introductory>[^。！？]+)[。]?', body)
    if proficiency:
        return (f'{prefix}熟悉的方向包括 {proficiency["familiar"]}。对 {proficiency["introductory"]} 有基础了解。',
                '改写措辞并保留掌握程度')

    if prefix.startswith(('编程与采集', '编程语言')) and '。' not in body.rstrip('。'):
        items = [item.strip() for item in body.rstrip('。').split('、')]
        groups = {'编程语言': [], '浏览器自动化工具': [], '数据库': []}
        for item in items:
            if item and all(part.strip().casefold() in PROGRAMMING_LANGUAGES for part in item.split('/')):
                groups['编程语言'].append(item)
            elif re.fullmatch(r'[\w .-]+\s+浏览器自动化', item):
                groups['浏览器自动化工具'].append(item.removesuffix('浏览器自动化').strip())
            elif item.casefold() in DATABASES:
                groups['数据库'].append(item)
            else:
                break
        else:
            if sum(bool(group) for group in groups.values()) > 1:
                sentences = [f'{label}包括 {"、".join(group)}' for label, group in groups.items() if group]
                return '。'.join(sentences) + '。', '按语言、自动化与数据库写清技能'

    reliability = re.fullmatch(r'(?P<practices>[^。！？]+)。\s*使用\s*(?P<tools>[^。！？]+)[。]?', body)
    if prefix.startswith('工程与可靠性') and reliability:
        return (f'工程工具包括 {reliability["tools"]}。可靠性相关能力包括{reliability["practices"]}。',
                '改写为工程工具和可靠性能力两句')

    collection = re.fullmatch(r'(?:用|使用)\s*(?P<tool>[\w.+# -]+?)\s+采集(?P<object>[^。！？]+)并去重[。]?', body)
    if collection:
        return f'{prefix}借助 {collection["tool"]} 对{collection["object"]}进行采集和去重。', '用动作和处理对象重写项目表述'

    return None, '保留已有证据'


def resume_suggestions(resume: str, jobs: list[Job], skills: list['Skill'], edits: list[ResumeEdit]) -> list[dict]:
    lines = [line.strip() for line in resume.splitlines() if line.strip()]
    priorities = {(edit.job_id, edit.skill.casefold()): edit.action for edit in edits}

    def priority(skill: 'Skill') -> tuple[bool, bool, bool]:
        edit = priorities.get((skill.job_id, skill.name.casefold()))
        return edit is None, skill.priority != 'required', edit != 'promote'

    ordered = sorted(skills, key=priority)
    rewrites = []
    retained = []
    used = set()
    for skill in ordered:
        quote = skill.resume_evidence
        if not quote:
            continue
        source = next((line for line in lines if quote in line), None)
        if not source or len(source) > 450 or source in used:
            continue
        suggested, action = rewrite_resume_line(source)
        used.add(source)
        job = jobs[skill.job_id - 1]
        destination = rewrites if resume_text_changed(source, suggested) else retained
        destination.append({
            'job_id': skill.job_id, 'job_title': job.title, 'job_url': job.url,
            'skill': skill.name, 'status': skill.status, 'original_text': source,
            'suggested_text': suggested, 'supporting_fact': quote,
            'jd_evidence': skill.jd_evidence, 'action': action,
        })
        if len(rewrites) == 3:
            break
    return (rewrites + retained)[:3]
