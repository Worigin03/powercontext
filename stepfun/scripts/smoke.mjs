import { chromium } from 'playwright';

const url = process.argv[2] || 'http://127.0.0.1:8787/';
const browser = await chromium.launch({
  channel: 'chrome',
  headless: false,
  args: ['--enable-unsafe-webgpu'],
});
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
page.on('console', (m) => console.log('[console]', m.type(), m.text().slice(0, 240)));
page.on('pageerror', (e) => console.log('[pageerror]', e.message));
page.on('requestfailed', (r) => console.log('[reqfail]', r.url().slice(0, 120), r.failure()?.errorText));

const t0 = Date.now();
await page.goto(url, { waitUntil: 'domcontentloaded' });

let ready = false;
for (let i = 0; i < 60; i++) {
  const txt = await page.locator('#badges').innerText().catch(() => '');
  if (txt.includes('WebGPU') || txt.includes('WASM') || txt.includes('失败')) {
    ready = true;
    console.log(`[ready] ${((Date.now() - t0) / 1000).toFixed(1)}s ${txt.replace(/\n/g, ' | ')}`);
    break;
  }
  if (i % 5 === 0) console.log(`[wait ${i}] ${txt.slice(0, 80)}`);
  await page.waitForTimeout(2000);
}
if (!ready) console.log('[timeout] badges:', await page.locator('#badges').innerText());

console.log('runtime:', (await page.locator('#runtime-info').innerText()).replace(/\n/g, ' | '));
console.log('isolated:', await page.evaluate(() => crossOriginIsolated));

if (ready) {
  await page.fill('#paste', 'seekdb 是一款支持向量检索的嵌入式数据库。混合检索会把向量召回与全文召回通过 RRF 融合。\n\n端侧推理指模型在用户设备的浏览器中执行，不依赖云端 API。WebGPU 可用时优先使用 GPU，否则回退到 WASM。');
  await page.click('#ingest');
  for (let i = 0; i < 90; i++) {
    const stage = await page.locator('#ingest-stage').innerText().catch(() => '');
    if (stage.includes('完成') || stage.includes('失败')) break;
    await page.waitForTimeout(1000);
  }
  console.log('ingest:', await page.locator('#ingest-stage').innerText(), '|', await page.locator('#ingest-meta').innerText());

  await page.fill('#query', '混合检索怎么融合两路结果');
  await page.click('#search');
  await page.waitForTimeout(3000);
  console.log('timings:', (await page.locator('#timings').innerText()).replace(/\n/g, ' '));
  console.log('hits:', (await page.locator('#results').innerText()).slice(0, 300).replace(/\n/g, ' '));
  await page.screenshot({ path: 'bench/smoke.png' });
}
await browser.close();
