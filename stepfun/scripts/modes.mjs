#!/usr/bin/env node
/**
 * 索引策略对照：在同一页面、同一份已建好的集合上，依次用 hybrid / vector / fulltext
 * 三种检索模式跑同一批查询，隔离出「混合检索」相对于单路召回的收益。
 *
 * 第一次 hybrid 运行负责导入并建索引，后两次传 skipIngest=true 复用同一集合，
 * 因此三次结果只差检索策略，不掺入向量化或写库的波动。
 *
 * 用法：node scripts/modes.mjs [--device=webgpu] [--dtype=fp16] [--repeat=2]
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
const DEVICE = args.get('device') || 'webgpu';
const DTYPE = args.get('dtype') || 'fp16';
const REPEAT = Number(args.get('repeat') || 2);
const BATCH = Number(args.get('batch') || 16);
const TOPK = Number(args.get('topk') || 10);
const MODES = ['hybrid', 'vector', 'fulltext'];

const browser = await chromium.launch({
  channel: 'chrome',
  headless: false,
  args: ['--enable-unsafe-webgpu', '--window-size=1200,820', '--disable-background-timer-throttling'],
});
const context = await browser.newContext({ viewport: { width: 1200, height: 820 } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text().slice(0, 200));
});

console.log(`▶ ${DTYPE} + ${DEVICE}：hybrid / vector / fulltext`);
await page.goto(`${BASE}/?bench=1&device=${DEVICE}&dtype=${DTYPE}`, { waitUntil: 'domcontentloaded' });
await page.waitForFunction(() => window.__benchReady === true, { timeout: 180000 });

const out = [];
for (const [i, mode] of MODES.entries()) {
  const rec = await page.evaluate(
    async ({ device, dtype, repeat, batch, topk, mode, skipIngest }) => {
      return await window.__bench.run({ device, dtype, repeat, batchSize: batch, topK: topk, mode, skipIngest });
    },
    { device: DEVICE, dtype: DTYPE, repeat: REPEAT, batch: BATCH, topk: TOPK, mode, skipIngest: i > 0 },
  );
  out.push({ mode, ...rec });
  console.log(
    `  ${mode.padEnd(8)} Recall@10 ${(rec.quality.recall10 * 100).toFixed(1)}% · nDCG@10 ${(rec.quality.ndcg10 * 100).toFixed(
      1,
    )}% · MRR@10 ${(rec.quality.mrr10 * 100).toFixed(1)}% · 检索 P50 ${rec.search.dbMs.median} ms`,
  );
}

await browser.close();

const file = path.resolve(args.get('out') || 'bench/modes.json');
await fs.mkdir(path.dirname(file), { recursive: true });
await fs.writeFile(
  file,
  JSON.stringify(
    {
      generatedAt: new Date().toISOString(),
      device: DEVICE,
      dtype: DTYPE,
      repeat: REPEAT,
      topK: TOPK,
      chunks: out[0]?.scale?.chunks ?? null,
      queries: out[0]?.scale?.queries ?? null,
      evaluated: out[0]?.quality?.evaluatedQueries ?? null,
      results: out,
      consoleErrors: errors.slice(0, 5),
    },
    null,
    2,
  ),
);
console.log(`\n结果写入 ${file}`);
