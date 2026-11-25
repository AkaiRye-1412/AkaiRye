"""PDF 根目录扫描服务（M9，04 文档 3.8）：扫描 → manual 全量镜像同步（Q-15）。

TODO(业务实现)：
- 扫描 pdf_root 全部 PDF：新文件建 manual(status='discovered')；
- mtime 变化 → ready/update 标记；文件消失 → invalid（FR-RAG-10）；
- 删除且无切块的清理行；品牌自动识别（FR-RAG-12：高创/ACS/Agito 路径匹配）。
"""


class PdfScanService:
    def scan(self, conn) -> dict:
        raise NotImplementedError("业务实现阶段：返回差异摘要 {added, updated, removed}")
