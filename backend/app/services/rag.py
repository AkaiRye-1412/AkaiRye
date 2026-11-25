"""RAG 服务（M3/C1，FR-RAG-16~27）：混合检索 + LLM 生成 + 来源组装。

检索管线（D5 自写轻量方案，Q-14 流式通道 A）：
1. 应用层 bigram 分词 → FTS5 关键词查询；
2. query embedding（openai SDK）→ sqlite-vec KNN；
3. RRF 融合排序 → SQL 范围过滤（scope: current/project/brand/all，FR-RAG-17/18）；
4. 带 chunk 上下文调 LLM（流式），token 经 broker.publish(rag.stream) 推送；
5. 完成后消息 + 引用落库（rag_message + rag_citation 独立表，Q-13）。

TODO(业务实现)：ask()（校验同会话无并发提问 → 409 state_conflict）、检索、生成、引用组装。
"""


class RagService:
    def ask(self, conn, session_id: int, question: str, scope: dict) -> int:
        raise NotImplementedError("业务实现阶段：提问入口，返回 message_id")

    def search(self, conn, query: str, scope: dict) -> list[dict]:
        raise NotImplementedError("业务实现阶段：混合检索（RRF 融合）")
