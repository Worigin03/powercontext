/**
 * 端到端基准（在浏览器内运行，由 scripts/bench.mjs 通过 Playwright 触发）。
 *
 * 覆盖：模型加载、语料向量化吞吐、写入/索引刷新、查询向量化、seekdb 检索、端到端延迟，
 * 以及量化前后在固定评测集上的 Recall@10 / MRR@10 / nDCG@10。
 */
import type { App } from './main';
import { chunkDocs, type SourceDoc } from './chunk';
import { api } from './api';

type EvalFile = {
  corpus: SourceDoc[];
  queries: { id: string; query: string; goldPhrase?: string; anchor?: string; type?: string; doc?: string }[];
};

/** 5-gram 集合，用于把「小节正文锚点」映射到具体的块 */
function ngrams(text: string, n = 5): Set<string> {
  const t = text.replace(/\s+/g, '').toLowerCase();
  const out = new Set<string>();
  for (let i = 0; i + n <= t.length; i++) out.add(t.slice(i, i + n));
  return out;
}

function coverage(anchor: Set<string>, chunkGrams: Set<string>): number {
  if (!anchor.size) return 0;
  let hit = 0;
  for (const g of anchor) if (chunkGrams.has(g)) hit++;
  return hit / anchor.size;
}

export type BenchConfig = {
  device: 'webgpu' | 'wasm';
  dtype: 'fp32' | 'fp16' | 'int8';
  collection?: string;
  batchSize?: number;
  topK?: number;
  repeat?: number;
  evalPath?: string;
  chunkSize?: number;
  chunkOverlap?: number;
  skipIngest?: boolean;
  mode?: 'hybrid' | 'vector' | 'fulltext';
};

const median = (xs: number[]) => {
  const a = [...xs].sort((x, y) => x - y);
  if (!a.length) return 0;
  const mid = Math.floor(a.length / 2);
  return a.length % 2 ? a[mid] : (a[mid - 1] + a[mid]) / 2;
};
const p95 = (xs: number[]) => {
  const a = [...xs].sort((x, y) => x - y);
  return a.length ? a[Math.min(a.length - 1, Math.floor(a.length * 0.95))] : 0;
};
const round = (n: number, d = 2) => +n.toFixed(d);

function recallAtK(ranked: string[], gold: Set<string>, k: number) {
  if (!gold.size) return 0;
  const top = ranked.slice(0, k);
  let hit = 0;
  for (const g of gold) if (top.includes(g)) hit++;
  return hit / gold.size;
}

function mrrAtK(ranked: string[], gold: Set<string>, k: number) {
  for (let i = 0; i < Math.min(k, ranked.length); i++) if (gold.has(ranked[i])) return 1 / (i + 1);
  return 0;
}

function ndcgAtK(ranked: string[], gold: Set<string>, k: number) {
  let dcg = 0;
  for (let i = 0; i < Math.min(k, ranked.length); i++) {
    if (gold.has(ranked[i])) dcg += 1 / Math.log2(i + 2);
  }
  const ideal = gold.size > 0 ? Array.from({ length: Math.min(k, gold.size) }, (_, i) => 1 / Math.log2(i + 2)).reduce((a, b) => a + b, 0) : 0;
  return ideal ? dcg / ideal : 0;
}

