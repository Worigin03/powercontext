const view = document.getElementById("view");
const nav = document.getElementById("nav");
const toastEl = document.getElementById("toast");

const state = {
  health: null,
  stats: null,
  papers: [],
  glossary: [],
  q: "",
  mode: "hybrid",
  paperIds: [],
  showFilters: false,
  lastSearch: null,
  highlightTerms: [],
  ask: { q: "", result: null, loading: false },
  compare: { q: "", ids: [], result: null, loading: false },
  review: { topic: "", ids: [], markdown: "", loading: false },
  statsAt: 0,
};

const SAMPLES = [
  "联邦学习如何降低通信开销？",
  "边缘计算怎样降低物联网设备的延迟和能耗？",
  "RAG 相比纯参数化模型解决了什么问题？",
  "LoRA 为什么能减少大模型微调成本？",
  "端侧大模型面临哪些资源约束？",
];

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmt(n) {
  return Number(n || 0).toLocaleString("en-US");
}

function yearOf(p) {
  return (p && p.published ? p.published : "").slice(0, 4);
}

function authorsShort(a) {
  if (!a) return "未标注作者";
  const parts = a.split(",").map((x) => x.trim()).filter(Boolean);
  return parts.length > 3 ? parts.slice(0, 3).join(", ") + " 等" : parts.join(", ");
}

function arxivAbs(pdfUrl, id) {
  if (pdfUrl && pdfUrl.includes("/pdf/")) return pdfUrl.replace("/pdf/", "/abs/");
  return id && /^\d{4}\.\d{4,5}$/.test(id) ? `https://arxiv.org/abs/${id}` : "";
}

async function api(path, opts = {}) {
  const init = { ...opts };
  if (init.body && !(init.body instanceof FormData)) {
    init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
  }
  const resp = await fetch(path, init);
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return resp.json();
}

let toastTimer;
function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2600);
}

function copy(text, label = "已复制") {
  navigator.clipboard.writeText(text).then(() => toast(label)).catch(() => toast("复制失败"));
}

function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, "") || "overview";
  const [path, qs] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  return { view: parts[0] || "overview", parts, params: new URLSearchParams(qs || "") };
}

function setActive(viewName) {
  const group = viewName === "reader" || viewName === "paper" ? "library" : viewName;
  nav.querySelectorAll("a").forEach((a) => {
    a.classList.toggle("active", a.dataset.view === group);
  });
}

function go(hash) {
  location.hash = hash;
}

function searchForm(value, placeholder = "用中文提问，例如：联邦学习如何降低通信开销？") {
  return `
  <form class="searchbar" data-action="search-form">
    <input name="q" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false" />
    <span class="hint">Enter</span>
    <button class="go" type="submit">检索</button>
  </form>`;
}

function footer() {
  return `<footer class="foot">
    <span>PAPERTRAIL · SEEKDB HYBRID RETRIEVAL</span>
    <span>向量 384 维 · IK 全文 · RRF 融合 · 页码引用</span>
  </footer>`;
}

function emptyBox(title, desc) {
  return `<div class="empty"><strong>${esc(title)}</strong>${esc(desc)}</div>`;
}

