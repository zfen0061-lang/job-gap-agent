"use strict";

const field = (id) => document.getElementById(id);
let sourceUrl = null;

function element(tag, text, style) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (style) node.className = style;
  return node;
}

function status(message, error = false, target = "status") {
  field(target).textContent = message;
  field(target).classList.toggle("danger", error);
}

async function api(path, body) {
  const options = body instanceof FormData
    ? { method: "POST", body }
    : body ? {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    } : {};
  const response = await fetch("/api/" + path, options);
  if (!(response.headers.get("Content-Type") || "").includes("application/json")) {
    throw new Error("网页请求未完成，请检查本地服务后再试。");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "网页请求未完成，可以重试。");
  return data;
}

async function run(button, message, action, target = "status") {
  if (button) button.disabled = true;
  status(message, false, target);
  try {
    await action();
    status("", false, target);
  } catch (error) {
    status(error.message, true, target);
  } finally {
    if (button) button.disabled = false;
  }
}

function showItems(id, items, render, emptyMessage) {
  const container = field(id);
  container.replaceChildren();
  if (!items.length && emptyMessage) container.append(element("p", emptyMessage, "muted"));
  for (const item of items) {
    const row = element("div", "", "item");
    render(row, item);
    container.append(row);
  }
}

function renderReport(report, resume) {
  field("report-title").textContent = report.target;
  field("summary").textContent = [
    report.job_check.risk,
    "技术证据覆盖率 " + report.match_percentage + "%",
    report.mode,
  ].join(" · ");
  const official = report.sources[0].url;
  field("official-entry").hidden = !official;
  if (official) field("official-link").href = official;
  else field("official-link").removeAttribute("href");
  const conditions = report.eligibility[0].conditions.filter((item) => item.jd_evidence);
  showItems("conditions", conditions, (row, item) => {
    const candidate = item.candidate || "资料待补充";
    row.append(element("h3", item.label + " · " + item.status, item.status === "不符合" ? "danger" : null));
    row.append(element("p", "岗位要求 " + item.required + " · 我的资料 " + candidate));
    row.append(element("p", "JD 原文「" + item.jd_evidence + "」", "muted"));
  }, "没有可从 JD 原文核对的明确硬条件。");

  const skillStatus = { matched: "证据覆盖", partial: "证据部分覆盖", missing: "简历未体现" };
  showItems("skills", report.skills, (row, item) => {
    row.append(element("h3", item.name + " · " + skillStatus[item.status]));
    row.append(element("p", "JD 原文「" + item.jd_evidence + "」"));
    if (item.resume_evidence) row.append(element("p", "简历依据「" + item.resume_evidence + "」", "muted"));
  });
  showItems("gaps", report.gap_analysis.filter((item) => item.suggestion), (row, item) => {
    row.append(element("h3", "补强 " + item.skill));
    row.append(element("p", item.suggestion.project));
    row.append(element("p", "验收 " + item.suggestion.deliverable, "muted"));
  });
  showItems("keywords", report.job_check.keywords, (row, item) => {
    row.append(element("h3", item.keyword + " · " + item.status));
    if (item.resume_keyword) row.append(element("p", "简历用词「" + item.resume_keyword + "」"));
    row.append(element("p", "JD 原文「" + item.jd_evidence + "」", "muted"));
  }, "暂无能从所引 JD 片段核实的用词。");

  const original = resume.split(/\r\n|\r|\n/);
  field("original").textContent = original.join("\n");
  const suggestions = report.resume_suggestions.filter((item) => (
    item.suggested_text && original.filter((line) => line.trim() === item.original_text).length === 1
  )).map((item) => {
    const start = original.findIndex((line) => line.trim() === item.original_text);
    return { ...item, start_line: start, end_line: start + 1 };
  });
  const edits = [];
  function updatePreview() {
    const changed = new Map();
    for (const { item, checkbox, wording } of edits) {
      const replacement = wording.value.trim();
      if (checkbox.checked && replacement && replacement !== item.original_text.trim()) {
        changed.set(item.start_line, { end: item.end_line, replacement });
      }
    }
    const draft = field("draft");
    draft.replaceChildren();
    for (let index = 0; index < original.length; ) {
      if (index) draft.append("\n");
      const update = changed.get(index);
      draft.append(update ? element("mark", update.replacement) : original[index]);
      index = update ? update.end : index + 1;
    }
    field("applied").textContent = "已采纳 " + changed.size + " 条改写 · 下载前请确认文字和经历属实。";
  }
  function showRewrites(items) {
    const saved = new Map(edits.map(({ item, checkbox, wording }) => [
      item.start_line, { checked: checkbox.checked, text: wording.value, item },
    ]));
    edits.length = 0;
    showItems("rewrites", items, (row, item) => {
      const id = "rewrite-" + edits.length;
      const label = element("label", item.skill + " · 建议写法");
      label.htmlFor = id;
      const wording = element("textarea", "");
      wording.id = id;
      wording.rows = 2;
      wording.maxLength = 800;
      const previous = saved.get(item.start_line);
      wording.value = previous?.item === item ? previous.text : item.suggested_text;
      const checkbox = element("input", "");
      checkbox.type = "checkbox";
      checkbox.checked = previous?.item === item && previous.checked;
      const choice = element("label", " 采纳这条改写");
      choice.prepend(checkbox);
      row.append(element("p", "原文「" + item.original_text + "」"), label, wording, choice);
      row.append(element("p", item.jd_evidence ? "JD 原文「" + item.jd_evidence + "」" : item.action, "muted"));
      edits.push({ item, checkbox, wording });
      checkbox.addEventListener("change", updatePreview);
      wording.addEventListener("input", updatePreview);
    }, "当前没有可直接替换的短句。可以继续生成更多段落级改写建议。");
    updatePreview();
  }
  showRewrites(suggestions);
  field("review-status").textContent = "";
  field("expand-review").onclick = () => run(field("expand-review"), "正在改写项目段落…", async () => {
    const data = await api("review", {
      target: report.target,
      resume,
      jd: report.skills.map((item) => item.jd_evidence).join("\n").slice(0, 4000),
    });
    if (!data.edits.length) throw new Error("本次没有通过事实检查的新增改写。原建议已保留。");
    const keep = edits.filter((edit) => edit.checkbox.checked).map((edit) => edit.item);
    const nonOverlapping = data.edits.filter((item) => !keep.some((saved) =>
      item.start_line < saved.end_line && saved.start_line < item.end_line
    ));
    const extras = [...keep, ...nonOverlapping];
    const earlier = suggestions.filter((item) => !extras.some((saved) =>
      item.start_line < saved.end_line && saved.start_line < item.end_line
    ));
    showRewrites([...extras, ...earlier].sort((left, right) => left.start_line - right.start_line));
    field("rewrites").prepend(element("p", "已检查 " + data.considered + " 段，" +
      data.edits.length + " 条扩展建议可审核。修改建议属于草稿，请逐条确认。", "muted"));
  }, "review-status");
  field("results").hidden = false;
  field("report-title").focus();
}

