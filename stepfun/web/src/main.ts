import './styles.css';
import { api, type CollectionState, type SearchHit } from './api';
import { Embedder, detectWebGPU, type BackendDecision, type DeviceKind, type Dtype } from './embedder';
import { chunkDocs, type Chunk, type SourceDoc } from './chunk';

// 与 /models 下的目录名保持一致
export const MODEL_ID = 'bge-small-zh-v1.5';
export const EMBEDDING_DIM = 512;

const $ = <T extends HTMLElement = HTMLElement>(sel: string) => document.querySelector(sel) as T;

type QuantManifest = {
  fp32?: { bytes: number; compression_ratio?: number };
  int8?: { bytes: number; compression_ratio?: number; method?: string; cosine_vs_fp32?: number };
  fp16?: { bytes: number; compression_ratio?: number; method?: string };
  numeric_check?: Record<string, any>;
};

const DTYPE_FILE: Record<string, keyof QuantManifest> = {
  fp32: 'fp32',
  fp16: 'fp16',
  int8: 'int8',
  q8: 'int8',
  uint8: 'int8',
};

const fmtBytes = (n?: number) => (n ? `${(n / 1048576).toFixed(2)} MiB` : '–');
const fmtMs = (n?: number | null) => (n == null ? '–' : n >= 1000 ? `${(n / 1000).toFixed(2)} s` : `${n.toFixed(0)} ms`);
const pct = (a: number, b: number) => `${Math.min(100, Math.round((a / Math.max(1, b)) * 100))}%`;

type SessionStats = {
  imports: number;
  chunks: number;
  embedMs: number;
  queries: number;
  lastBackend: string;
};

export type App = {
  collection: string;
  embedder: Embedder;
  decision: BackendDecision | null;
  manifest: QuantManifest | null;
  stats: SessionStats;
  lastLoadMs: number;
  bootLoad: { device: DeviceKind; dtype: Dtype; loadMs: number } | null;
  setBackend(device: DeviceKind, dtype?: Dtype): Promise<void>;
  ingest(docs: SourceDoc[], opts?: { refresh?: boolean; batchSize?: number; onStage?: (s: string, p: number) => void }): Promise<{
    chunks: Chunk[];
    embedMs: number;
    writeMs: number;
    refreshMs: number | null;
    vectors: number[][];
  }>;
  searchRaw(query: string, opts?: { mode?: string; topK?: number; rrfWindow?: number }): Promise<{
    hits: SearchHit[];
    embedMs: number;
    dbMs: number;
    e2eMs: number;
    strategy: string;
  }>;
  resetCollection(name: string): Promise<CollectionState>;
  refreshState(): Promise<CollectionState>;
};

