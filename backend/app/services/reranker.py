"""本地 reranker（BGE cross-encoder）精排服务。

背景：向量检索召回一批候选后，用 reranker 对 (query, 候选) 逐条打分，把「真正相关」
的排到前面，再取前 N 条进 LLM。这是 RAG 的常见增强：向量/关键词负责召回，reranker 负责精排。

实现要点：
- 惰性加载：FlagEmbedding + torch 很重（CPU 上加载要几秒、占几百 MB 内存），只在
  第一次真正需要时加载；配置里没启用 reranker 时，本模块一个 import 都不触发。
- 单例 + 进程内缓存：模型只加载一次；rerank_model_path 变化时重新加载（换权重）。
- 线程安全：rerank 在后台生成线程里跑，锁住「加载/切换模型」这一小段即可，推理本身
  torch 自带 GIL，无需额外加锁。
- 失败降级：权重不存在 / 加载失败 / 推理报错，一律回退「按原召回顺序」，绝不阻断问答。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 进程内单例：只加载一次
_lock = threading.Lock()
_model = None
_model_path: str | None = None
_load_error: str | None = None


def _load(model_path: str):
    """加载 FlagReranker（import 在这里才发生，避免冷启动拖慢主进程）。"""
    global _model, _model_path, _load_error
    try:
        from FlagEmbedding import FlagReranker  # 重依赖，惰性 import

        _model = FlagReranker(model_path, use_fp16=False)
        _model_path = model_path
        _load_error = None
        logger.info("reranker 已加载：%s", model_path)
    except Exception as e:  # noqa: BLE001 —— 任何加载失败都记为可降级错误
        _model = None
        _model_path = None
        _load_error = f"{type(e).__name__}: {e}"
        logger.warning("reranker 加载失败（%s）：%s", model_path, _load_error)


def _ensure(model_path: str) -> bool:
    """确保模型已加载（路径变化时重载）。返回是否可用。"""
    global _model, _model_path
    if _model is not None and _model_path == model_path:
        return True
    with _lock:
        if _model is not None and _model_path == model_path:
            return True
        _load(model_path)
        return _model is not None


def enabled(model_path: str) -> bool:
    """配置启用了 reranker 且权重目录存在（调用方据此决定走不走精排）。"""
    if not model_path:
        return False
    return Path(model_path).is_dir()


def rerank(query: str, candidates: list[dict], model_path: str, keep: int) -> list[dict]:
    """对候选按 (query, 正文) 精排，返回按分数降序、最多 keep 条。

    candidates 是 retrieve() 产出的 chunk dict（含 content 字段）。失败时原样返回，
    由调用方再截到 keep 条——绝不因为 reranker 挂了就丢候选。
    """
    if not candidates or keep <= 0:
        return candidates
    if not _ensure(model_path):
        return candidates[:keep]

    # 打分输入：标题 + 正文（与向量化的输入口径一致，标题能代表块主题）
    texts = [(c.get("title_path") or "") + "\n" + (c.get("content") or "") for c in candidates]
    try:
        scores = _model.compute_score([[query, t] for t in texts], normalize=True)
        if scores is None:
            return candidates[:keep]
        if isinstance(scores, float):
            scores = [scores]
        scored = list(zip(candidates, scores))
        scored.sort(key=lambda x: float(x[1]), reverse=True)
        out = [c for c, _ in scored[:keep]]
        # 把分数写回，便于 debug / 前端引用展示
        for c, s in scored[:keep]:
            c["rerank_score"] = round(float(s), 4)
        return out
    except Exception as e:  # noqa: BLE001 —— 推理失败降级为原顺序
        logger.warning("rerank 推理失败，回退原顺序：%s", str(e)[:160])
        return candidates[:keep]


def load_error() -> str | None:
    """最近一次加载失败的描述（设置页可据此提示，而非静默失败）。"""
    return _load_error
