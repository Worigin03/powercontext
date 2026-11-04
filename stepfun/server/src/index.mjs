import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';
import { config } from './config.mjs';
import {
  browse,
  collectionState,
  dropCollection,
  ensureCollection,
  getClient,
  listCollections,
  refreshIndex,
  search,
  upsertDocs,
} from './db.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDist = path.resolve(__dirname, '../../web/dist');

const app = express();
app.use(express.json({ limit: '64mb' }));

// 开启 crossOriginIsolated：WASM 后端才能启用多线程（SharedArrayBuffer）
app.use((_req, res, next) => {
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
  res.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
  next();
});

const asyncRoute = (fn) => (req, res) => {
  Promise.resolve(fn(req, res)).catch((err) => {
    const status = err?.status || 500;
    res.status(status).json({ error: err?.message || String(err) });
  });
};

app.get('/api/health', asyncRoute(async (_req, res) => {
  const db = getClient();
  const started = performance.now();
  const rows = await db.execute('select version() as version');
  res.json({
    ok: true,
    seekdb: {
      mode: config.seekdbPath ? 'embedded' : 'server',
      host: config.seekdbPath || `${config.seekdb.host}:${config.seekdb.port}`,
      version: rows?.[0]?.version ?? 'unknown',
      pingMs: +(performance.now() - started).toFixed(2),
    },
    defaultCollection: config.defaultCollection,
    embeddingDim: config.embeddingDim,
  });
}));

app.get('/api/collections', asyncRoute(async (_req, res) => {
  res.json({ collections: await listCollections() });
}));

app.get('/api/collections/:name/state', asyncRoute(async (req, res) => {
  res.json(await collectionState(req.params.name));
}));

app.post('/api/collections', asyncRoute(async (req, res) => {
  const { name, dimension, distance, hnsw, fulltext } = req.body || {};
  if (!name || !/^[a-zA-Z0-9_]{1,64}$/.test(name)) {
    const err = new Error('集合名只允许字母、数字与下划线（1-64 字符）');
    err.status = 400;
    throw err;
  }
  const { created } = await ensureCollection(name, { dimension, distance, hnsw, fulltext });
  res.json({ ...(await collectionState(name)), created });
}));

app.delete('/api/collections/:name', asyncRoute(async (req, res) => {
  await dropCollection(req.params.name);
  res.json({ ok: true, name: req.params.name });
}));

app.post('/api/collections/:name/upsert', asyncRoute(async (req, res) => {
  const { docs, refresh = true, batchSize } = req.body || {};
  if (!Array.isArray(docs) || docs.length === 0) {
    const err = new Error('docs 不能为空');
    err.status = 400;
    throw err;
  }
  await ensureCollection(req.params.name);
  const written = await upsertDocs(req.params.name, docs, { batchSize });
  let refreshed = null;
  if (refresh) refreshed = await refreshIndex(req.params.name);
  res.json({
    ...written,
    refreshMs: refreshed?.refreshMs ?? null,
    state: await collectionState(req.params.name),
  });
}));

app.post('/api/collections/:name/refresh', asyncRoute(async (req, res) => {
  const out = await refreshIndex(req.params.name);
  res.json({ ...out, state: await collectionState(req.params.name) });
}));

app.post('/api/collections/:name/search', asyncRoute(async (req, res) => {
  const { query, embedding, topK, mode, rrfWindow, rrfConstant, where } = req.body || {};
  if (!Array.isArray(embedding) || embedding.length !== config.embeddingDim) {
    const err = new Error(`embedding 必须是 ${config.embeddingDim} 维数组`);
    err.status = 400;
    throw err;
  }
  if (!query || typeof query !== 'string') {
    const err = new Error('query 不能为空');
    err.status = 400;
    throw err;
  }
  const out = await search(req.params.name, { embedding, query, topK, mode, rrfWindow, rrfConstant, where });
  res.json(out);
}));

app.get('/api/collections/:name/docs', asyncRoute(async (req, res) => {
  const limit = Math.min(Number(req.query.limit) || 20, 200);
  const offset = Number(req.query.offset) || 0;
  res.json({ docs: await browse(req.params.name, { limit, offset }) });
}));

if (fs.existsSync(webDist)) {
  app.use(express.static(webDist, { maxAge: '1h', index: 'index.html' }));
  app.get(/^(?!\/api\/).*/, (_req, res) => res.sendFile(path.join(webDist, 'index.html')));
} else {
  app.get('/', (_req, res) => res.send('前端尚未构建：先在 stepfun/ 下执行 npm run build'));
}

app.listen(config.port, () => {
  console.log(`[server] listening on http://127.0.0.1:${config.port}`);
  console.log(`[server] seekdb ${config.seekdbPath ? `embedded:${config.seekdbPath}` : `${config.seekdb.host}:${config.seekdb.port}`}`);
});