export const app: App = {
  collection: 'edge_kb',
  embedder: new Embedder(),
  decision: null,
  manifest: null,
  stats: { imports: 0, chunks: 0, embedMs: 0, queries: 0, lastBackend: '–' },
  lastLoadMs: 0,
  bootLoad: null,

  async setBackend(device, dtype) {
    const chosen: Dtype = dtype || (device === 'webgpu' ? 'fp16' : 'int8');
    try {
      const { loadMs } = await app.embedder.init({
        device,
        dtype: chosen,
        onProgress: (p) => renderModelProgress(p),
      });
      app.decision = {
        device,
        dtype: chosen,
        requested: device,
        fallback: false,
        reason: device === 'webgpu' ? 'WebGPU 可用，已显式指定' : '按配置使用 WASM',
        adapter: app.decision?.adapter,
      };
      app.stats.lastBackend = `${device}/${chosen}`;
      app.lastLoadMs = loadMs;
      renderBadges(device, chosen, loadMs);
      renderRuntime(loadMs);
      return;
    } catch (err: any) {
      if (device === 'webgpu') {
        // 显式降级：WebGPU 初始化失败 → WASM/INT8，界面明确提示
        toast(`WebGPU 初始化失败，自动降级到 WASM：${err?.message || err}`, true);
        const { loadMs } = await app.embedder.init({
          device: 'wasm',
          dtype: 'int8',
          onProgress: (p) => renderModelProgress(p),
        });
        app.decision = {
          device: 'wasm',
          dtype: 'int8',
          requested: 'webgpu',
          fallback: true,
          reason: `WebGPU 初始化失败：${err?.message || err}`,
          adapter: app.decision?.adapter,
        };
        app.stats.lastBackend = 'wasm/int8（降级）';
        app.lastLoadMs = loadMs;
        renderBadges('wasm', 'int8', loadMs, true);
        renderRuntime(loadMs);
        return;
      }
      throw err;
    }
  },

  async ingest(docs, opts = {}) {
    const batchSize = opts.batchSize ?? (Number($<HTMLSelectElement>('#batch-size').value) || 16);
    const refresh = opts.refresh ?? true;
    const chunks = chunkDocs(docs, Number($<HTMLInputElement>('#chunk-size').value) || 600, Number($<HTMLInputElement>('#chunk-overlap').value) || 100);
    if (!chunks.length) throw new Error('没有可导入的文本');

    opts.onStage?.('向量化', 0);
    const t0 = performance.now();
    const res = await app.embedder.embed(
      chunks.map((c) => c.text),
      batchSize,
      (done, total) => opts.onStage?.('向量化', done / total),
    );
    const embedMs = performance.now() - t0;

    opts.onStage?.('写入 seekdb', 0.9);
    const payload = chunks.map((c, i) => ({
      id: c.id,
      text: c.text,
      embedding: res.vectors[i],
      metadata: c.metadata,
    }));
    const written = await api.upsert(app.collection, payload, refresh);
    opts.onStage?.('完成', 1);

    app.stats.imports += 1;
    app.stats.chunks += chunks.length;
    app.stats.embedMs += embedMs;

    return {
      chunks,
      vectors: res.vectors,
      embedMs,
      writeMs: written.writeMs,
      refreshMs: written.refreshMs,
    };
  },

  async searchRaw(query, opts = {}) {
    const mode = opts.mode || 'hybrid';
    const topK = opts.topK ?? 8;
    const t0 = performance.now();
    const q = await app.embedder.embed([query], 1, undefined, 'query');
    const embedMs = performance.now() - t0;
    const res = await api.search(app.collection, {
      query,
      embedding: q.vectors[0],
      topK,
      mode,
      rrfWindow: opts.rrfWindow ?? 60,
    });
    const e2eMs = performance.now() - t0;
    app.stats.queries += 1;
    return { hits: res.hits, embedMs, dbMs: res.tookMs, e2eMs, strategy: res.strategy };
  },

  async resetCollection(name) {
    await api.deleteCollection(name).catch(() => {});
    const state = await api.createCollection(name, EMBEDDING_DIM);
    app.collection = name;
    return state;
  },

  refreshState() {
    return api.state(app.collection);
  },
};

/* ------------------------------- 渲染 ------------------------------- */

function renderBadges(device: DeviceKind, dtype: Dtype, loadMs: number, fallback = false) {
  const info = app.manifest?.[DTYPE_FILE[dtype] as keyof QuantManifest] as any;
  const size = fmtBytes(info?.bytes);
  const el = $('#badges');
  el.innerHTML = `
    <span class="badge ${device === 'webgpu' ? 'gpu' : 'wasm'}">
      ${device === 'webgpu' ? '● WebGPU' : '● WASM'}${fallback ? ' · 已降级' : ''}
    </span>
    <span class="badge">模型 <b>${dtype.toUpperCase()}</b> · ${size}</span>
    <span class="badge">加载 <b>${fmtMs(loadMs)}</b></span>
    <span class="badge" id="seekdb-badge">seekdb <b>…</b></span>
  `;
}