function profileData() {
  const profile = {};
  for (const id of ["graduation_year", "days_per_week", "months"]) {
    if (field(id).value) profile[id] = Number(field(id).value);
  }
  if (field("degree").value) profile.degree = field("degree").value;
  profile.months_at_least = field("months_at_least").checked;
  profile.cities = field("cities").value.split(/[,，、]/).map((city) => city.trim()).filter(Boolean);
  return profile;
}

function renderRecommendations(data, resume) {
  const recommended = data.results.filter((item) => item.stars > 0);
  const excluded = data.results.filter((item) => item.stars === 0);
  field("recommendation-summary").textContent =
    data.direction + " · 核对 " + data.results.length + " 条 · " + recommended.length +
    " 条可继续了解 · 星级仅用于本次投递排序。";
  function renderRow(row, item) {
    const report = item.report;
    row.append(element("h3", (item.company ? item.company + " · " : "") + report.target));
    row.append(element("p", "★".repeat(item.stars) + "☆".repeat(5 - item.stars) +
      " · " + item.label + " · 技术证据覆盖率 " + report.match_percentage + "%", "rank-stars"));
    for (const reason of item.match_evidence) {
      row.append(element("p", "匹配依据 " + reason.skill + " · 简历「" + reason.resume + "」", "muted"));
    }
    for (const condition of item.pending) {
      row.append(element("p", condition.label + " · " + condition.status + " · " + condition.required, "muted"));
    }
    if (item.missing.length) row.append(element("p", "值得补充证据 " + item.missing.join("、")));
    const detail = element("button", "打开体检和改写", "secondary");
    detail.type = "button";
    detail.addEventListener("click", () => renderReport(report, resume));
    row.append(detail);
    if (report.sources[0].url) {
      const official = element("a", " 官网岗位");
      official.href = report.sources[0].url;
      official.target = "_blank";
      official.rel = "noopener noreferrer";
      row.append(official);
    }
  }
  showItems("recommendation-list", recommended, renderRow, "本次暂无可以继续考虑的岗位。可用手工粘贴 JD 体检。");
  showItems("excluded-list", excluded, renderRow);
  field("excluded-jobs").hidden = !excluded.length;
  field("excluded-summary").textContent = excluded.length + " 条存在明确条件冲突";
  field("recommendation-notices").replaceChildren(...data.notices.map((notice) => element("p", notice, "muted")));
  field("recommendations").hidden = false;
  field("recommendation-title").focus();
}