/* ------------------------------ overview ------------------------------ */
function renderOverview() {
  const s = state.stats;
  if (!s) return emptyBox("正在读取文献库", "首次加载需要准备 embedding 模型。");
  const maxTopic = Math.max(1, ...s.topics.map((t) => t.count));
  view.innerHTML = `
  <section class="page">
    <div class="eyebrow">Research Workspace · 2026 OceanBase</div>
    <h1 class="display">把论文，读成<em>证据</em>。</h1>
    <p class="lede">中文提问，直接检索英文论文原文。用 seekdb 把向量语义与 IK 全文召回融进一条链路，每条答案都能回到论文的某一页。</p>
    ${searchForm("")}
    <div class="search-tools">
      <span class="meta">本地语料 ${fmt(s.papers)} 篇 · ${fmt(s.chunks)} 个证据块 · 平均检索 ${s.avg_latency_ms} ms</span>
    </div>

    <div class="stat-grid" style="margin-top:34px">
      <div class="stat"><div class="k">Indexed Papers</div><div class="v">${fmt(s.papers)}<small>篇</small></div></div>
      <div class="stat"><div class="k">Evidence Chunks</div><div class="v">${fmt(s.chunks)}<small>块</small></div></div>
      <div class="stat"><div class="k">Pages Parsed</div><div class="v">${fmt(s.pages)}<small>页</small></div></div>
      <div class="stat"><div class="k">Bilingual Terms</div><div class="v">${fmt(s.glossary_count)}<small>条</small></div></div>
    </div>

    <div class="grid-2" style="margin-top:36px">
      <div class="panel">
        <div class="panel-head"><h3>文献库</h3><span>按证据密度排序</span></div>
        <div class="panel-body" style="padding-top:6px;padding-bottom:6px">
          ${s.top_papers.map((p, i) => `
            <div class="paper-line" data-action="open-paper" data-id="${esc(p.id)}">
              <span class="idx">${String(i + 1).padStart(2, "0")}</span>
              <div><div class="t">${esc(p.title)}</div>
              <div class="m">${esc(p.topic)} · ${p.page_count} 页 · ${p.chunk_count} 块 · ${yearOf(p)}</div></div>
            </div>`).join("")}
        </div>
      </div>

      <div>
        <div class="panel">
          <div class="panel-head"><h3>研究主题</h3><span>TOPICS</span></div>
          <div class="panel-body">
            ${s.topics.map((t) => `
              <div class="topic-row">
                <span>${esc(t.name)}</span>
                <span class="bar"><i style="width:${Math.round((t.count / maxTopic) * 100)}%"></i></span>
                <span class="n">${t.count}</span>
              </div>`).join("")}
          </div>
        </div>
        <div class="panel" style="margin-top:22px">
          <div class="panel-head"><h3>最近检索</h3><span>LOG</span></div>
          <div class="panel-body">
            ${s.recent_queries.length ? s.recent_queries.map((q) => `
              <a class="query-pill" href="#/search?q=${encodeURIComponent(q.query)}">
                ${esc(q.query)}<span class="mono">${q.latency_ms}ms</span>
              </a>`).join("") : `<div class="help-line">还没有检索记录，从下面一个问题开始。</div>`}
          </div>
        </div>
      </div>
    </div>

    <div class="section-head"><h2 class="section">从一个真实问题开始</h2><span class="aside">点击直接提问</span></div>
    <div class="search-tools" style="gap:8px">
      ${SAMPLES.map((q) => `<button class="chip" data-action="ask-sample" data-q="${esc(q)}">${esc(q)}</button>`).join("")}
    </div>
    ${footer()}
  </section>`;

  view.querySelector('[data-action="search-form"]').addEventListener("submit", (e) => {
    e.preventDefault();
    submitSearch(e.target.q.value.trim());
  });
  view.querySelectorAll('[data-action="ask-sample"]').forEach((el) =>
    el.addEventListener("click", () => go(`#/ask?q=${encodeURIComponent(el.dataset.q)}`)));
  view.querySelectorAll('[data-action="open-paper"]').forEach((el) =>
    el.addEventListener("click", () => go(`#/paper/${encodeURIComponent(el.dataset.id)}`)));
}

/* ------------------------------ search ------------------------------ */
function resultItem(hit, maxBm25) {
  const vPct = Math.max(0, Math.min(100, Math.round((hit.vector_sim ?? 0) * 100)));
  const bPct = hit.bm25 == null ? 0 : Math.max(4, Math.round((hit.bm25 / maxBm25) * 100));
  const cite = `${hit.title} — 第 ${hit.page_no} 页`;
  return `
  <article class="result">
    <div class="rank">${String(hit.rank).padStart(2, "0")}</div>
    <div>
      <h3><a href="#/reader/${encodeURIComponent(hit.paper_id)}/${hit.page_no}">${esc(hit.title)}</a></h3>
      <div class="meta">
        <span>${esc(authorsShort(hit.authors))}</span>
        <span class="dot">${yearOf(hit)}</span>
        <span class="dot">${esc(hit.section || "正文")}</span>
        <span class="dot">p.${hit.page_no}</span>
      </div>
      <div class="snippet">${hit.snippet}</div>
      <div class="result-actions">
        <a class="btn ghost small" href="#/reader/${encodeURIComponent(hit.paper_id)}/${hit.page_no}">在原文中查看 →</a>
        <button class="btn small" data-action="copy-cite" data-cite="${esc(cite)}">复制引用</button>
        <button class="btn ghost small" data-action="compare-one" data-id="${esc(hit.paper_id)}">加入对比</button>
      </div>
    </div>
    <div class="result-side">
      <div class="score"><div class="lab"><span>VECTOR</span><span>${hit.vector_sim == null ? "—" : hit.vector_sim.toFixed(3)}</span></div><div class="track"><i style="width:${vPct}%"></i></div></div>
      <div class="score"><div class="lab"><span>BM25</span><span>${hit.bm25 == null ? "—" : hit.bm25.toFixed(2)}</span></div><div class="track"><i style="width:${bPct}%;background:var(--accent)"></i></div></div>
      <div class="score"><div class="lab"><span>RRF</span><span>${(hit.rrf || 0).toFixed(4)}</span></div></div>
      <div>V#${hit.ranks.vector ?? "—"} · F#${hit.ranks.fulltext ?? "—"}</div>
    </div>
  </article>`;
}

function renderSearchLoading() {
  view.innerHTML = `
  <section class="page">
    <div class="eyebrow">Evidence Search</div>
    <h1 class="display">证据检索</h1>
    ${searchForm(state.q)}
    <div class="result-head"><span class="count"><span class="spinner"></span> 正在召回</span></div>
  </section>`;
  bindSearchForm();
}

function submitSearch(q) {
  if (!q) return;
  state.q = q;
  const target = `#/search?q=${encodeURIComponent(q)}`;
  if (location.hash === target) runSearch();
  else go(target);
}

function bindSearchForm() {
  const form = view.querySelector('[data-action="search-form"]');
  if (form) form.addEventListener("submit", (e) => {
    e.preventDefault();
    submitSearch(e.target.q.value.trim());
  });
}