function renderModelProgress(p: any) {
  if (!p || p.status === 'done') return;
  const file = String(p.file || '').split('/').pop();
  if (!file) return;
  const ratio = typeof p.progress === 'number' ? p.progress / 100 : 0;
  $('#ingest-progress').hidden = false;
  $('#ingest-stage').textContent = `加载模型 ${file}`;
  $('#ingest-pct').textContent = pct(ratio, 1);
  $('#ingest-bar').style.width = pct(ratio, 1);
}

function renderRuntime(loadMs: number) {
  const d = app.decision!;
  const info = app.manifest?.[DTYPE_FILE[d.dtype] as keyof QuantManifest] as any;
  const fp32 = app.manifest?.fp32?.bytes;
  const rows: [string, string, string?][] = [
    ['推理后端', d.device === 'webgpu' ? 'WebGPU (ONNX Runtime Web)' : 'WASM (ONNX Runtime Web)', d.device === 'webgpu' ? 'ok' : 'warn'],
    ['精度', d.dtype.toUpperCase(), ''],
    ['模型文件', `onnx/${d.dtype === 'fp32' ? 'model' : d.dtype === 'fp16' ? 'model_fp16' : 'model_int8'}.onnx`],
    ['模型体积', `${fmtBytes(info?.bytes)}${fp32 && info?.bytes ? ` · 压缩 ${(fp32 / info.bytes).toFixed(2)}×` : ''}`],
    ['GPU 适配器', d.adapter || (d.device === 'webgpu' ? '未知' : '未使用（WASM）')],
    ['模型加载', fmtMs(loadMs)],
    ['降级', d.fallback ? `是 · ${d.reason}` : '否', d.fallback ? 'warn' : 'ok'],
  ];
  $('#runtime-info').innerHTML = rows
    .map(([k, v, cls]) => `<div class="k">${k}</div><div class="v ${cls || ''}">${escapeHtml(v)}</div>`)
    .join('');
}

function renderSchema(state?: CollectionState) {
  const rows = [
    ['集合', app.collection],
    ['向量索引', `HNSW · ${EMBEDDING_DIM} 维 · cosine`],
    ['全文索引', 'FULLTEXT · ngram2 (2~3)'],
    ['融合方式', 'DBMS_HYBRID_SEARCH · RRF'],
    ['文档数', state ? String(state.count) : '–'],
  ];
  $('#schema-info').innerHTML = rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${escapeHtml(v)}</div>`).join('');
}

function renderKbState(state: CollectionState) {
  const rows = [
    ['文档块', String(state.count)],
    ['待刷新', state.pendingRefresh ? `${state.pendingRefresh} 条` : '0 条', state.pendingRefresh ? 'warn' : 'ok'],
    ['上次刷新', state.lastRefreshMs != null ? fmtMs(state.lastRefreshMs) : '–'],
    ['刷新时间', state.lastRefreshAt ? new Date(state.lastRefreshAt).toLocaleTimeString() : '–'],
  ];
  $('#kb-state').innerHTML = rows
    .map(([k, v, cls]) => `<div class="k">${k}</div><div class="v ${cls || ''}">${escapeHtml(String(v))}</div>`)
    .join('');
}

function renderSession() {
  const s = app.stats;
  const rows = [
    ['导入次数', String(s.imports)],
    ['本次向量化', `${s.chunks} 块`],
    ['向量化总耗时', fmtMs(s.embedMs)],
    ['平均吞吐', s.embedMs > 0 ? `${(s.chunks / (s.embedMs / 1000)).toFixed(1)} 块/s` : '–'],
    ['查询次数', String(s.queries)],
    ['当前后端', s.lastBackend],
  ];
  $('#session-info').innerHTML = rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${escapeHtml(v)}</div>`).join('');
}

