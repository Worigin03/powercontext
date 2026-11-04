const state = { data: null, query: "", mode: "search" };

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

// The backend escapes snippet text before wrapping matches in <mark>.
// Re-allow only those tags so recorded HTML can never inject markup.
const marked = (html) =>
  esc(html).replace(/&lt;mark&gt;/g, "<mark>").replace(/&lt;\/mark&gt;/g, "</mark>");

const fmt = (n) => Number(n || 0).toLocaleString("en-US");
const authorsShort = (authors) => {
  const list = String(authors || "").split(",").map((s) => s.trim()).filter(Boolean);
  return list.length > 3 ? `${list.slice(0, 3).join(", ")} 等` : list.join(", ");
};
const yearOf = (published) => String(published || "").slice(0, 4);

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".segmented button").forEach((btn) => {
    btn.setAttribute("aria-selected", String(btn.dataset.mode === mode));
  });
  render();
}

function setQuery(query) {
  state.query = query;
  document.querySelectorAll(".query-list button").forEach((btn) => {
    btn.setAttribute("aria-pressed", String(btn.dataset.q === query));
  });
  render();
}

function renderQueries() {
  const list = $("#query-list");
  list.innerHTML = Object.keys(state.data.answers).map((q) => `
    <button type="button" data-q="${esc(q)}" aria-pressed="false">${esc(q)}</button>
  `).join("");
  list.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => setQuery(btn.dataset.q));
  });
}

function renderSearch(record) {
  const hits = record.hits.slice(0, 4);
  const total = record.count ?? record.hits.length;
  const max = {
    vec: Math.max(...hits.map((h) => Number(h.vector_sim) || 0), 0.001),
    bm25: Math.max(...hits.map((h) => Number(h.bm25) || 0), 0.001),
    rrf: Math.max(...hits.map((h) => Number(h.rrf) || 0), 0.001),
  };
  const bar = (label, value, maxValue, digits) => {
    const shown = value === null || value === undefined || Number(value) <= 0;
    return `
    <div class="score-row">
      <span>${label}</span>
      <span class="bar"><i style="width:${shown ? 0 : Math.max(2, Math.min(100, (Number(value) / maxValue) * 100))}%"></i></span>
      <b>${shown ? "—" : Number(value).toFixed(digits)}</b>
    </div>`;
  };
  return `
    <div class="result-top">
      <h3 class="result-q">${esc(record.query)}</h3>
      <div class="result-meta">共 <b>${total}</b> 条候选 · 展示前 ${hits.length} 条<br />混合检索 ${record.latency_ms} ms · RRF k=60</div>
    </div>
    ${hits.map((hit) => `
      <article class="hit">
        <div class="rank">${String(hit.rank).padStart(2, "0")}</div>
        <div>
          <div class="title">${esc(hit.title)}</div>
          <div class="sub">${esc(authorsShort(hit.authors))} · ${esc(yearOf(hit.published))} · ${esc(hit.section)} · p.${esc(hit.page_no)}</div>
          <div class="snippet">${marked(hit.snippet)}</div>
        </div>
        <div class="scores" aria-label="检索分数">
          ${bar("VECTOR", hit.vector_sim, max.vec, 3)}
          ${bar("BM25", hit.bm25, max.bm25, 2)}
          ${bar("RRF", hit.rrf, max.rrf, 4)}
        </div>
      </article>`).join("")}
  `;
}

function renderAnswer(record) {
  const papers = record.papers || [];
  const refOf = (paperId) => {
    const found = papers.find((p) => p.paper_id === paperId);
    return found ? found.ref : "P?";
  };
  return `
    <div class="result-top">
      <h3 class="result-q">${esc(record.query)}</h3>
      <div class="result-meta">${record.evidence.length} 条证据 · ${papers.length} 篇文献<br />${record.latency_ms} ms · 句子级重排</div>
    </div>
    <div class="answer-card">
      <p class="summary">${esc(record.summary)}</p>
      ${record.evidence.map((ev) => `
        <article class="evidence">
          <span class="ref">${esc(refOf(ev.paper_id))}</span>
          <p>${esc(ev.text)}</p>
          <footer>
            <span>${esc(ev.title)}</span>
            <span>· ${esc(ev.section)} · p.${esc(ev.page_no)}</span>
          </footer>
        </article>`).join("")}
      <div class="answer-foot">
        <span class="pill">抽取式 · 未改写原文</span>
        <span>每条证据可直接翻到论文对应页码核对</span>
      </div>
    </div>
  `;
}

function render() {
  const panel = $("#panel-result");
  if (!state.data || !state.query) return;
  const record = state.mode === "answer"
    ? state.data.answers[state.query]
    : state.data.searches[state.query];
  if (!record) {
    panel.innerHTML = `<p class="result-meta">没有这条记录。</p>`;
    return;
  }
  panel.innerHTML = state.mode === "answer" ? renderAnswer(record) : renderSearch(record);
}

function setupLightbox() {
  const dialog = $("#lightbox");
  if (!dialog || typeof dialog.showModal !== "function") return;
  const img = $("#lightbox-img");
  document.querySelectorAll(".shot").forEach((btn) => {
    btn.addEventListener("click", () => {
      img.src = btn.dataset.full;
      dialog.showModal();
    });
  });
  const close = () => dialog.close();
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  $(".lightbox .close").addEventListener("click", close);
}

(async function init() {
  document.querySelectorAll(".segmented button").forEach((btn) => {
    btn.addEventListener("click", () => setMode(btn.dataset.mode));
  });
  try {
    const response = await fetch("demo-data.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    renderQueries();
    state.query = Object.keys(state.data.answers)[0];
    document.querySelectorAll(".query-list button")[0].setAttribute("aria-pressed", "true");
    render();
  } catch (error) {
    $("#panel-result").innerHTML = `
      <p class="result-meta">演示数据加载失败：${esc(error.message)}。请在静态服务器下打开本站。</p>`;
  }
  setupLightbox();
})();
