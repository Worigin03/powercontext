/**
 * 主线程侧的 embedding 封装：把 Worker 的消息协议包装成 Promise。
 * 同时负责 WebGPU 探测与自动降级决策（界面需要显示当前后端与降级原因）。
 */
export type Dtype = 'fp32' | 'fp16' | 'int8' | 'q8' | 'uint8';
export type DeviceKind = 'webgpu' | 'wasm';

export type BackendDecision = {
  device: DeviceKind;
  dtype: Dtype;
  requested: DeviceKind;
  fallback: boolean;
  reason: string;
  adapter?: string;
};

export type EmbedResult = { vectors: number[][]; totalMs: number; batchMs: number[] };

type Pending = {
  resolve: (v: any) => void;
  reject: (e: Error) => void;
  onBatch?: (done: number, total: number, batchMs: number) => void;
};

/** 探测 WebGPU：返回适配器信息，失败则说明原因 */
export async function detectWebGPU(): Promise<{ ok: boolean; adapter?: string; reason: string }> {
  const nav = navigator as any;
  if (!nav.gpu) return { ok: false, reason: '浏览器未提供 navigator.gpu' };
  try {
    const adapter = await nav.gpu.requestAdapter({ powerPreference: 'high-performance' });
    if (!adapter) return { ok: false, reason: 'requestAdapter 返回空（无可用 GPU 适配器）' };
    let info: any = null;
    try {
      info = adapter.info || (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : null);
    } catch {
      info = null;
    }
    const label = info
      ? [info.vendor, info.architecture, info.description].filter(Boolean).join(' / ')
      : adapter.constructor?.name || 'GPUAdapter';
    return { ok: true, adapter: label, reason: 'WebGPU 可用' };
  } catch (err: any) {
    return { ok: false, reason: `requestAdapter 抛错：${err?.message || err}` };
  }
}

export class Embedder {
  private worker: Worker | null = null;
  private seq = 1;
  private pending = new Map<number, Pending>();
  private progressCb: ((p: any) => void) | null = null;
  backend: { device: DeviceKind; dtype: Dtype; wasmThreads?: number | null } | null = null;

  private ensureWorker() {
    if (this.worker) return this.worker;
    this.worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' });
    this.worker.onerror = (e) => {
      const err = new Error(`Worker 失败：${e.message || '未知错误'}`);
      for (const [, job] of this.pending) job.reject(err);
      this.pending.clear();
    };
    this.worker.onmessageerror = () => console.error('[worker] messageerror');
    this.worker.onerror = (e) => {
      const err = new Error(`Worker 失败：${e.message || '未知错误'}`);
      for (const [, job] of this.pending) job.reject(err);
      this.pending.clear();
    };
    this.worker.onmessageerror = () => console.error('[worker] messageerror');
    this.worker.onmessage = (e: MessageEvent) => {
      const msg = e.data;
      if (msg.type === 'progress') {
        this.progressCb?.(msg.payload);
        return;
      }
      if (msg.type === 'batch') {
        this.pending.get(msg.jobId)?.onBatch?.(msg.done, msg.total, msg.batchMs);
        return;
      }
      if (msg.type === 'pong') {
        this.pending.get(msg.jobId)?.resolve(true);
        this.pending.delete(msg.jobId);
        return;
      }
      const job = this.pending.get(msg.jobId);
      if (!job) {
        if (msg.type === 'ready') (this as any).readyResolve?.(msg);
        if (msg.type === 'error') console.error('[worker]', msg.message, msg.stack);
        return;
      }
      this.pending.delete(msg.jobId);
      if (msg.type === 'error') job.reject(new Error(msg.message));
      else job.resolve(msg);
    };
    return this.worker;
  }

  async init(opts: {
    model?: string;
    device: DeviceKind;
    dtype: Dtype;
    threads?: number;
    pooling?: 'mean' | 'cls';
    onProgress?: (p: any) => void;
  }): Promise<{ loadMs: number; backend: any }> {
    const w = this.ensureWorker();
    this.progressCb = opts.onProgress || null;
    const jobId = this.seq++;
    const ready = new Promise<any>((resolve, reject) => {
      this.pending.set(jobId, { resolve, reject });
    });
    w.postMessage({
      type: 'init',
      jobId,
      payload: {
        model: opts.model || 'bge-small-zh-v1.5',
        pooling: opts.pooling || 'cls',
        device: opts.device,
        dtype: opts.dtype,
        threads: opts.threads ?? Math.min(4, navigator.hardwareConcurrency || 4),
      },
    });
    this.backend = { device: opts.device, dtype: opts.dtype };
    return ready;
  }

  async embed(
    texts: string[],
    batchSize = 16,
    onBatch?: (done: number, total: number, batchMs: number) => void,
    role: 'query' | 'document' = 'document',
    bucket = true,
  ): Promise<EmbedResult> {
    const w = this.ensureWorker();
    const jobId = this.seq++;
    const p = new Promise<any>((resolve, reject) => {
      this.pending.set(jobId, { resolve, reject, onBatch });
    });
    w.postMessage({ type: 'embed', jobId, texts, batchSize, role, bucket });
    const msg = await p;
    const flat = new Float32Array(msg.buffer);
    const dim = msg.dim as number;
    const vectors: number[][] = [];
    for (let i = 0; i < msg.count; i++) {
      vectors.push(Array.from(flat.subarray(i * dim, (i + 1) * dim)));
    }
    return { vectors, totalMs: msg.totalMs, batchMs: msg.batchMs };
  }

  dispose() {
    this.worker?.terminate();
    this.worker = null;
    this.pending.clear();
  }
}