function renderHits(hits: SearchHit[], query: string) {
  const box = $('#results');
  if (!hits.length) {
    box.innerHTML = `<div class="empty"><p>没有命中结果。</p><p class="muted">试试换用「向量」或「全文」模式，或补充语料。</p></div>`;
    return;
  }
  const terms = [...new Set([query, ...query.split(/\s+/).filter((t) => t.length >= 2)])];
  box.innerHTML = hits
    .map((h, i) => {
      const text = h.text.length > 420 ? `${h.text.slice(0, 420)}…` : h.text;
      return `
      <article class="hit">
        <div class="hit-head">
          <div><span class="hit-rank">#${i + 1}</span> <span class="hit-title">${escapeHtml(h.metadata?.title || h.metadata?.source || h.id)}</span></div>
          <span class="hit-score">${h.score == null ? '–' : h.score.toFixed(4)}</span>
        </div>
        <div class="hit-text">${highlight(text, terms)}</div>
        <div class="hit-meta">
          <span>块 ${h.metadata?.chunk_index ?? '–'}</span>
          <span>${h.metadata?.chars ?? text.length} 字</span>
          <span>${escapeHtml(h.id)}</span>
        </div>
      </article>`;
    })
    .join('');
}

function highlight(text: string, terms: string[]): string {
  let html = escapeHtml(text);
  for (const t of terms) {
    if (!t.trim()) continue;
    const safe = escapeRegExp(escapeHtml(t.trim()));
    html = html.replace(new RegExp(safe, 'gi'), (m) => `<mark>${m}</mark>`);
  }
  return html;
}

const escapeHtml = (s: string) =>
  String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
const escapeRegExp = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

let toastTimer: number | undefined;
function toast(msg: string, isError = false) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = `toast${isError ? ' err' : ''}`;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => (el.hidden = true), isError ? 6000 : 3000);
}

/* ------------------------------- 交互 ------------------------------- */

const state: { files: SourceDoc[] } = { files: [] };

async function handleFiles(fileList: FileList | File[]) {
  const files = Array.from(fileList).slice(0, 50);
  const docs: SourceDoc[] = [];
  for (const f of files) {
    if (f.size > 8 * 1024 * 1024) {
      toast(`跳过 ${f.name}：超过 8 MB`, true);
      continue;
    }
    const text = await f.text();
    if (f.name.endsWith('.json')) {
      try {
        const json = JSON.parse(text);
        docs.push({ source: f.name, text: JSON.stringify(json, null, 2) });
      } catch {
        docs.push({ source: f.name, text });
      }
    } else {
      docs.push({ source: f.name, text });
    }
  }
  state.files.push(...docs);
  $('#paste').value = state.files.map((d) => `# ${d.source}\n${d.text}`).join('\n\n');
  toast(`已读取 ${docs.length} 个文件，共 ${docs.reduce((n, d) => n + d.text.length, 0)} 字符`);
}

async function runIngest() {
  const paste = $('#paste').value.trim();
  const docs: SourceDoc[] = [...state.files];
  if (paste) {
    const fromPaste = state.files.length ? paste : '';
    docs.push({ source: `paste-${new Date().toISOString().slice(11, 19).replace(/:/g, '')}.txt`, text: fromPaste || paste });
  }
  if (!docs.length) {
    toast('请先导入文件或粘贴文本', true);
    return;
  }

  const btn = $<HTMLButtonElement>('#ingest');
  btn.disabled = true;
  $('#ingest-progress').hidden = false;
  const t0 = performance.now();

  try {
    const result = await app.ingest(docs, {
      refresh: true,
      onStage: (stage, p) => {
        $('#ingest-stage').textContent = stage;
        $('#ingest-pct').textContent = pct(p, 1);
        $('#ingest-bar').style.width = pct(p, 1);
      },
    });
    const totalMs = performance.now() - t0;
    $('#ingest-stage').textContent = '完成';
    $('#ingest-pct').textContent = '100%';
    $('#ingest-bar').style.width = '100%';
    $('#ingest-meta').textContent = `向量化 ${fmtMs(result.embedMs)} · 写入 ${fmtMs(result.writeMs)} · 刷新索引 ${fmtMs(result.refreshMs)} · 总耗时 ${fmtMs(totalMs)}`;

    renderStats(result.chunks.length, result.embedMs, totalMs);
    const st = await app.refreshState();
    renderKbState(st);
    renderSchema(st);
    renderSession();
    toast(`已写入 ${result.chunks.length} 个文本块，刷新后即可检索`);
    state.files = [];
  } catch (err: any) {
    $('#ingest-stage').textContent = '失败';
    $('#ingest-meta').textContent = err?.message || String(err);
    toast(`导入失败：${err?.message || err}`, true);
  } finally {
    btn.disabled = false;
  }
}

