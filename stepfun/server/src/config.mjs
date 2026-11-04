import os from 'node:os';
import path from 'node:path';

function num(v, fallback) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export const config = {
  port: num(process.env.PORT, 8787),
  // seekdb 连接：默认连本地 server 模式（docker compose 或 scripts/start-seekdb.* 启动）
  seekdb: {
    host: process.env.SEEKDB_HOST || '127.0.0.1',
    port: num(process.env.SEEKDB_PORT, 2881),
    user: process.env.SEEKDB_USER || 'root',
    password: process.env.SEEKDB_PASSWORD || '',
    database: process.env.SEEKDB_DATABASE || 'test',
    queryTimeout: num(process.env.SEEKDB_QUERY_TIMEOUT, 60000),
  },
  // 嵌入式模式（可选）：设置 SEEKDB_PATH 后走进程内 seekdb，无需单独 server
  seekdbPath: process.env.SEEKDB_PATH || '',
  defaultCollection: process.env.DEFAULT_COLLECTION || 'edge_kb',
  embeddingDim: num(process.env.EMBEDDING_DIM, 512),
  dataDir: process.env.DATA_DIR || path.join(os.tmpdir(), 'seekdb-edge-kb'),
};