export async function runBench(app: App, cfg: BenchConfig) {
  const evalPath = cfg.evalPath || '/eval/eval-set.json';
  const batchSize = cfg.batchSize ?? 16;
  const topK = cfg.topK ?? 10;
  const repeat = cfg.repeat ?? 3;
  const collection = cfg.collection || `bench_${cfg.dtype}_${cfg.device}`;
  const searchMode = cfg.mode || 'hybrid';

  const data: EvalFile = await fetch(evalPath).then((r) => r.json());

  // 1) 模型加载：页面启动时已经按同一配置加载过，则直接采用那次冷启动耗时（含模型文件下载）
  const bootMatch = app.bootLoad && app.bootLoad.device === cfg.device && app.bootLoad.dtype === cfg.dtype;
  const loadStart = performance.now();
  if (!bootMatch) await app.setBackend(cfg.device, cfg.dtype);
  const loadMs = bootMatch ? app.bootLoad!.loadMs : performance.now() - loadStart;

  // 2) 冷启动首次推理
  const coldStart = performance.now();
  await app.embedder.embed(['warmup: seekdb hybrid retrieval'], 1);
  const coldMs = performance.now() - coldStart;

  // 3) 向量化 + 写入
  const chunks = chunkDocs(data.corpus, cfg.chunkSize ?? 600, cfg.chunkOverlap ?? 100);
  let embedMs = 0;
  let writeMs = 0;
  let refreshMs = 0;
  let vectors: number[][] = [];

  if (!cfg.skipIngest) {
    await app.resetCollection(collection);
    const t0 = performance.now();
    const res = await app.embedder.embed(chunks.map((c) => c.text), batchSize);
    embedMs = performance.now() - t0;
    vectors = res.vectors;

    const upsert = await api.upsert(
      collection,
      chunks.map((c, i) => ({ id: c.id, text: c.text, embedding: res.vectors[i], metadata: c.metadata })),
      false,
    );
    writeMs = upsert.writeMs;
    const refresh = await api.refresh(collection);
    refreshMs = refresh.refreshMs;
    app.collection = collection;
  } else {
    app.collection = collection;
    await app.embedder.embed(chunks.slice(0, 4).map((c) => c.text), batchSize);
  }

  // 4) 金标解析：优先用 goldPhrase 精确包含；否则用 anchor 的 5-gram 覆盖率 >= 50%
  const chunkGrams = chunks.map((c) => ngrams(c.text));
  const goldByQuery = new Map<string, Set<string>>();
  for (const q of data.queries) {
    let ids: Set<string>;
    if (q.goldPhrase) {
      const phrase = q.goldPhrase.toLowerCase();
      ids = new Set(chunks.filter((c) => c.text.toLowerCase().includes(phrase)).map((c) => c.id));
    } else {
      const anchor = ngrams(q.anchor || q.query);
      ids = new Set(
        chunks.filter((_, i) => coverage(anchor, chunkGrams[i]) >= 0.5).map((c) => c.id),
      );
    }
    goldByQuery.set(q.id, ids);
  }

  // 5) 查询循环
  const perQuery: {
    id: string;
    query: string;
    goldCount: number;
    topIds: string[];
    goldIds: string[];
    embedMs: number;
    dbMs: number;
    e2eMs: number;
    recall10: number;
    mrr10: number;
    ndcg10: number;
  }[] = [];
  const allEmbed: number[] = [];
  const allDb: number[] = [];
  const allE2e: number[] = [];
  let missingGold = 0;

  for (const q of data.queries) {
    const gold = goldByQuery.get(q.id) || new Set<string>();
    if (!gold.size) missingGold++;
    const rounds = { embed: [] as number[], db: [] as number[], e2e: [] as number[], recall: [] as number[], docRecall: [] as number[], mrr: [] as number[], ndcg: [] as number[] };
    const goldDocs = new Set(Array.from(gold).map((id) => id.split(':')[0]));
    const topIdsByQuery = new Map<string, string[]>();
    for (let r = 0; r < repeat; r++) {
      const t0 = performance.now();
      const emb = await app.embedder.embed([q.query], 1, undefined, 'query');
      const t1 = performance.now();
      const res = await api.search(collection, {
        query: q.query,
        embedding: emb.vectors[0],
        topK,
        mode: searchMode,
        rrfWindow: 60,
      });
      const t2 = performance.now();
      const ranked = res.hits.map((h) => h.id);
      topIdsByQuery.set(q.id, ranked);
      rounds.embed.push(t1 - t0);
      rounds.db.push(res.tookMs);
      rounds.e2e.push(t2 - t0);
      rounds.recall.push(recallAtK(ranked, gold, 10));
      rounds.docRecall.push(ranked.slice(0, 10).some((id) => goldDocs.has(id.split(':')[0])) ? 1 : 0);
      rounds.mrr.push(mrrAtK(ranked, gold, 10));
      rounds.ndcg.push(ndcgAtK(ranked, gold, 10));
    }
    const row = {
      id: q.id,
      query: q.query,
      goldCount: gold.size,
      topIds: topIdsByQuery.get(q.id) || [],
      goldIds: Array.from(gold).slice(0, 4),
      embedMs: median(rounds.embed),
      dbMs: median(rounds.db),
      e2eMs: median(rounds.e2e),
      docRecall10: median(rounds.docRecall),
      recall10: median(rounds.recall),
      mrr10: median(rounds.mrr),
      ndcg10: median(rounds.ndcg),
    };
    perQuery.push(row);
    allEmbed.push(row.embedMs);
    allDb.push(row.dbMs);
    allE2e.push(row.e2eMs);
  }

  const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
  // 金标缺失（小节正文被截断/切块边界）的查询不计入质量均值
  const valid = perQuery.filter((q) => q.goldCount > 0);

  return {
    config: {
      device: cfg.device,
      dtype: cfg.dtype,
      batchSize,
      topK,
      repeat,
      chunkSize: cfg.chunkSize ?? 600,
      chunkOverlap: cfg.chunkOverlap ?? 100,
      collection,
      userAgent: navigator.userAgent,
      hardwareConcurrency: navigator.hardwareConcurrency,
      adapter: app.decision?.adapter || null,
      fallback: app.decision?.fallback ?? false,
      loadMs: round(loadMs),
      coldFirstInferenceMs: round(coldMs),
    },
    scale: { docs: data.corpus.length, chunks: chunks.length, chars: chunks.reduce((n, c) => n + c.text.length, 0), queries: data.queries.length, missingGold },
    ingest: {
      embedMs: round(embedMs),
      chunksPerSecond: round(chunks.length / (embedMs / 1000), 1),
      charsPerSecond: round(chunks.reduce((n, c) => n + c.text.length, 0) / (embedMs / 1000), 0),
      writeMs: round(writeMs),
      refreshMs: round(refreshMs),
      vectorBytes: vectors.length * (vectors[0]?.length || 0) * 4,
    },
    search: {
      embedMs: { median: round(median(allEmbed)), p95: round(p95(allEmbed)), mean: round(mean(allEmbed)) },
      dbMs: { median: round(median(allDb)), p95: round(p95(allDb)), mean: round(mean(allDb)) },
      e2eMs: { median: round(median(allE2e)), p95: round(p95(allE2e)), mean: round(mean(allE2e)) },
    },
    quality: {
      evaluatedQueries: valid.length,
      docRecall10: round(mean(valid.map((q) => q.docRecall10)), 4),
      recall10: round(mean(valid.map((q) => q.recall10)), 4),
      mrr10: round(mean(valid.map((q) => q.mrr10)), 4),
      ndcg10: round(mean(valid.map((q) => q.ndcg10)), 4),
    },
    perQuery,
    backend: app.decision,
  };
}