function renderStats(chunks: number, embedMs: number, totalMs: number) {
  const binfo = app.manifest?.[DTYPE_FILE[app.decision!.dtype] as keyof QuantManifest] as any;
  $('#ingest-stats').innerHTML = `
    <div class="stat"><span>文本块</span><b>${chunks}</b></div>
    <div class="stat alt"><span>向量化吞吐</span><b>${(chunks / (embedMs / 1000)).toFixed(1)}<small> 块/s</small></b></div>
    <div class="stat"><span>总耗时</span><b>${fmtMs(totalMs)}</b></div>
    <div class="stat"><span>模型体积</span><b>${fmtBytes(binfo?.bytes)}</b></div>
  `;
}

async function runSearch() {
  const query = $<HTMLInputElement>('#query').value.trim();
  if (!query) return;
  const mode = $('.segmented#mode .active')?.getAttribute('data-mode') || 'hybrid';
  const topK = Number($<HTMLInputElement>('#topk').value) || 8;
  const rrfWindow = Number($<HTMLInputElement>('#rrf-window').value) || 60;

  const btn = $<HTMLButtonElement>('#search');
  btn.disabled = true;
  try {
    const { hits, embedMs, dbMs, e2eMs } = await app.searchRaw(query, { mode, topK, rrfWindow });
    $('#timings').hidden = false;
    $('#t-embed').textContent = fmtMs(embedMs);
    $('#t-db').textContent = fmtMs(dbMs);
    $('#t-e2e').textContent = fmtMs(e2eMs);
    renderHits(hits, query);
    renderSession();
  } catch (err: any) {
    toast(`检索失败：${err?.message || err}`, true);
  } finally {
    btn.disabled = false;
  }
}

/* ------------------------------- 启动 ------------------------------- */

async function renderCollections() {
  const sel = $<HTMLSelectElement>('#collection-select');
  try {
    const { collections } = await api.listCollections();
    const names = collections.map((c) => c.name);
    if (!names.includes(app.collection)) names.unshift(app.collection);
    sel.innerHTML = names.map((n) => `<option value="${n}" ${n === app.collection ? 'selected' : ''}>${n}</option>`).join('');
  } catch {
    sel.innerHTML = `<option>${app.collection}</option>`;
  }
}

async function switchCollection(name: string) {
  app.collection = name;
  localStorage.setItem('kb.collection', name);
  const st = await api.state(name).catch(() => null);
  if (st) {
    renderKbState(st);
    renderSchema(st);
  }
  $('#results').innerHTML = `<div class="empty"><p>已切换到集合 <b>${escapeHtml(name)}</b>。</p><p class="muted">导入或检索都会作用在当前集合上。</p></div>`;
  $('#timings').hidden = true;
  toast(`当前集合：${name}`);
}

