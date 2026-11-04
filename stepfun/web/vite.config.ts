import { defineConfig } from 'vite';

// COOP/COEP：开启 crossOriginIsolated，WASM 才能使用多线程（SharedArrayBuffer）
const isolationHeaders = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'require-corp',
};

export default defineConfig({
  server: {
    port: 5173,
    headers: isolationHeaders,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8787', changeOrigin: true },
    },
  },
  preview: { headers: isolationHeaders, port: 4173 },
  build: {
    target: 'es2022',
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 4096,
  },
  worker: { format: 'es' },
  optimizeDeps: {
    // onnxruntime-web 自带 wasm 加载逻辑，交给 Vite 预构建会破坏 wasmPaths
    exclude: ['@huggingface/transformers', 'onnxruntime-web'],
  },
});
