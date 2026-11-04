/// <reference lib="webworker" />
/**
 * 端侧 embedding Worker。
 * 所有推理（文档向量化 + 查询向量化）都在这里完成，主线程只负责 UI。
 */
import { env, pipeline } from '@huggingface/transformers';

// 只从本地 /models 加载，禁止任何远程模型请求（保证推理完全在设备上）
env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = '/models/';
env.useBrowserCache = true;
// ORT Web 的 wasm 运行时随应用一起分发，避免运行时依赖 CDN
env.backends.onnx.wasm.wasmPaths = '/ort/';

type InitPayload = {
  model: string;
  pooling?: 'mean' | 'cls';
  device: 'webgpu' | 'wasm';
  dtype: 'fp32' | 'fp16' | 'int8' | 'q8' | 'uint8';
  threads?: number;
};

let extractor: any = null;
let backendInfo: Record<string, unknown> = {};

const post = (msg: unknown, transfer: Transferable[] = []) =>
  (self as unknown as Worker).postMessage(msg, transfer);

async function init(payload: InitPayload, jobId: number) {
  console.log('[worker] init', JSON.stringify(payload), 'wasmPaths=', env.backends.onnx.wasm.wasmPaths);
  console.log('[worker] init', JSON.stringify(payload), 'wasmPaths=', env.backends.onnx.wasm.wasmPaths);
  if (payload.device === 'wasm' && payload.threads) {
    env.backends.onnx.wasm.numThreads = payload.threads;
  }
  const t0 = performance.now();
  extractor = await pipeline('feature-extraction', payload.model, {
    device: payload.device,
    dtype: payload.dtype,
    progress_callback: (p: any) => post({ type: 'progress', payload: p }),
  });
  const loadMs = performance.now() - t0;
  console.log('[worker] pipeline ready in', loadMs.toFixed(0), 'ms');
  console.log('[worker] pipeline ready in', loadMs.toFixed(0), 'ms');

  pooling = payload.pooling || 'cls';
  backendInfo = {
    device: payload.device,
    dtype: payload.dtype,
    wasmThreads: payload.device === 'wasm' ? env.backends.onnx.wasm.numThreads : null,
    simd: payload.device === 'wasm' ? (env.backends.onnx.wasm as any)?.simd : null,
  };
  post({ type: 'ready', jobId, loadMs, backend: backendInfo });
}

let pooling: 'mean' | 'cls' = 'cls';
// BGE 系列要求查询侧加指令前缀（文档侧不加），否则短查询的召回会明显变差
const QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：';

async function embed(
  texts: string[],
  batchSize: number,
  jobId: number,
  role: 'query' | 'document' = 'document',
  bucket = true,
) {
  if (!extractor) throw new Error('模型尚未就绪');
  const inputs = role === 'query' ? texts.map((t) => QUERY_PREFIX + t) : texts;

  // 长度分桶：同一批里文本长度接近，可显著减少 padding 带来的无效计算
  const order = inputs.map((t, i) => i);
  if (bucket && inputs.length > batchSize) order.sort((a, b) => inputs[a].length - inputs[b].length);

  const vectors: number[][] = new Array(inputs.length);
  const batchMs: number[] = [];
  const started = performance.now();

  for (let i = 0; i < order.length; i += batchSize) {
    const idx = order.slice(i, i + batchSize);
    const batch = idx.map((k) => inputs[k]);
    const t = performance.now();
    const out = await extractor(batch, { pooling, normalize: true });
    const list: number[][] = out.tolist();
    batchMs.push(performance.now() - t);
    list.forEach((vec, j) => (vectors[idx[j]] = vec));
    post({
      type: 'batch',
      jobId,
      done: Math.min(i + batch.length, inputs.length),
      total: inputs.length,
      batchMs: batchMs[batchMs.length - 1],
    });
  }

  const totalMs = performance.now() - started;
  const flat = new Float32Array(vectors.length * vectors[0].length);
  vectors.forEach((v, i) => flat.set(v, i * v.length));
  post({ type: 'embedded', jobId, dim: vectors[0].length, count: vectors.length, totalMs, batchMs, buffer: flat.buffer }, [
    flat.buffer,
  ]);
}

self.onmessage = async (e: MessageEvent) => {
  const msg = e.data;
  try {
    if (msg.type === 'init') await init(msg.payload, msg.jobId);
    else if (msg.type === 'embed')
      await embed(msg.texts, msg.batchSize || 16, msg.jobId, msg.role || 'document', msg.bucket !== false);
    else if (msg.type === 'ping') post({ type: 'pong', id: msg.id });
  } catch (err: any) {
    post({ type: 'error', jobId: msg.jobId, message: err?.message || String(err), stack: err?.stack });
  }
};
