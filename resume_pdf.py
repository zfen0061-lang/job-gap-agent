import os
import re
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape


SECTION_HEADINGS = frozenset({
    '教育背景', '教育经历', '专业技能', '核心技能', '技能', '项目', '项目经历', '个人项目',
    '课程项目', '实习经历', '工作经历', '其他经历', '个人简介', '奖项与证书',
})
PROJECT_HEADING = re.compile(r'\s*[|｜]\s*(独立开发|个人项目|团队项目|课程项目)\s*$')
DATE_LINE = re.compile(r'^20\d{2}\.\d{1,2}(?:\s|[（(—–-]|$)')


def resume_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont, TTFError

    configured = os.getenv('RESUME_PDF_FONT')
    windows_fonts = Path(os.getenv('WINDIR', 'C:/Windows')) / 'Fonts'
    candidates = (
        [Path(configured)] if configured else
        [windows_fonts / 'msyh.ttc', windows_fonts / 'simhei.ttf',
         Path('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'),
         Path('/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf')]
    )
    for path in candidates:
        if path.is_file():
            try:
                font = TTFont('ResumeCJK', str(path), subfontIndex=0)
            except (TTFError, OSError):
                continue
            pdfmetrics.registerFont(font)
            return 'ResumeCJK'
    if configured:
        raise ValueError('RESUME_PDF_FONT 无法读取，请指定有效的中文 TrueType 字体。')
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    return 'STSong-Light'


def render_resume_pdf(text: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    if not text.strip():
        raise ValueError('简历内容为空。')
    buffer = BytesIO()
    font = resume_font()
    body = ParagraphStyle('ResumeBody', fontName=font, fontSize=10.5, leading=16,
                          textColor=colors.HexColor('#171F20'), wordWrap='CJK', alignment=TA_LEFT,
                          spaceAfter=4)
    heading = ParagraphStyle('ResumeHeading', parent=body, fontSize=12, leading=17,
                             textColor=colors.HexColor('#164B44'), spaceBefore=10, keepWithNext=True)
    name = ParagraphStyle('ResumeName', parent=body, fontSize=18, leading=25, spaceAfter=10, keepWithNext=True)
    attached = ParagraphStyle('ResumeAttached', parent=body, keepWithNext=True)
    story = []
    lines = text.strip().splitlines()
    for index, line in enumerate(lines):
        clean = ''.join(char for char in line if char.isprintable() or char == '\t').strip()
        if not clean:
            story.append(Spacer(1, 4))
            continue
        label = clean.rstrip('：:')
        if index == 0 and len(clean) <= 30:
            style = name
        elif label in SECTION_HEADINGS or PROJECT_HEADING.search(clean):
            style = heading
        elif DATE_LINE.match(clean) or (index + 1 < len(lines) and lines[index + 1].startswith('技术栈 ')):
            style = attached
        else:
            style = body
        story.append(Paragraph(escape(clean).replace('\t', '  '), style))
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=39, rightMargin=39,
                            topMargin=36, bottomMargin=36, title='定制简历', author='')
    doc.build(story)
    return buffer.getvalue()