field("recommend").addEventListener("click", () => run(field("recommend"), "正在搜索企业官网并核对岗位证据…", async () => {
  const resume = field("resume").value.trim();
  if (!resume) throw new Error("请先上传或粘贴简历。");
  const data = await api("recommend", { resume, profile: profileData() });
  renderRecommendations(data, resume);
}));

field("url").addEventListener("input", () => { sourceUrl = null; });
field("fetch").addEventListener("click", () => run(field("fetch"), "正在读取岗位正文…", async () => {
  const url = field("url").value.trim();
  if (!url) throw new Error("请填写岗位链接，也可以直接粘贴岗位正文。");
  const job = await api("job", { url });
  sourceUrl = job.url;
  field("title").value = job.title;
  field("jd").value = job.text;
  field("jd").focus();
}));

field("file").addEventListener("change", () => {
  const file = field("file").files[0];
  if (!file) return;
  run(field("file"), "正在提取简历文字…", async () => {
    const data = new FormData();
    data.append("resume", file);
    field("resume").value = (await api("extract", data)).text;
    field("resume").focus();
  });
});

field("analysis").addEventListener("submit", (event) => {
  event.preventDefault();
  run(field("run"), "正在分析。模型返回后会校验引用和公开硬条件…", async () => {
    const resume = field("resume").value.trim();
    const report = await api("check", {
      title: field("title").value.trim() || "目标岗位",
      jd: field("jd").value.trim(),
      resume,
      source_url: sourceUrl || field("url").value.trim() || null,
      profile: profileData(),
    });
    renderReport(report.report, resume);
  });
});

field("demo").addEventListener("click", () => run(field("demo"), "正在加载样例…", async () => {
  const data = await api("demo");
  sourceUrl = null;
  field("url").value = "";
  field("file").value = "";
  field("title").value = data.report.target;
  field("jd").value = data.jd;
  field("resume").value = data.resume;
  for (const [key, value] of Object.entries(data.profile)) {
    if (typeof value === "boolean") field(key).checked = value;
    else field(key).value = Array.isArray(value) ? value.join("、") : (value ?? "");
  }
  renderReport(data.report, data.resume);
}));

function downloadFile(file, filename) {
  const url = URL.createObjectURL(file);
  const link = element("a", "");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

field("download").addEventListener("click", () => {
  downloadFile(new Blob([field("draft").textContent], { type: "text/plain;charset=utf-8" }), "定制简历.txt");
});

field("pdf").addEventListener("click", () => run(field("pdf"), "正在生成 PDF…", async () => {
  if (location.protocol === "file:") {
    window.print();
    return;
  }
  const response = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resume: field("draft").textContent }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || "PDF 生成失败，请重试。");
  }
  downloadFile(await response.blob(), "定制简历.pdf");
}, "export-status"));
