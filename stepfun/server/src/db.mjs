import { FulltextIndexConfig, Schema, SeekdbClient, VectorIndexConfig } from 'seekdb';
import { config } from './config.mjs';

async function requireCollection(name) {
  const col = await getCollectionOrNull(name);
  if (!col) {
    const err = new Error(`集合不存在：${name}`);
    err.status = 404;
    throw err;
  }
  return col;
}

/** 写入后尚未 refresh 的条数（用于索引刷新状态展示） */
const pendingRefresh = new Map();
/** 上次 refresh 的耗时与时间戳 */
const refreshInfo = new Map();

let client = null;

export function getClient() {
  if (client) return client;
  if (config.seekdbPath) {
    client = new SeekdbClient({ path: config.seekdbPath, database: config.seekdb.database });
  } else {
    client = new SeekdbClient({
      host: config.seekdb.host,
      port: config.seekdb.port,
      user: config.seekdb.user,
      password: config.seekdb.password,
      database: config.seekdb.database,
      queryTimeout: config.seekdb.queryTimeout,
    });
  }
  return client;
}

export function collectionSchema({ dimension = config.embeddingDim, distance = 'cosine', hnsw, fulltext } = {}) {
  return new Schema({
    vectorIndex: new VectorIndexConfig({
      hnsw: { dimension, distance, ...(hnsw || {}) },
    }),
    // ngram2 分析器对中英文混排都适用：无需分词词典即可对中文按 2~3 字滑窗建索引
    fulltextIndex: new FulltextIndexConfig(fulltext?.analyzer || 'ngram2', fulltext?.properties || {
      min_ngram_size: 2,
      max_ngram_size: 3,
    }),
  });
}

/**
 * 读取集合句柄。
 * 必须显式传 embeddingFunction: null —— 否则 SDK 会尝试解析集合元数据里记录的
 * "default-embed"（服务端推理函数），而本题的向量一律由浏览器算好后传入。
 */
async function getCollectionOrNull(name) {
  const db = getClient();
  try {
    return await db.getCollection({ name, embeddingFunction: null });
  } catch {
    return null;
  }
}

export async function ensureCollection(name, options = {}) {
  const db = getClient();
  const existing = await getCollectionOrNull(name);
  if (existing) {
    return { collection: existing, created: false };
  }
  const col = await db.createCollection({
    name,
    schema: collectionSchema(options),
    configuration: { dimension: options.dimension || config.embeddingDim, distance: options.distance || 'cosine' },
    embeddingFunction: null, // 向量由浏览器端算好后传入，服务端不做任何推理
  });
  return { collection: col, created: true };
}

export async function dropCollection(name) {
  const db = getClient();
  if (await getCollectionOrNull(name)) {
    await db.deleteCollection(name);
  }
  pendingRefresh.delete(name);
  refreshInfo.delete(name);
}

export async function listCollections() {
  const db = getClient();
  const cols = await db.listCollections({ withEmbeddingFunction: false });
  return cols.map((c) => ({
    name: c.name,
    dimension: c.dimension,
    distance: c.distance,
    pendingRefresh: pendingRefresh.get(c.name) || 0,
    lastRefreshMs: refreshInfo.get(c.name)?.ms ?? null,
    lastRefreshAt: refreshInfo.get(c.name)?.at ?? null,
  }));
}

export async function collectionState(name) {
  const db = getClient();
  const col = await getCollectionOrNull(name);
  if (!col) {
    return { name, exists: false, count: 0, dimension: null, pendingRefresh: 0 };
  }
  const count = await col.count();
  return {
    name,
    exists: true,
    count,
    dimension: col.dimension,
    distance: col.distance,
    pendingRefresh: pendingRefresh.get(name) || 0,
    lastRefreshMs: refreshInfo.get(name)?.ms ?? null,
    lastRefreshAt: refreshInfo.get(name)?.at ?? null,
  };
}

/**
 * 批量写入（upsert）。embeddings 必须由调用方（浏览器）计算。
 * 返回写入耗时与待刷新条数。
 */
export async function upsertDocs(name, docs, { batchSize = 32 } = {}) {
  const col = await requireCollection(name);
  const started = performance.now();
  let written = 0;

  for (let i = 0; i < docs.length; i += batchSize) {
    const slice = docs.slice(i, i + batchSize);
    await col.upsert({
      ids: slice.map((d) => d.id),
      documents: slice.map((d) => d.text),
      embeddings: slice.map((d) => d.embedding),
      metadatas: slice.map((d) => d.metadata || {}),
    });
    written += slice.length;
  }

  pendingRefresh.set(name, (pendingRefresh.get(name) || 0) + written);
  return { written, writeMs: performance.now() - started, pendingRefresh: pendingRefresh.get(name) };
}

export async function refreshIndex(name) {
  const col = await requireCollection(name);
  const started = performance.now();
  await col.refresh_index();
  const ms = performance.now() - started;
  pendingRefresh.set(name, 0);
  refreshInfo.set(name, { ms, at: new Date().toISOString() });
  return { refreshMs: ms, at: refreshInfo.get(name).at };
}

function unpack(result, include) {
  const ids = result.ids?.[0] || [];
  const docs = result.documents?.[0] || [];
  const metas = result.metadatas?.[0] || [];
  const dists = result.distances?.[0] || [];
  return ids.map((id, i) => {
    const row = { id, text: docs[i] ?? '', metadata: metas[i] ?? {}, score: dists[i] ?? null };
    if (include?.embeddings) row.embedding = result.embeddings?.[0]?.[i] ?? null;
    return row;
  });
}

/**
 * 混合检索：向量 + 全文在 seekdb 内部完成，RRF 融合由 DBMS_HYBRID_SEARCH 计算。
 * mode: hybrid | vector | fulltext
 */
export async function search(name, { embedding, query, topK = 10, mode = 'hybrid', rrfWindow = 60, rrfConstant = 60, where } = {}) {
  const col = await requireCollection(name);
  const include = ['documents', 'metadatas', 'distances'];

  const started = performance.now();
  let result;
  let strategy = mode;

  if (mode === 'vector') {
    result = await col.query({
      queryEmbeddings: [embedding],
      nResults: topK,
      where,
      include,
    });
  } else if (mode === 'fulltext') {
    result = await col.hybridSearch({
      query: { whereDocument: { $contains: query }, nResults: topK, where },
      nResults: topK,
      include,
    });
  } else {
    result = await col.hybridSearch({
      query: { whereDocument: { $contains: query }, nResults: topK, where },
      knn: { queryEmbeddings: [embedding], nResults: topK },
      rank: { rrf: { rankWindowSize: rrfWindow, rankConstant: rrfConstant } },
      nResults: topK,
      include,
    });
  }
  const tookMs = performance.now() - started;

  return { strategy, tookMs, hits: unpack(result, { embeddings: false }) };
}

export async function browse(name, { limit = 20, offset = 0 } = {}) {
  const col = await requireCollection(name);
  const res = await col.get({ limit, offset, include: ['documents', 'metadatas'] });
  const ids = res.ids || [];
  const docs = res.documents || [];
  const metas = res.metadatas || [];
  return ids.map((id, i) => ({ id, text: docs[i] ?? '', metadata: metas[i] ?? {} }));
}