/**
 * 单项优化对比：同一页面内、同一份语料、连续测量，尽量减少机器负载波动的影响。
 * 对比维度：批大小（8/16/32/64）× 是否按长度分桶。
 */
export async function runSweep(
  app: App,
  cfg: { device: 'webgpu' | 'wasm'; dtype: 'fp32' | 'fp16' | 'int8'; sample?: number; sizes?: number[] },
) {
  const sample = cfg.sample ?? 96;
  const sizes = cfg.sizes ?? [8, 16, 32, 64];

  const bootMatch = app.bootLoad && app.bootLoad.device === cfg.device && app.bootLoad.dtype === cfg.dtype;
  if (!bootMatch) await app.setBackend(cfg.device, cfg.dtype);

  const data: EvalFile = await fetch('/eval/eval-set.json').then((r) => r.json());
  const all = chunkDocs(data.corpus, 600, 100);
  const texts = all.slice(0, sample).map((c) => c.text);

  // 预热，排除首次 kernel 编译
  await app.embedder.embed(texts.slice(0, 4), 4);

  const rows: { batchSize: number; bucket: boolean; ms: number; chunksPerSecond: number; charsPerSecond: number }[] = [];
  for (const bucket of [true, false]) {
    for (const size of sizes) {
      const t0 = performance.now();
      await app.embedder.embed(texts, size, undefined, 'document', bucket);
      const ms = performance.now() - t0;
      rows.push({
        batchSize: size,
        bucket,
        ms: round(ms),
        chunksPerSecond: round(texts.length / (ms / 1000), 1),
        charsPerSecond: round(texts.reduce((n, t) => n + t.length, 0) / (ms / 1000), 0),
      });
    }
  }
  return { device: cfg.device, dtype: cfg.dtype, sample: texts.length, avgChars: round(texts.reduce((n, t) => n + t.length, 0) / texts.length), rows };
}

export function attachBench(app: App) {
  (window as any).__bench = {
    run: (cfg: BenchConfig) => runBench(app, cfg),
    sweep: (cfg: any) => runSweep(app, cfg),
    app,
  };
  (window as any).__benchReady = true;
}