async function renderSearch() {
  const route = parseRoute();
  const q = route.params.get("q") || state.q;
  if (q) state.q = q;
  if (!state.q) {
    view.innerHTML = `
    <section class="page">
      <div class="eyebrow">Evidence Search</div>
      <h1 class="display">证据检索</h1>
      <p class="lede">输入研究问题，系统会同时执行向量召回、IK 全文召回，并用 RRF 融合两路排名。</p>
      ${searchForm("")}
      <div class="search-tools">${SAMPLES.slice(0, 3).map((s) => `<button class="chip" data-action="sample">${esc(s)}</button>`).join("")}</div>
      ${footer()}
    </section>`;
    bindSearchForm();
    view.querySelectorAll('[data-action="sample"]').forEach((b) => b.addEventListener("click", () => {
      state.q = b.textContent.trim(); go(`#/search?q=${encodeURIComponent(state.q)}`); runSearch();
    }));
    return;
  }
  if (!state.lastSearch || state.lastSearch.query !== state.q) {
    await runSearch(true);
    return;
  }
  renderSearchResults();
}

async function runSearch(silent = false) {
  if (!silent) renderSearchLoading();
  try {
    const data = await api("/api/search", {
      method: "POST",
      body: JSON.stringify({ query: state.q, top_k: 10, mode: state.mode, paper_ids: state.paperIds.length ? state.paperIds : null }),
    });
    state.lastSearch = data;
    state.highlightTerms = [...new Set([
      ...(data.query.match(/[A-Za-z][A-Za-z0-9\-]{2,}/g) || []),
      ...data.glossary.flatMap((g) => g.en.match(/[A-Za-z][A-Za-z0-9\-]{2,}/g) || []),
    ])].slice(0, 12);
    renderSearchResults();
  } catch (err) {
    view.innerHTML = `<section class="page"><div class="eyebrow">Error</div>${emptyBox("检索失败", err.message)}</section>`;
  }
}

function renderSearchResults() {
  const data = state.lastSearch;
  const maxBm25 = Math.max(0.001, ...data.hits.map((h) => h.bm25 || 0));
  const selected = state.paperIds;
  view.innerHTML = `
  <section class="page">
    <div class="eyebrow">Evidence Search</div>
    <h1 class="display">证据检索</h1>
    ${searchForm(state.q)}
    <div class="search-tools">
      <div class="mode-tabs">
        ${[["hybrid", "混合检索"], ["semantic", "仅语义"], ["keyword", "仅关键词"]].map(([m, label]) =>
          `<button data-action="mode" data-mode="${m}" class="${state.mode === m ? "active" : ""}">${label}</button>`).join("")}
      </div>
      <button class="chip ${selected.length ? "on" : ""}" data-action="toggle-filters">
        限定文献${selected.length ? ` (${selected.length})` : ""}
      </button>
      <span class="meta">${data.latency_ms} ms · 向量 ${data.counts.vector} · 全文 ${data.counts.fulltext}</span>
    </div>
    ${state.showFilters ? `
      <div class="panel" style="margin-top:14px"><div class="panel-body">
        <div class="paper-picker">
          ${state.papers.map((p) => `
            <label class="pick ${selected.includes(p.id) ? "on" : ""}">
              <input type="checkbox" data-action="pick-filter" value="${esc(p.id)}" ${selected.includes(p.id) ? "checked" : ""} />
              <span>${esc(p.title)}<span class="m">${esc(p.topic)} · ${p.page_count}p</span></span>
            </label>`).join("")}
        </div>
        <div class="search-tools"><button class="chip" data-action="clear-filters">清空限定</button></div>
      </div></div>` : ""}
    ${data.glossary.length ? `<div class="help-line">术语扩展：${data.glossary.map((g) => `<span class="kbd">${esc(g.zh)} → ${esc(g.en)}</span>`).join(" ")}</div>` : ""}

    <div class="result-head">
      <span class="count">命中 <b>${data.hits.length}</b> 条证据</span>
      <span class="lat">"${esc(data.query)}"</span>
    </div>
    ${data.hits.length ? data.hits.map((h) => resultItem(h, maxBm25)).join("") : emptyBox("没有命中", "试试更换关键词、扩大文献范围，或切换为「仅语义」。")}
    ${footer()}
  </section>`;

  bindSearchForm();
  view.querySelectorAll('[data-action="mode"]').forEach((b) => b.addEventListener("click", () => {
    state.mode = b.dataset.mode;
    runSearch();
  }));
  view.querySelector('[data-action="toggle-filters"]').addEventListener("click", () => {
    state.showFilters = !state.showFilters;
    renderSearchResults();
  });
  view.querySelectorAll('[data-action="pick-filter"]').forEach((c) => c.addEventListener("change", () => {
    const id = c.value;
    state.paperIds = c.checked ? [...state.paperIds, id] : state.paperIds.filter((x) => x !== id);
    runSearch();
  }));
  const clear = view.querySelector('[data-action="clear-filters"]');
  if (clear) clear.addEventListener("click", () => { state.paperIds = []; runSearch(); });
  view.querySelectorAll('[data-action="copy-cite"]').forEach((b) => b.addEventListener("click", () => copy(b.dataset.cite, "引用已复制")));
  view.querySelectorAll('[data-action="compare-one"]').forEach((b) => b.addEventListener("click", () => {
    state.compare.ids = [b.dataset.id];
    go(`#/compare?ids=${encodeURIComponent(b.dataset.id)}`);
  }));
}

