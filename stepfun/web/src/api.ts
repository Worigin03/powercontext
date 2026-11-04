/** seekdb 后端 API 客户端：浏览器只上传向量与文本，检索由 seekdb 完成 */
export type Backend = { device: 'webgpu' | 'wasm'; adapter?: string; fallback: boolean; reason?: string };

export type SearchHit = {
  id: string;
  text: string;
  metadata: Record<string, any>;
  score: number | null;
};

export type SearchResponse = {
  strategy: string;
  tookMs: number;
  hits: SearchHit[];
};

export type CollectionState = {
  name: string;
  exists: boolean;
  count: number;
  dimension: number | null;
  distance?: string;
  pendingRefresh: number;
  lastRefreshMs: number | null;
  lastRefreshAt: string | null;
};

const base = '';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(base + path, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers || {}) },
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);
  return body as T;
}

export const api = {
  health: () => request<{ ok: boolean; seekdb: any; defaultCollection: string; embeddingDim: number }>('/api/health'),

  listCollections: () => request<{ collections: any[] }>('/api/collections'),

  state: (name: string) => request<CollectionState>(`/api/collections/${encodeURIComponent(name)}/state`),

  createCollection: (name: string, dimension = 384) =>
    request<CollectionState & { created: boolean }>('/api/collections', {
      method: 'POST',
      body: JSON.stringify({ name, dimension }),
    }),

  deleteCollection: (name: string) =>
    request<{ ok: boolean }>(`/api/collections/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  upsert: (
    name: string,
    docs: { id: string; text: string; embedding: number[]; metadata: Record<string, any> }[],
    refresh = true,
  ) =>
    request<{ written: number; writeMs: number; refreshMs: number | null; state: CollectionState }>(
      `/api/collections/${encodeURIComponent(name)}/upsert`,
      { method: 'POST', body: JSON.stringify({ docs, refresh }) },
    ),

  refresh: (name: string) =>
    request<{ refreshMs: number; at: string; state: CollectionState }>(
      `/api/collections/${encodeURIComponent(name)}/refresh`,
      { method: 'POST' },
    ),

  search: (
    name: string,
    body: { query: string; embedding: number[]; topK: number; mode: string; rrfWindow?: number },
  ) =>
    request<SearchResponse>(`/api/collections/${encodeURIComponent(name)}/search`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  docs: (name: string, limit = 20, offset = 0) =>
    request<{ docs: any[] }>(`/api/collections/${encodeURIComponent(name)}/docs?limit=${limit}&offset=${offset}`),
};
