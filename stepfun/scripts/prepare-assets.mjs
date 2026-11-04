#!/usr/bin/env node
/**
 * 把 ONNX Runtime Web 的 wasm 运行时复制到 web/public/ort，
 * 使应用在完全离线的环境下也能跑 WASM / WebGPU 后端（不依赖 CDN）。
 *
 * 用法：node scripts/prepare-assets.mjs
 */
import fs from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve('.');
const src = path.join(root, 'web/node_modules/onnxruntime-web/dist');
const dst = path.join(root, 'web/public/ort');

// 不同 ORT 版本会按需请求 asyncify / jsep / jspi 等变体，全部随应用分发
const entries = await fs.readdir(src);
const FILES = entries.filter((f) => /^ort-wasm-simd-threaded.*\.(wasm|mjs)$/.test(f)).sort();

await fs.mkdir(dst, { recursive: true });
let bytes = 0;
for (const f of FILES) {
  const from = path.join(src, f);
  const to = path.join(dst, f);
  try {
    await fs.copyFile(from, to);
    const st = await fs.stat(to);
    bytes += st.size;
    console.log(`  ${f.padEnd(38)} ${(st.size / 1048576).toFixed(2)} MiB`);
  } catch (err) {
    console.warn(`  跳过 ${f}：${err.message}`);
  }
}
console.log(`ORT 运行时就绪：${dst}（合计 ${(bytes / 1048576).toFixed(2)} MiB）`);

const modelDir = path.join(root, 'web/public/models/all-MiniLM-L6-v2');
const probe = ['config.json', 'tokenizer.json', 'onnx/model.onnx', 'onnx/model_int8.onnx'];
const missing = [];
for (const f of probe) {
  try {
    await fs.access(path.join(modelDir, f));
  } catch {
    missing.push(f);
  }
}
if (missing.length) {
  console.warn(`\n模型文件缺失：${missing.join(', ')}\n请先执行：node scripts/fetch-model.mjs && python scripts/quantize.py`);
} else {
  console.log('模型文件齐全');
}