/* ------------------------------ ask ------------------------------ */
function evidenceCard(e) {
  return `
  <div class="ev-item">
    <span class="ref">${esc(e.ref || "")}</span>
    <div class="quote">“${esc(e.text)}”</div>
    <div class="src">
      <span>${esc(authorsShort(e.authors))}</span>
      <a href="#/reader/${encodeURIComponent(e.paper_id)}/${e.page_no}">${esc(e.title.length > 62 ? e.title.slice(0, 62) + "…" : e.title)} · p.${e.page_no}</a>
      <span>${esc(e.section || "")}</span>
    </div>
  </div>`;
}

function renderAsk() {
  const route = parseRoute();
  const q = route.params.get("q");
  if (q && q !== state.ask.q) {
    state.ask.q = q;
    state.ask.result = null;
  }
  view.innerHTML = `
  <section class="page">
    <div class="eyebrow">Research Answer</div>
    <h1 class="display">研究问答</h1>
    <p class="lede">答案不是凭空生成的。系统先从 seekdb 召回证据，再按查询相关性重排句子，每条结论都绑定论文与页码。</p>
    <form class="searchbar" data-action="ask-form">
      <input name="q" value="${esc(state.ask.q)}" placeholder="输入研究问题，例如：端侧大模型如何降低推理能耗？" autocomplete="off" />
      <button class="go" type="submit">${state.ask.loading ? "分析中…" : "找证据"}</button>
    </form>
    <div class="search-tools">
      ${SAMPLES.slice(0, 4).map((s) => `<button class="chip" data-action="ask-sample" data-q="${esc(s)}">${esc(s)}</button>`).join("")}
    </div>
    <div id="ask-result">${state.ask.result ? renderAnswer(state.ask.result) : ""}</div>
    ${footer()}
  </section>`;

  const form = view.querySelector('[data-action="ask-form"]');
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const val = e.target.q.value.trim();
    if (!val) return;
    state.ask.q = val;
    runAsk();
  });
  view.querySelectorAll('[data-action="ask-sample"]').forEach((b) => b.addEventListener("click", () => {
    state.ask.q = b.dataset.q;
    runAsk();
  }));
  bindAnswerActions();
  if (state.ask.q && !state.ask.result) runAsk();
}

async function runAsk() {
  state.ask.loading = true;
  const box = document.getElementById("ask-result");
  if (box) box.innerHTML = `<div class="result-head"><span class="count"><span class="spinner"></span> 正在跨论文定位证据…</span></div>`;
  try {
    const data = await api("/api/answer", {
      method: "POST",
      body: JSON.stringify({ query: state.ask.q, top_k: 12 }),
    });
    state.ask.result = data;
    if (box) box.innerHTML = renderAnswer(data);
    bindAnswerActions();
  } catch (err) {
    if (box) box.innerHTML = emptyBox("问答失败", err.message);
  } finally {
    state.ask.loading = false;
    const btn = view.querySelector('[data-action="ask-form"] .go');
    if (btn) btn.textContent = "找证据";
  }
}

function renderAnswer(data) {
  return `
  <div class="answer-card">
    <div class="answer-top">
      <div class="q">${esc(data.query)}</div>
      <div class="meta">${data.evidence.length} 条证据 · ${data.papers.length} 篇文献<br/>${data.latency_ms} ms</div>
    </div>
    <div class="answer-body">
      <div class="answer-summary">${esc(data.summary)}</div>
      ${data.generated ? `<div class="answer-gen"><span class="label">LLM SYNTHESIS</span>${esc(data.generated)}</div>` : ""}
      <div class="evidence">${data.evidence.map(evidenceCard).join("")}</div>
      <div class="answer-foot">
        <button class="btn" data-action="copy-answer" data-idx="0">复制证据清单</button>
        <a class="btn ghost" href="#/review?topic=${encodeURIComponent(data.query)}">生成综述素材</a>
        <span class="help-line" style="margin:0;align-self:center">${data.llm_enabled ? "已启用大模型综合" : "未配置大模型：当前展示抽取式证据摘要，不做改写"}</span>
      </div>
    </div>
  </div>`;
}

function bindAnswerActions() {
  const btn = view.querySelector('[data-action="copy-answer"]');
  if (btn) btn.addEventListener("click", () => {
    const d = state.ask.result;
    if (!d) return;
    const text = d.evidence.map((e) => `- “${e.text}” (${e.title}, p.${e.page_no})`).join("\n");
    copy(text, "证据清单已复制");
  });
}