function wireEvents() {
  const dz = $('#dropzone');
  dz.addEventListener('click', () => $<HTMLInputElement>('#file-input').click());
  $<HTMLInputElement>('#file-input').addEventListener('change', (e) => {
    const files = (e.target as HTMLInputElement).files;
    if (files?.length) handleFiles(files);
  });
  dz.addEventListener('dragover', (e) => {
    e.preventDefault();
    dz.classList.add('drag');
  });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    dz.classList.remove('drag');
    if (e.dataTransfer?.files?.length) handleFiles(e.dataTransfer.files);
  });

  $('#ingest').addEventListener('click', runIngest);
  $('#clear-import').addEventListener('click', () => {
    state.files = [];
    $('#paste').value = '';
    $('#ingest-progress').hidden = true;
    $('#ingest-stats').innerHTML = '';
    $('#ingest-meta').textContent = '';
  });

  $('#search').addEventListener('click', runSearch);
  $('#query').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') runSearch();
  });

  document.querySelectorAll('.segmented#mode button').forEach((b) =>
    b.addEventListener('click', () => {
      document.querySelectorAll('.segmented#mode button').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
    }),
  );

  $<HTMLSelectElement>('#collection-select').addEventListener('change', (e) => {
    switchCollection((e.target as HTMLSelectElement).value);
  });

  $('#collection-new').addEventListener('click', async () => {
    const name = prompt('新集合名称（字母/数字/下划线）', `kb_${Date.now().toString(36).slice(-4)}`);
    if (!name) return;
    try {
      await api.createCollection(name, EMBEDDING_DIM);
      await switchCollection(name);
      await renderCollections();
    } catch (err: any) {
      toast(`创建集合失败：${err?.message || err}`, true);
    }
  });

  $('#load-sample').addEventListener('click', async () => {
    try {
      const data = await fetch('/eval/eval-set.json').then((r) => r.json());
      await handleFiles(
        data.corpus.slice(0, 12).map((d: any) => new File([d.text], d.source.replace(/\//g, '_'), { type: 'text/markdown' })),
      );
      toast(`已载入 ${Math.min(12, data.corpus.length)} 篇示例文档，可直接点击「向量化并写入」`);
    } catch (err: any) {
      toast(`载入示例失败：${err?.message || err}`, true);
    }
  });

  $('#refresh-index').addEventListener('click', async () => {
    try {
      const out = await api.refresh(app.collection);
      renderKbState(out.state);
      toast(`索引已刷新，用时 ${fmtMs(out.refreshMs)}`);
    } catch (err: any) {
      toast(`刷新失败：${err?.message || err}`, true);
    }
  });
}

async function boot() {
  wireEvents();
  renderSession();

  const health = await api.health().catch((e) => {
    toast(`seekdb 连接失败：${e.message}`, true);
    return null;
  });
  if (health) {
    app.collection = localStorage.getItem('kb.collection') || health.defaultCollection || 'edge_kb';
    const badge = document.querySelector('#seekdb-badge b');
    if (badge) badge.textContent = String(health.seekdb.version).replace('5.7.25-OceanBase ', '');
  }

  // 模型体积/压缩比元数据
  app.manifest = await fetch(`/models/${MODEL_ID}/quantization.json`)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);

  const params = new URLSearchParams(location.search);
  const forced = params.get('device') as DeviceKind | null;
  const forcedDtype = params.get('dtype') as Dtype | null;
  const probe = await detectWebGPU();
  const device: DeviceKind = forced === 'wasm' ? 'wasm' : forced === 'webgpu' ? 'webgpu' : probe.ok ? 'webgpu' : 'wasm';

  if (!app.decision) app.decision = { device, dtype: device === 'webgpu' ? 'fp16' : 'int8', requested: device, fallback: false, reason: probe.reason, adapter: probe.adapter };
  if (device === 'wasm' && !probe.ok) {
    app.decision = { ...app.decision, fallback: true, reason: `WebGPU 不可用：${probe.reason}` };
    toast(`未检测到 WebGPU（${probe.reason}），已使用 WASM`, true);
  }

  try {
    await app.setBackend(device, forcedDtype || undefined);
  } catch (err: any) {
    $('#badges').innerHTML = `<span class="badge err">模型加载失败：${escapeHtml(err?.message || String(err))}</span>`;
    return;
  }

  const st = await app.refreshState().catch(() => null);
  if (st) {
    renderKbState(st);
    renderSchema(st);
  }
  await renderCollections();
  app.bootLoad = app.decision ? { device: app.decision.device, dtype: app.decision.dtype, loadMs: app.lastLoadMs } : null;
  renderSession();

  if (params.has('bench')) {
    const mod = await import('./bench');
    mod.attachBench(app);
  }
}

boot();
