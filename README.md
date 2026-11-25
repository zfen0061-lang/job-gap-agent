# AI 求职竞争力分析 Agent

基于 Python 和 DeepSeek 的求职技能差距分析工具。检索真实招聘 JD，对照简历生成带原文证据的匹配度报告和项目补强建议，支持 SQLite 历史记录与 Evals 评估。

输入目标岗位关键词和简历，工具会从招聘官网筛选最多 10 个单岗详情页，生成 Markdown 和 JSON 报告。可以先查看[离线样例报告](examples/离线样例报告.md)，了解技能缺口、推荐项目及其验收方式如何呈现。

报告还可以逐句给出简历修改建议、核对岗位硬条件，并用同一批 JD 对比修改前后的简历。

## 快速运行

使用 Python 3.10 或更新版本。可以克隆仓库，也可以在 GitHub 选择 **Code → Download ZIP** 后解压。先运行离线样例，确认环境可用。

```powershell
git clone https://github.com/zfen0061-lang/job-gap-agent.git
cd job-gap-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe agent.py --offline
```

在 macOS 或 Linux 上，进入项目目录后运行：

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python agent.py --offline
```

离线演示用一条模拟 JD、两版模拟简历和一份模拟候选人资料，展示逐句建议、硬条件、前后对比及一次格式纠错。正常输出为 **33.3%**，包含 RAG、Agent Workflow 两项缺口。结果保存在 `reports/report.md` 和 `reports/report.json`，并追加到 `reports/career_analysis.sqlite3`。

## 搜索真实岗位

将 `.env.example` 复制为项目根目录的 `.env`，填入自己的 `DEEPSEEK_API_KEY`。当前终端显式设置的同名环境变量优先于 `.env`。本地 `.env` 已被 Git 忽略。

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 在本地编辑 .env 并填入自己的 Key 后运行。
.\.venv\Scripts\python.exe agent.py --target 'AI Agent 应用研发实习'
```

上面的命令会检索真实岗位，并用内置模拟简历生成报告。分析自己的简历时，把文件放在 `private/`，再指定路径：

```powershell
.\.venv\Scripts\python.exe agent.py --target 'AI Agent 应用研发实习' --resume 'private/resume.txt'
```

需要临时使用另一把 Key 时，可以在 PowerShell 7.2 及以上运行 `$env:DEEPSEEK_API_KEY = Read-Host -Prompt 'DeepSeek API Key' -MaskInput`，仅影响当前终端及其启动的脚本。

`--resume` 接受 UTF-8 TXT 或带文字层的 PDF。想只发送技能与项目描述时，可以使用删去联系方式的 TXT。分析请求会把简历文字和 JD 发送给 DeepSeek，搜索请求只包含岗位关键词。省略 `--resume` 时使用脚本内的模拟简历。