/* ------------------------------ compare ------------------------------ */
function renderCompare() {
  const route = parseRoute();
  const idsParam = route.params.get("ids");
  if (idsParam) state.compare.ids = idsParam.split(",").filter(Boolean).slice(0, 4);
  view.innerHTML = `
  <section class="page">
    <div class="eyebrow">Cross-Paper Comparison</div>
    <h1 class="display">跨论文对比</h1>
    <p class="lede">同一个问题，不同论文给出的证据并排放。适合写 related work 时快速定位差异。</p>
    <div class="panel"><div class="panel-body">
      <div class="field">
        <label>对比问题</label>
        <input type="text" id="cmp-q" value="${esc(state.compare.q)}" placeholder="例如：如何降低联邦学习的通信开销？" />
      </div>
      <div class="field">
        <label>选择 2–4 篇文献</label>
        <div class="paper-picker">
          ${state.papers.map((p) => `
            <label class="pick ${state.compare.ids.includes(p.id) ? "on" : ""}">
              <input type="checkbox" data-action="pick-compare" value="${esc(p.id)}" ${state.compare.ids.includes(p.id) ? "checked" : ""} />
              <span>${esc(p.title)}<span class="m">${esc(p.topic)} · ${yearOf(p)}</span></span>
            </label>`).join("")}
        </div>
      </div>
      <button class="btn" id="cmp-run">${state.compare.loading ? "对比中…" : "开始对比"}</button>
      <span class="help-line">已选 ${state.compare.ids.length} 篇</span>
    </div></div>
    <div id="cmp-result">${state.compare.result ? renderCompareResult(state.compare.result) : ""}</div>
    ${footer()}
  </section>`;

  view.querySelectorAll('[data-action="pick-compare"]').forEach((c) => c.addEventListener("change", () => {
    state.compare.q = document.getElementById("cmp-q").value;
    let ids = state.compare.ids;
    if (c.checked) {
      if (ids.length >= 4) { c.checked = false; toast("最多选择 4 篇"); return; }
      ids = [...ids, c.value];
    } else ids = ids.filter((x) => x !== c.value);
    state.compare.ids = ids;
    renderCompare();
  }));
  view.querySelector("#cmp-run").addEventListener("click", runCompare);
}

async function runCompare() {
  const q = document.getElementById("cmp-q").value.trim();
  if (!q) return toast("先输入一个问题");
  if (state.compare.ids.length < 2) return toast("至少选择 2 篇文献");
  state.compare.q = q;
  state.compare.loading = true;
  const btn = document.getElementById("cmp-run");
  btn.textContent = "对比中…";
  try {
    const data = await api("/api/compare", {
      method: "POST",
      body: JSON.stringify({ query: q, paper_ids: state.compare.ids }),
    });
    state.compare.result = data;
    document.getElementById("cmp-result").innerHTML = renderCompareResult(data);
  } catch (err) {
    toast(err.message);
  } finally {
    state.compare.loading = false;
    btn.textContent = "开始对比";
  }
}

function renderCompareResult(data) {
  return `
  <div class="compare-grid">
    ${data.columns.map((c) => `
      <div class="compare-col">
        <h3>${esc(c.title)}</h3>
        <div class="m">${esc(authorsShort(c.authors))} · ${yearOf(c)}</div>
        ${c.evidence.length ? c.evidence.map((e) => `
          <blockquote>“${esc(e.text)}”</blockquote>
          <div class="p">第 ${e.page_no} 页 · ${esc(e.section || "正文")}</div>
        `).join("") : `<div class="help-line">该文献中未找到直接证据</div>`}
      </div>`).join("")}
  </div>`;
}

