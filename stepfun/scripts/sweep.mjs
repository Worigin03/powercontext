#!/usr/bin/env node
/**
 * 单项优化对比（批大小 × 长度分桶 × 后端），在同一页面内连续测量。
 * 用法：node scripts/sweep.mjs [--sample=96]
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const args = new Map(process.argv.slice(2).map((a) => a.replace(/^--/, '').split('=')));
const BASE = args.get('base') || 'http://127.0.0.1:8787';
const SAMPLE = Number(args.get('sample') || 96);

const CONFIGS = [
  { device: 'webgpu', dtype: 'fp32' },
  { device: 'webgpu', dtype: 'fp16' },
  { device: 'wasm', dtype: 'int8' },
];

const browser = await chromium.launch({
  channel: 'chrome',
  headless: false,
  args: ['--enable-unsafe-webgpu', '--window-size=1100,760'],
});

const out = [];
for (const cfg of CONFIGS) {
  const context = await browser.newContext({ viewport: { width: 1100, height: 760 } });
  const page = await context.newPage();
  try {
    await page.goto(`${BASE}/?bench=1&device=${cfg.device}&dtype=${cfg.dtype}`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.__benchReady === true, { timeout: 180000 });
    const res = await page.evaluate((c) => window.__bench.sweep(c), { ...cfg, sample: SAMPLE });
    out.push(res);
    const best = [...res.rows].sort((a, b) => b.chunksPerSecond - a.chunksPerSecond)[0];
    console.log(`▶ ${cfg.dtype}+${cfg.device}: 最佳 batch=${best.batchSize} bucket=${best.bucket} → ${best.chunksPerSecond} 块/s`);
    for (const r of res.rows) {
      console.log(`   batch=${String(r.batchSize).padStart(2)} bucket=${r.bucket ? 'on ' : 'off'} ${String(r.chunksPerSecond).padStart(6)} 块/s (${r.ms} ms)`);
    }
  } catch (err) {
    console.error(`✗ ${cfg.dtype}+${cfg.device}: ${err.message}`);
  } finally {
    await context.close();
  }
}

await browser.close();
await fs.mkdir('bench', { recursive: true });
await fs.writeFile('bench/sweep.json', JSON.stringify({ generatedAt: new Date().toISOString(), sample: SAMPLE, results: out }, null, 2));
console.log('写入 bench/sweep.json');