在线模式默认从百度、字节跳动和腾讯的招聘官网筛选最多 10 个单岗详情页。程序利用搜索返回的正文，无有效正文时尝试 Fetch，再按链接和正文去重。搜索、网页读取和模型分析使用同一个 DeepSeek Key。检索数不足时按实际样本生成报告，并标明数量。筛选后的岗位保存为 `reports/jd_snapshot.json`，可用 `--jobs-file` 重新分析。职位是否继续招聘以官网为准。模型默认为 `deepseek-flash`，可以用 `DEEPSEEK_MODEL` 调整。 [DeepSeek Search & Fetch 文档](https://api-docs.deepseek.com/guides/search/) · [DeepSeek JSON Output 文档](https://api-docs.deepseek.com/guides/json_mode/)

## 核对硬条件与对比两版简历

将 [候选人资料模板](candidate_profile.example.json) 复制到 `private/profile.json` 并按自己情况填写。五个字段都可省略。学历值使用 `associate`、`bachelor`、`master` 或 `doctor`，毕业届次使用四位年份，地点使用城市名称数组，到岗天数和实习月数使用整数。资料只在本地用于和 JD 硬条件比对。

```powershell
.\.venv\Scripts\python.exe agent.py --jobs-file reports/jd_snapshot.json `
    --profile private/profile.json --resume private/before.txt `
    --compare-resume private/after.txt --out-dir reports/compare
```

也可以把 `--jobs-file` 换成 `--target`，首次搜索取得的同一批 JD 会同时用于两版简历。报告按岗位展示已核对、待确认和不符合的硬条件，以及对应原文。技术覆盖率不计入这些条件。对比沿用初次提取的技能、权重和评分规则，展示覆盖率差和发生变化的证据。省略 `--compare-resume` 时生成普通报告。

修改建议引用已有简历句子及其对应 JD。程序会用原有事实改写措辞，例如把一串工具写成有明确分类的技能描述，并保留「熟悉」「了解」等原有程度。只有实际发生措辞变化时才显示建议写法。采纳建议后，可以用新版简历重跑对比。

## 用本地岗位样本分析

```powershell
.\.venv\Scripts\python.exe agent.py --jobs-file sample_jobs.json --resume resume.txt
```

`sample_jobs.json` 是 2026 年 11 月 25 日整理的 10 条百度官网岗位摘要，每条均附原始链接。样本包括应用开发、平台研发、算法及评测，适合验证批量分析流程。做实际投递决策时，可以导入同一方向与同一学历层级的岗位。导入文件包含 `title`、`text`、`url` 三个字段。此模式读取本地 JD，模型分析仍使用 DeepSeek。

如需只用模拟 JD 与真实模型测试，设置 Key 后运行 `agent.py` 即可。

## 匹配度口径

- 必需技能权重为 2，加分技能权重为 1。简历证据完整、部分覆盖、未体现时分别计 100%、50%、0%。先计算各岗位的技术要求覆盖率，再对岗位等权平均。
- 每项技能的 JD 依据来自按序编号的原文片段。模型选择编号，程序取回原文。完整或部分覆盖也必须提供可在简历中找到的短句。匹配深度由模型判断。
- 薪资只记岗位实际披露的金额。学历、实习时长和地点条件以岗位来源为准。覆盖率反映所选样本与简历表述的贴合程度。
- JSON 结构或证据校验失败时，将错误摘要追加到提示词，最多请求三次。认证、网络或账户错误直接显示。

## 本地验证

```powershell
.\.venv\Scripts\python.exe evals.py
.\.venv\Scripts\python.exe -m unittest -v
```

Evals 使用固定响应走同一套分析、证据校验和 SQLite 入库流程，无需 DeepSeek Key。

| 用例 | 匹配度 | 判断 | 重试次数 |
| --- | ---: | --- | ---: |
| 完美匹配 | 100.0% | 无技能缺口 | 0 |
| 技能缺口 | 33.3% | 缺少 Agent Workflow、RAG 的简历证据 | 0 |
| 乱码后纠错 | 75.0% | pytest 仅部分覆盖 | 1 |

预期汇总为 `一共处理 3 条，成功入库 3 条，触发重试 1 次。` 重试次数从第二次请求开始计。用例失败会在终端列出原因并以非零退出，其他用例继续执行。这些用例验证流程和预期行为，实际模型的判断质量需要用真实响应另行评估。

此外，事实校验 Evals 会故意注入简历原文里没有的数字、技术栈和项目经历，验证三类虚构证据都会触发纠错。

### 使用真实 DeepSeek 响应评估

在已配置 `DEEPSEEK_API_KEY` 的终端运行：

```powershell
.\.venv\Scripts\python.exe evals.py --live
```

三个用例都使用 DeepSeek 返回的 JSON 做评估。第三个用例会先在本地注入一条无效响应，验证错误原因确实进入下一次请求，再由 DeepSeek 返回纠错结果。仅通过评估的报告才写入 `reports/career_evals_live.sqlite3`。

## SQLite 历史记录

`career_analysis` 表保存岗位、来源、JD 条数、匹配度、模型请求次数和完整的 `report_json`。完整 JSON 包含技能依据、差距分析和来源链接。每次成功执行追加一条记录，校验失败的响应不会写入。

普通分析和 Evals 默认分别保存到 `reports/career_analysis.sqlite3` 和 `reports/career_evals.sqlite3`。两个脚本均支持 `--db` 指定保存位置。重复运行会保留每次的历史，Evals 汇总只统计当次运行。

数据库与 Git 忽略规则已经配置。用于真实分析的简历可以放在被忽略的 `private/` 目录。API Key 可从本地 `.env` 或本机环境变量读取。