/* ------------------------------ review ------------------------------ */
function renderReview() {
  const route = parseRoute();
  const topic = route.params.get("topic");
  if (topic) state.review.topic = topic;
  view.innerHTML = `
  <section class="page">
    <div class="eyebrow">Literature Review Kit</div>
    <h1 class="display">综述素材</h1>
    <p class="lede">按主题自动汇总跨论文证据，生成带页码的 Markdown 草稿。它不替你下结论，只把可核验的原材料排好。</p>
    <div class="review-grid">
      <div class="panel"><div class="panel-body">
        <div class="field">
          <label>综述主题</label>
          <input type="text" id="rv-topic" value="${esc(state.review.topic)}" placeholder="例如：边缘智能中的模型压缩技术" />
        </div>
        <div class="field">
          <label>限定文献（可选，默认全库）</label>
          <div class="paper-picker" style="grid-template-columns:1fr;max-height:260px">
            ${state.papers.map((p) => `
              <label class="pick ${state.review.ids.includes(p.id) ? "on" : ""}">
                <input type="checkbox" data-action="pick-review" value="${esc(p.id)}" ${state.review.ids.includes(p.id) ? "checked" : ""} />
                <span>${esc(p.title)}<span class="m">${esc(p.topic)}</span></span>
              </label>`).join("")}
          </div>
        </div>
        <button class="btn" id="rv-run">${state.review.loading ? "生成中…" : "生成 Markdown"}</button>
      </div></div>
      <div>
        ${state.review.markdown
          ? `<div class="search-tools" style="margin-bottom:10px">
               <button class="btn small" id="rv-download">下载 .md</button>
               <button class="btn ghost small" id="rv-copy">复制全文</button>
               <span class="meta">${state.review.markdown.length} 字符</span>
             </div>
             <div class="md-preview">${esc(state.review.markdown)}</div>`
          : emptyBox("还没有生成内容", "输入主题并点击生成，右侧会出现可直接编辑的综述素材。")}
      </div>
    </div>
    ${footer()}
  </section>`;

  view.querySelectorAll('[data-action="pick-review"]').forEach((c) => c.addEventListener("change", () => {
    state.review.topic = document.getElementById("rv-topic").value;
    state.review.ids = c.checked ? [...state.review.ids, c.value] : state.review.ids.filter((x) => x !== c.value);
    renderReview();
  }));
  view.querySelector("#rv-run").addEventListener("click", runReview);
  const dl = view.querySelector("#rv-download");
  if (dl) dl.addEventListener("click", () => {
    const blob = new Blob([state.review.markdown], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `review-${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
  const cp = view.querySelector("#rv-copy");
  if (cp) cp.addEventListener("click", () => copy(state.review.markdown, "综述素材已复制"));
}

async function runReview() {
  const topic = document.getElementById("rv-topic").value.trim();
  if (!topic) return toast("先输入综述主题");
  state.review.topic = topic;
  state.review.loading = true;
  const btn = document.getElementById("rv-run");
  btn.textContent = "生成中…";
  try {
    const data = await api("/api/review", {
      method: "POST",
      body: JSON.stringify({ topic, paper_ids: state.review.ids.length ? state.review.ids : null }),
    });
    state.review.markdown = data.markdown;
    renderReview();
  } catch (err) {
    toast(err.message);
    btn.textContent = "生成 Markdown";
  } finally {
    state.review.loading = false;
  }
}

/* ------------------------------ library ------------------------------ */
function renderLibrary() {
  view.innerHTML = `
  <section class="page">
    <div class="eyebrow">Local Corpus</div>
    <h1 class="display">文献库</h1>
    <p class="lede">所有 PDF 在本地解析、切块、向量化，再写入 seekdb。原始文件不离开这台机器。</p>

    <div class="ingest-box">
      <div class="ingest-cell">
        <h3>按 arXiv ID 入库</h3>
        <div class="ingest-row">
          <input id="ax-id" placeholder="例如 2005.11401 或论文链接" />
          <button class="btn" id="ax-add">抓取入库</button>
        </div>
        <div class="help-line">自动下载 PDF、抽取元数据、按页切块并生成向量。</div>
        <div id="ax-job"></div>
      </div>
      <div class="ingest-cell">
        <h3>上传本地 PDF</h3>
        <div class="ingest-row">
          <input type="file" id="up-file" accept="application/pdf" style="padding:7px" />
          <button class="btn" id="up-add">解析入库</button>
        </div>
        <div class="help-line">适合课程论文、实验报告与未公开稿件（≤40MB）。</div>
        <div id="up-job"></div>
      </div>
    </div>

    <div class="section-head"><h2 class="section">已索引文献</h2><span class="aside">${state.papers.length} 篇</span></div>
    <table class="lib-table">
      <thead><tr><th>论文</th><th>主题</th><th>年份</th><th>规模</th><th></th></tr></thead>
      <tbody>
        ${state.papers.map((p) => `
          <tr>
            <td><div class="title" data-action="open-paper" data-id="${esc(p.id)}">${esc(p.title)}</div>
                <div class="sub">${esc(authorsShort(p.authors))}</div></td>
            <td><span class="badge">${esc(p.topic)}</span></td>
            <td>${yearOf(p) || "—"}</td>
            <td class="sub">${p.page_count} 页<br/>${p.chunk_count} 块</td>
            <td style="white-space:nowrap">
              <a class="btn ghost small" href="#/reader/${encodeURIComponent(p.id)}/1">阅读</a>
              ${arxivAbs(p.pdf_url, p.arxiv_id) ? `<a class="btn ghost small" target="_blank" rel="noopener" href="${esc(arxivAbs(p.pdf_url, p.arxiv_id))}">arXiv</a>` : ""}
            </td>
          </tr>`).join("")}
      </tbody>
    </table>
    ${footer()}
  </section>`;

  view.querySelectorAll('[data-action="open-paper"]').forEach((el) =>
    el.addEventListener("click", () => go(`#/paper/${encodeURIComponent(el.dataset.id)}`)));

  document.getElementById("ax-add").addEventListener("click", async () => {
    const val = document.getElementById("ax-id").value.trim();
    if (!val) return toast("输入 arXiv ID 或链接");
    startJob("/api/ingest", { arxiv: val }, "ax-job");
  });
  document.getElementById("up-add").addEventListener("click", () => {
    const file = document.getElementById("up-file").files[0];
    if (!file) return toast("先选择一个 PDF");
    const fd = new FormData();
    fd.append("file", file);
    startJob("/api/upload", fd, "up-job");
  });
}

async function startJob(url, body, boxId) {
  const box = document.getElementById(boxId);
  box.innerHTML = `<div class="job">正在提交任务…<div class="bar"><i></i></div></div>`;
  try {
    const opts = body instanceof FormData
      ? { method: "POST", body }
      : { method: "POST", body: JSON.stringify(body) };
    const { job_id } = await api(url, opts);
    pollJob(job_id, box);
  } catch (err) {
    box.innerHTML = `<div class="job" style="color:var(--accent)">失败：${esc(err.message)}</div>`;
  }
}

async function pollJob(jobId, box) {
  const tick = async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      if (job.status === "done") {
        box.innerHTML = `<div class="job" style="color:var(--green)">完成：${job.result.chunks} 块 · ${job.result.pages} 页 · ${job.result.seconds}s</div>`;
        toast("论文已入库");
        await refreshPapers();
        renderLibrary();
        return;
      }
      if (job.status === "error") {
        box.innerHTML = `<div class="job" style="color:var(--accent)">失败：${esc(job.error)}</div>`;
        return;
      }
      box.innerHTML = `<div class="job">${job.status === "queued" ? "排队中" : "解析 / 向量化 / 写入 seekdb"}…<div class="bar"><i></i></div></div>`;
      setTimeout(tick, 1600);
    } catch (err) {
      box.innerHTML = `<div class="job" style="color:var(--accent)">任务查询失败：${esc(err.message)}</div>`;
    }
  };
  tick();
}

/* ------------------------------ paper detail ------------------------------ */
function renderPaper(paperId) {
  api(`/api/papers/${encodeURIComponent(paperId)}`).then(({ paper, outline, notes }) => {
    view.innerHTML = `
    <section class="page">
      <div class="eyebrow">Paper Detail</div>
      <div class="reader-head">
        <div>
          <h1>${esc(paper.title)}</h1>
          <div class="m">${esc(paper.authors)} · ${esc(paper.published)} · ${esc(paper.topic)}</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <a class="btn" href="#/reader/${encodeURIComponent(paper.id)}/1">开始阅读</a>
          ${arxivAbs(paper.pdf_url, paper.arxiv_id) ? `<a class="btn ghost" target="_blank" rel="noopener" href="${esc(arxivAbs(paper.pdf_url, paper.arxiv_id))}">打开 arXiv</a>` : ""}
        </div>
      </div>
      <div class="grid-2">
        <div>
          <div class="panel"><div class="panel-head"><h3>摘要</h3><span>ABSTRACT</span></div>
            <div class="panel-body" style="font-family:var(--serif);font-size:14.5px;line-height:1.85">${esc(paper.abstract || "（该文献未提供摘要）")}</div>
          </div>
          <div class="section-head"><h2 class="section">章节结构</h2><span class="aside">${outline.length} 段</span></div>
          ${outline.map((o) => `
            <div class="paper-line" data-action="jump" data-page="${o.first_page}">
              <span class="idx">p.${o.first_page}</span>
              <div><div class="t">${esc(o.section || "正文")}</div>
              <div class="m">${o.chunks} 个证据块 · 至第 ${o.last_page} 页</div></div>
            </div>`).join("")}
        </div>
        <div>
          <div class="panel"><div class="panel-head"><h3>阅读笔记</h3><span>${notes.length} 条</span></div>
            <div class="panel-body">
              <div class="field"><textarea id="pd-note" placeholder="记录一个判断、疑问或可复用的观点…"></textarea></div>
              <button class="btn small" id="pd-save">保存笔记</button>
              <div style="margin-top:18px">${notes.map(noteItem).join("") || `<div class="help-line">还没有笔记。</div>`}</div>
            </div>
          </div>
        </div>
      </div>
      ${footer()}
    </section>`;
    view.querySelectorAll('[data-action="jump"]').forEach((el) =>
      el.addEventListener("click", () => go(`#/reader/${encodeURIComponent(paper.id)}/${el.dataset.page}`)));
    document.getElementById("pd-save").addEventListener("click", async () => {
      const comment = document.getElementById("pd-note").value.trim();
      if (!comment) return toast("笔记不能为空");
      await api("/api/notes", { method: "POST", body: JSON.stringify({ paper_id: paper.id, comment }) });
      toast("笔记已保存");
      renderPaper(paper.id);
    });
  }).catch((err) => {
    view.innerHTML = `<section class="page">${emptyBox("文献不存在", err.message)}</section>`;
  });
}

function noteItem(n) {
  return `<div class="note-item">
    ${n.quote ? `<div class="q">“${esc(n.quote)}”</div>` : ""}
    <div>${esc(n.comment)}</div>
    <div class="m">${n.page_no ? `p.${n.page_no} · ` : ""}${esc(n.created_at || "")}</div>
  </div>`;
}

/* ------------------------------ reader ------------------------------ */
async function renderReader(paperId, pageNo) {
  const route = parseRoute();
  const q = route.params.get("q") || state.q;
  try {
    const [{ paper, outline, notes }, page] = await Promise.all([
      api(`/api/papers/${encodeURIComponent(paperId)}`),
      api(`/api/papers/${encodeURIComponent(paperId)}/pages/${pageNo}`),
    ]);
    const terms = [...new Set([
      ...(q.match(/[A-Za-z][A-Za-z0-9\-]{2,}/g) || []),
      ...state.highlightTerms,
    ])].slice(0, 14);
    view.innerHTML = `
    <section class="page">
      <div class="eyebrow">Reader · p.${pageNo}</div>
      <div class="reader-head">
        <div>
          <h1>${esc(paper.title)}</h1>
          <div class="m">${esc(authorsShort(paper.authors))} · ${esc(paper.published)} · 第 ${pageNo} / ${paper.page_count} 页</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <a class="btn ghost" href="#/paper/${encodeURIComponent(paper.id)}">文献详情</a>
          ${arxivAbs(paper.pdf_url, paper.arxiv_id) ? `<a class="btn" target="_blank" rel="noopener" href="${esc(arxivAbs(paper.pdf_url, paper.arxiv_id))}">打开 PDF</a>` : ""}
        </div>
      </div>
      <div class="reader-layout">
        <aside class="toc">
          <h4>Outline</h4>
          ${outline.map((o) => `<a class="${pageNo >= o.first_page && pageNo <= o.last_page ? "on" : ""}" href="#/reader/${encodeURIComponent(paper.id)}/${o.first_page}${q ? `?q=${encodeURIComponent(q)}` : ""}">${esc(o.section || "正文")} <span style="color:var(--muted);font-family:var(--mono);font-size:10px">p.${o.first_page}</span></a>`).join("")}
        </aside>
        <div>
          <div class="reader-page">
            ${page.chunks.map((c) => `<p>${markText(c.content, terms)}</p>`).join("")}
          </div>
          <div class="reader-nav">
            ${pageNo > 1 ? `<a class="btn ghost small" href="#/reader/${encodeURIComponent(paper.id)}/${pageNo - 1}${q ? `?q=${encodeURIComponent(q)}` : ""}">← 上一页</a>` : "<span></span>"}
            <span>${esc(paper.topic)} · ${page.chunks.length} 个证据块</span>
            ${pageNo < paper.page_count ? `<a class="btn ghost small" href="#/reader/${encodeURIComponent(paper.id)}/${pageNo + 1}${q ? `?q=${encodeURIComponent(q)}` : ""}">下一页 →</a>` : "<span></span>"}
          </div>
          <div class="note-panel">
            <div class="section-head" style="margin-top:0"><h2 class="section" style="font-size:17px">本页笔记</h2><span class="aside">${notes.length} 条</span></div>
            ${notes.map(noteItem).join("") || `<div class="help-line">在阅读时记录判断，之后可按文献回看。</div>`}
            <div class="field" style="margin-top:14px"><textarea id="rd-note" placeholder="这一页的关键结论、疑问或可引用的句子…"></textarea></div>
            <button class="btn small" id="rd-save">保存到第 ${pageNo} 页</button>
          </div>
        </div>
      </div>
      ${footer()}
    </section>`;
    document.getElementById("rd-save").addEventListener("click", async () => {
      const comment = document.getElementById("rd-note").value.trim();
      if (!comment) return toast("笔记不能为空");
      await api("/api/notes", { method: "POST", body: JSON.stringify({ paper_id: paper.id, page_no: pageNo, comment }) });
      toast("笔记已保存");
      renderReader(paper.id, pageNo);
    });
  } catch (err) {
    view.innerHTML = `<section class="page">${emptyBox("无法打开该页", err.message)}</section>`;
  }
}

function markText(text, terms) {
  let out = esc(text);
  const valid = terms.filter((t) => t && t.length >= 3).sort((a, b) => b.length - a.length);
  if (!valid.length) return out;
  try {
    const re = new RegExp("(" + valid.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi");
    out = out.replace(re, "<mark>$1</mark>");
  } catch (_) {}
  return out;
}

/* ------------------------------ router ------------------------------ */
async function refreshPapers() {
  const data = await api("/api/papers");
  state.papers = data.papers;
}

async function loadBase() {
  try {
    const [health, stats, papers, glossary] = await Promise.all([
      api("/api/health"),
      api("/api/stats"),
      api("/api/papers"),
      api("/api/glossary"),
    ]);
    state.health = health;
    state.stats = stats;
    state.statsAt = Date.now();
    state.papers = papers.papers;
    state.glossary = glossary.glossary;
    const pulse = document.getElementById("db-pulse");
    pulse.classList.add(health.ok ? "ok" : "bad");
    document.getElementById("db-label").textContent = health.ok ? "seekdb 已连接" : "seekdb 未连接";
    document.getElementById("rail-meta").textContent = `${stats.papers} 篇 · ${fmt(stats.chunks)} 块 · ${stats.avg_latency_ms}ms`;
  } catch (err) {
    document.getElementById("db-pulse").classList.add("bad");
    document.getElementById("db-label").textContent = "无法连接服务";
  }
}

async function route() {
  const r = parseRoute();
  setActive(r.view);
  window.scrollTo({ top: 0 });
  switch (r.view) {
    case "overview": renderOverview(); break;
    case "search": renderSearch(); break;
    case "ask": renderAsk(); break;
    case "compare": renderCompare(); break;
    case "review": renderReview(); break;
    case "library": renderLibrary(); break;
    case "paper": renderPaper(r.parts[1]); break;
    case "reader": renderReader(r.parts[1], Number(r.parts[2] || 1)); break;
    default: go("#/overview");
  }
}

view.addEventListener("click", (e) => {
  const a = e.target.closest("a");
  if (a && a.getAttribute("href") === location.hash) e.preventDefault();
});

window.addEventListener("hashchange", route);

(async function init() {
  await loadBase();
  await route();
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
      const input = view.querySelector(".searchbar input");
      if (input) { e.preventDefault(); input.focus(); }
    }
  });
})();
