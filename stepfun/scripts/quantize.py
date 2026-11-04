#!/usr/bin/env python
"""
bge-small-zh-v1.5 量化脚本（INT8 动态量化 + FP16 转换）

输入：web/public/models/all-MiniLM-L6-v2/onnx/model.onnx            (FP32 基线)
输出：web/public/models/all-MiniLM-L6-v2/onnx/model_int8.onnx       (自研 INT8)
      web/public/models/all-MiniLM-L6-v2/onnx/model_fp16_ours.onnx  (自研 FP16)

产物命名与 Transformers.js 的 dtype 约定对齐：
  dtype=fp32 -> model.onnx
  dtype=int8 -> model_int8.onnx
  dtype=fp16 -> model_fp16.onnx

用法：
  python scripts/quantize.py [--root web/public/models/all-MiniLM-L6-v2]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnxruntime.quantization import QuantType, quantize_dynamic

DEFAULT_ROOT = Path("web/public/models/bge-small-zh-v1.5")


def human(n: int) -> str:
    return f"{n / 1048576:.2f} MiB"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def describe(model_path: Path) -> dict:
    m = onnx.load(str(model_path), load_external_data=False)
    init_bytes = sum(
        int(np.prod(t.dims)) * np.dtype(onnx.helper.tensor_dtype_to_np_dtype(t.data_type)).itemsize
        for t in m.graph.initializer
        if t.data_type != onnx.TensorProto.STRING
    )
    ops = sorted({n.op_type for n in m.graph.node})
    return {
        "path": str(model_path),
        "bytes": model_path.stat().st_size,
        "initializer_bytes": init_bytes,
        "nodes": len(m.graph.node),
        "op_types": ops,
        "ir_version": m.ir_version,
        "opset": [(o.domain or "ai.onnx", o.version) for o in m.opset_import],
    }


def run(model_path: Path, feeds: dict) -> np.ndarray:
    sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    names = [o.name for o in sess.get_outputs()]
    out = sess.run(names, feeds)
    # 取 last_hidden_state 再按 attention mask 做 mean pooling，与浏览器端保持一致
    hidden = out[0]
    mask = feeds["attention_mask"].astype(np.float32)[..., None]
    pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
    return pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    args = ap.parse_args()
    root = Path(args.root)
    onnx_dir = root / "onnx"
    fp32 = onnx_dir / "model.onnx"
    int8 = onnx_dir / "model_int8.onnx"
    fp16 = onnx_dir / "model_fp16.onnx"
    fp16_ours = onnx_dir / "model_fp16_ours.onnx"

    if not fp32.exists():
        raise SystemExit(f"缺少 FP32 基线模型：{fp32}（先运行 node scripts/fetch-model.mjs）")

    report: dict = {"model": "BAAI/bge-small-zh-v1.5", "dimension": 512}
    base = describe(fp32)
    report["fp32"] = base
    print(f"[fp32] {human(base['bytes'])}  nodes={base['nodes']}  ops={len(base['op_types'])}")

    # ---------- INT8 动态量化 ----------
    t0 = time.time()
    quantize_dynamic(
        model_input=str(fp32),
        model_output=str(int8),
        weight_type=QuantType.QUInt8,
        per_channel=False,
        reduce_range=False,
        extra_options={"MatMulConstBOnly": True},
    )
    q_secs = time.time() - t0
    q = describe(int8)
    report["int8"] = {**q, "quantize_seconds": round(q_secs, 2),
                      "method": "onnxruntime.quantization.quantize_dynamic(weight_type=QUInt8, MatMulConstBOnly)",
                      "compression_ratio": round(base["bytes"] / q["bytes"], 3)}
    print(f"[int8] {human(q['bytes'])}  ratio={base['bytes'] / q['bytes']:.2f}x  ({q_secs:.1f}s)")

    # ---------- FP16 转换 ----------
    # onnxconverter-common 在部分 opset 下会生成 Cast 类型不一致的图，因此产物必须经过 ORT 载入校验，
    # 不通过则回退到上游官方导出的 model_fp16.onnx（同一权重的 fp16 导出）。
    def _try_fp16() -> dict | None:
        from onnxruntime.transformers.float16 import convert_float_to_float16

        m = onnx.load(str(fp32))
        m16 = convert_float_to_float16(m, keep_io_types=True)
        onnx.save(m16, str(fp16_ours))
        ort.InferenceSession(str(fp16_ours), providers=["CPUExecutionProvider"])  # 载入校验
        return describe(fp16_ours)

    fp16_info = None
    try:
        d = _try_fp16()
        shutil.copyfile(fp16_ours, fp16)
        fp16_info = {**d, "method": "onnxruntime.transformers.float16.convert_float_to_float16(keep_io_types=True)",
                     "compression_ratio": round(base["bytes"] / d["bytes"], 3), "validated": True}
        print(f"[fp16] {human(d['bytes'])}  ratio={base['bytes'] / d['bytes']:.2f}x  (self-converted)")
    except Exception as exc:  # noqa: BLE001
        print(f"[fp16] 自转换不可用，沿用上游 model_fp16.onnx：{exc}")
        if fp16.exists():
            d = describe(fp16)
            fp16_info = {**d, "method": "upstream FP16 export (Xenova/all-MiniLM-L6-v2)",
                         "compression_ratio": round(base["bytes"] / d["bytes"], 3), "validated": False}
    if fp16_info:
        report["fp16"] = fp16_info

    # ---------- 数值校验（同输入对比 FP32 输出） ----------
    rng = np.random.default_rng(20261104)
    seq = 16
    vocab_size = 20000
    vocab_file = root / "vocab.txt"
    if vocab_file.exists():
        vocab_size = max(1000, sum(1 for _ in vocab_file.open("rb")) - 1)
    feeds = {
        "input_ids": rng.integers(low=1000, high=vocab_size, size=(4, seq), dtype=np.int64),
        "attention_mask": np.ones((4, seq), dtype=np.int64),
        "token_type_ids": np.zeros((4, seq), dtype=np.int64),
    }
    try:
        ref = run(fp32, feeds)
    except Exception as exc:  # noqa: BLE001
        print(f"数值校验跳过（fp32 推理失败：{exc}）")
        ref = None

    if ref is not None:
        checks = {}
        for name, path in (("int8", int8), ("fp16", fp16)):
            if not path.exists():
                continue
            try:
                got = run(path, feeds)
                cos = float(np.mean(np.sum(ref * got, axis=1)))
                checks[name] = {
                    "cosine_vs_fp32": round(cos, 6),
                    "max_abs_diff": round(float(np.max(np.abs(ref - got))), 6),
                }
                report.setdefault(name, {}).update(checks[name])
                print(f"[check] {name}: cosine={cos:.6f}  max|Δ|={checks[name]['max_abs_diff']}")
            except Exception as exc:  # noqa: BLE001
                print(f"[check] {name} 失败：{exc}")
        report["numeric_check"] = {
            "input": "random token ids 4x16, attention_mask all ones, mean pooling + L2",
            **checks,
        }

    report["transformers_js_files"] = {
        "fp32": "onnx/model.onnx",
        "int8": "onnx/model_int8.onnx",
        "fp16": "onnx/model_fp16.onnx",
    }
    report["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    out = root / "quantization.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n报告写入 {out}")
    for name in ("fp32", "int8", "fp16"):
        if name in report:
            print(f"  {name:5s} {human(report[name]['bytes']):>10s}  ratio={report[name].get('compression_ratio', 1.0)}")


if __name__ == "__main__":
    os.environ.setdefault("ORT_DISABLE_ALL_OPTIMIZATIONS", "0")
    main()
