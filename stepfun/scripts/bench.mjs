#!/usr/bin/env node
/**
 * 基准运行器：用 Playwright 驱动真实 Chrome，在浏览器内跑 web/src/bench.ts。
 *
 * 每个配置使用独立的浏览器上下文（清空 HTTP 缓存），因此模型加载时间是「冷启动」口径。
 *
 * 用法：
 *   node scripts/bench.mjs                # 全部配置
 *   node scripts/bench.mjs --quick        # 只跑 2 个配置、每条查询 1 轮（自检）
 *   node scripts/bench.mjs --only=fp32:webgpu
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const args = new Map(
  process.argv.slice(2).map((a) => {
    const [k, v] = a.replace(/^--/, '').split('=');
    return [k, v ?? 'true'];
  }),
);

const BASE = args.get('base') || 'http://127.0.0.1:8787';
const REPEAT = Number(args.get('repeat') || 3);
const BATCH = Number(args.get('batch') || 16);
const TOPK = Number(args.get('topk') || 10);
const OUT_DIR = path.resolve('bench');

const ALL_CONFIGS = [
  { label: 'fp32 + WebGPU', device: 'webgpu', dtype: 'fp32' },
  { label: 'fp16 + WebGPU', device: 'webgpu', dtype: 'fp16' },
  { label: 'int8 + WebGPU', device: 'webgpu', dtype: 'int8' },
  { label: 'int8 + WASM', device: 'wasm', dtype: 'int8' },
  { label: 'fp32 + WASM', device: 'wasm', dtype: 'fp32' },
];

const only = args.get('only');
let configs = ALL_CONFIGS;
if (only) configs = ALL_CONFIGS.filter((c) => only.split(',').includes(`${c.dtype}:${c.device}`));
if (args.get('quick')) configs = ALL_CONFIGS.filter((c) => ['fp32 + WebGPU', 'int8 + WASM'].includes(c.label));

const repeat = args.get('quick') ? 1 : REPEAT;

const browser = await chromium.launch({
  channel: 'chrome',
  headless: false,
  args: ['--enable-unsafe-webgpu', '--window-size=1200,820', '--disable-background-timer-throttling'],
});

const results = [];
for (const cfg of configs) {
  process.stdout.write(`\n▶ ${cfg.label} ...\n`);
  const context = await browser.newContext({ viewport: { width: 1200, height: 820 } });
  const page = await context.newPage();
  const t0 = Date.now();
  let errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 200));
  });

  try {
    await page.goto(`${BASE}/?bench=1&device=${cfg.device}&dtype=${cfg.dtype}`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.__benchReady === true, { timeout: 180000 });

    const out = await page.evaluate(
      async ({ device, dtype, repeat, batch, topk, mode, chunkSize, chunkOverlap }) => {
        return await window.__bench.run({ device, dtype, repeat, batchSize: batch, topK: topk, mode, chunkSize, chunkOverlap });
      },
      {
        device: cfg.device,
        dtype: cfg.dtype,
        repeat,
        batch: BATCH,
        topk: TOPK,
        mode: args.get('mode') || 'hybrid',
        chunkSize: Number(args.get('chunk') || 600),
        chunkOverlap: Number(args.get('overlap') || 100),
      },
    );

    const record = { label: cfg.label, elapsedSec: +((Date.now() - t0) / 1000).toFixed(1), ...out };
    results.push(record);
    console.log(
      `  加载 ${record.config.loadMs} ms · 向量化 ${record.ingest.embedMs} ms (${record.ingest.chunksPerSecond} 块/s) · ` +
        `写入 ${record.ingest.writeMs} ms · 刷新 ${record.ingest.refreshMs} ms · 检索 P50 ${record.search.dbMs.median} ms · ` +
        `e2e P50 ${record.search.e2eMs.median} ms · Recall@10 ${record.quality.recall10} · nDCG@10 ${record.quality.ndcg10}`,
    );
  } catch (err) {
    console.error(`  ✗ ${cfg.label} 失败：${err.message}`);
    if (errors.length) console.error('   ', errors.slice(0, 3).join(' | '));
    results.push({ label: cfg.label, failed: true, error: err.message, consoleErrors: errors.slice(0, 5) });
  } finally {
    await context.close();
  }
}

await browser.close();

await fs.mkdir(OUT_DIR, { recursive: true });
const outFile = path.resolve(args.get('out') || path.join(OUT_DIR, 'results.json'));
await fs.writeFile(
  outFile,
  JSON.stringify(
    { generatedAt: new Date().toISOString(), base: BASE, repeat, batch: BATCH, topK: TOPK, chunk: args.get('chunk') || 600, tag: args.get('tag') || null, results },
    null,
    2,
  ),
);
console.log(`\n结果写入 ${outFile}`);
