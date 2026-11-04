"""Seed the seekdb corpus with real arXiv papers and a zh->en glossary."""
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.ingest import ingest_arxiv

PAPERS = [
    "1602.05629",  # federated learning
    "1706.03762",  # attention
    "1810.04805",  # BERT
    "2005.11401",  # RAG
    "2106.09685",  # LoRA
    "2201.11903",  # chain-of-thought
    "2210.03629",  # ReAct
    "2304.03442",  # generative agents
    "1701.01090",  # mobile edge computing survey
    "1905.10083",  # edge intelligence
    "2108.04973",  # 6G IoT survey
    "2311.12275",  # on-device LLM personalization
    "2101.12631",  # ANN survey
]

GLOSSARY = [
    ("联邦学习", "federated learning", "分布式训练"),
    ("边缘计算", "edge computing", "就近计算"),
    ("移动边缘计算", "mobile edge computing MEC", "移动网络"),
    ("边缘智能", "edge intelligence", "端侧 AI"),
    ("物联网", "internet of things IoT", "感知网络"),
    ("大语言模型", "large language model LLM", "基础模型"),
    ("语言模型", "language model", "LM"),
    ("检索增强生成", "retrieval augmented generation RAG", "RAG"),
    ("检索增强", "retrieval-augmented retrieval augmented", "检索"),
    ("注意力机制", "attention mechanism attention", "Transformer"),
    ("微调", "fine-tuning fine-tune", "适配"),
    ("低秩适配", "low-rank adaptation LoRA", "参数高效微调"),
    ("思维链", "chain-of-thought reasoning", "推理"),
    ("提示工程", "prompt engineering prompting", "提示词"),
    ("智能体", "agent agents", "Agent"),
    ("多智能体", "multi-agent", "协作"),
    ("模型压缩", "model compression", "轻量化"),
    ("量化", "quantization quantized", "低精度"),
    ("知识蒸馏", "knowledge distillation", "蒸馏"),
    ("剪枝", "pruning", "稀疏化"),
    ("能耗", "energy consumption power", "能效"),
    ("延迟", "latency delay", "时延"),
    ("带宽", "bandwidth", "网络"),
    ("隐私", "privacy", "数据保护"),
    ("安全", "security", "攻击面"),
    ("对抗攻击", "adversarial attack", "鲁棒性"),
    ("向量检索", "vector search", "相似度检索"),
    ("近似最近邻", "approximate nearest neighbor ANN", "向量索引"),
    ("嵌入", "embedding embeddings", "表示学习"),
    ("语义检索", "semantic search", "语义匹配"),
    ("数据集", "dataset datasets", "基准"),
    ("准确率", "accuracy", "精度"),
    ("推理", "inference", "预测"),
    ("训练", "training train", "优化"),
    ("通信开销", "communication cost overhead", "通信效率"),
    ("设备端", "on-device device", "端侧"),
    ("模型卸载", "model offloading offload", "计算卸载"),
    ("资源调度", "resource allocation scheduling", "资源管理"),
    ("强化学习", "reinforcement learning RL", "奖励"),
    ("对齐", "alignment aligned", "偏好"),
    ("幻觉", "hallucination", "事实性"),
    ("长上下文", "long context", "上下文窗口"),
    ("知识图谱", "knowledge graph", "结构化知识"),
    ("个性化", "personalization personalized", "用户偏好"),
    ("大模型", "large language model LLM foundation model", "基础模型"),
    ("端侧", "on-device edge device", "设备端"),
    ("时延", "latency delay", "延迟"),
    ("通信效率", "communication efficiency", "通信"),
    ("参数高效微调", "parameter-efficient fine-tuning PEFT", "PEFT"),
    ("预训练", "pre-training pretrained", "预训练"),
    ("生成式", "generative generation", "生成"),
    ("向量数据库", "vector database", "向量存储"),
    ("检索器", "retriever retrieval", "检索"),
    ("推理成本", "inference cost", "成本"),
    ("数据异构", "data heterogeneity non-IID", "Non-IID"),
    ("模型聚合", "model aggregation federated averaging", "聚合"),
    ("本地训练", "local training", "端侧训练"),
    ("上下文学习", "in-context learning", "few-shot"),
    ("多模态", "multimodal", "多模态"),
    ("分布式训练", "distributed training", "分布式"),
    ("算力", "computation compute", "计算资源"),
    ("卸载", "offloading offload", "计算卸载"),
    ("缓存", "cache caching", "缓存"),
    ("调度", "scheduling scheduler", "调度"),
    ("鲁棒性", "robustness robust", "鲁棒"),
    ("可解释性", "interpretability explainable", "可解释"),
    ("基准测试", "benchmark", "评测"),
    ("评测", "evaluation benchmark", "评估"),
    ("吞吐", "throughput", "吞吐量"),
    ("稀疏化", "sparsity sparse", "稀疏"),
    ("低精度", "low precision", "量化"),
    ("联邦平均", "federated averaging FedAvg", "聚合算法"),
    ("模型编排", "model orchestration", "编排"),
    ("边缘服务器", "edge server", "边缘节点"),
    ("端到端", "end-to-end", "全链路"),
    ("强化学习反馈", "reinforcement learning from human feedback RLHF", "对齐"),
    ("资源约束", "resource constraints computation memory energy", "资源"),
    ("内存", "memory", "内存"),
    ("计算资源", "computation resources compute", "算力"),
    ("功耗", "power consumption energy", "功耗"),
    ("模型大小", "model size", "参数量"),
    ("设备异构", "device heterogeneity", "异构"),
    ("端侧推理", "on-device inference", "端侧"),
    ("实时性", "real-time latency", "实时"),
    ("网络带宽", "network bandwidth", "带宽"),
    ("数据隐私", "data privacy", "隐私"),
    ("模型更新", "model update updates", "更新"),
    ("梯度", "gradient gradients", "梯度"),
    ("压缩率", "compression rate ratio", "压缩"),
    ("参数量", "parameters parameter count", "规模"),
    ("加速器", "accelerator hardware", "硬件"),
    ("移动设备", "mobile device devices", "终端"),
    ("可扩展性", "scalability", "扩展"),
    ("能效", "energy efficiency", "能效"),
    ("任务卸载", "task offloading", "卸载"),
    ("服务放置", "service placement", "部署"),
    ("缓存策略", "caching policy", "缓存"),
    ("语义通信", "semantic communication", "通信"),
    ("数字孪生", "digital twin", "孪生"),
    ("车联网", "vehicular network", "车联网"),
    ("无人机", "UAV drone", "无人机"),
    ("无线", "wireless", "无线"),
    ("信道", "channel", "信道"),
    ("干扰", "interference", "干扰"),
    ("卸载决策", "offloading decision", "决策"),
    ("模型分割", "model partition partitioning", "分割"),
    ("本地推理", "local inference", "本地"),
    ("隐私保护", "privacy preservation", "隐私"),
    ("联邦", "federated", "联邦"),
    ("聚合", "aggregation aggregate", "聚合"),
    ("收敛", "convergence converge", "收敛"),
    ("非独立同分布", "non-IID", "数据分布"),
    ("客户端", "client clients", "节点"),
    ("服务器", "server", "服务端"),
    ("轮次", "round rounds", "训练轮次"),
]


def seed_glossary():
    for zh, en, note in GLOSSARY:
        db.run(
            "INSERT INTO glossary (zh, en, note) VALUES (%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE en=VALUES(en), note=VALUES(note)",
            (zh, en, note),
        )
    print(f"glossary: {len(GLOSSARY)} entries", flush=True)


def main():
    db.init_schema()
    seed_glossary()
    started = time.time()
    ok, failed = 0, 0
    for i, pid in enumerate(PAPERS, start=1):
        try:
            print(f"[{i}/{len(PAPERS)}] {pid} ...", flush=True)
            result = ingest_arxiv(pid)
            print("   ", result, flush=True)
            ok += 1
        except Exception as exc:
            failed += 1
            print(f"    FAILED {pid}: {exc}", flush=True)
            traceback.print_exc()
    stats = db.one("SELECT COUNT(*) papers, SUM(chunk_count) chunks, SUM(page_count) pages FROM papers")
    print(f"done in {time.time()-started:.0f}s ok={ok} failed={failed} stats={stats}", flush=True)


if __name__ == "__main__":
    main()
