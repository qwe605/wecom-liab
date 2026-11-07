"""bge-small ONNX INT8 与 hnswlib 的轻量本地实现。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Iterable


MAX_INDEX_BYTES = 150 * 1024 * 1024
MAX_SEQUENCE_LENGTH = 256


class FastEmbedTextEmbedder:
    """通过 FastEmbed/ONNX Runtime 单线程加载小型中文 Embedding 模型。"""

    def __init__(self, model_name: str, model_path: str = ""):
        """延迟导入重依赖，确保主 Web 进程不会意外加载模型。"""

        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError("轻量向量服务缺少 fastembed，请安装 requirements-embedding.txt") from exc
        self.model_name = model_name
        self._model = TextEmbedding(
            model_name=model_name,
            threads=1,
            specific_model_path=model_path or None,
            local_files_only=bool(model_path),
        )
        # FastEmbed 0.7 尚未暴露公共 max_length 参数；生产模型目录已固化
        # tokenizer_config=256，这里再次收紧运行时 tokenizer，防止开发环境
        # 直接下载 512 Token 默认模型而突破单进程内存预算。
        inner_model = getattr(self._model, "model", None)
        tokenizer = getattr(inner_model, "tokenizer", None)
        if tokenizer is None:
            raise RuntimeError("无法确认 FastEmbed tokenizer，拒绝以未知序列长度启动")
        tokenizer.enable_truncation(max_length=MAX_SEQUENCE_LENGTH)
        self._lock = RLock()

    def embed(self, texts: Iterable[str], batch_size: int = 1) -> list:
        """按受限批次生成归一化向量，所有推理在单锁内串行。"""

        values = [str(item)[:4000] for item in texts]
        with self._lock:
            return list(self._model.embed(values, batch_size=max(1, min(batch_size, 4))))


class HnswSkillIndex:
    """保存可原子替换、可由 MySQL 全量重建的 Skill 向量索引。"""

    def __init__(self, directory: str, embedder: FastEmbedTextEmbedder):
        """加载已有索引；索引不存在时保持空状态并允许全文检索降级。"""

        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_file = self.directory / "skills.hnsw"
        self.metadata_file = self.directory / "skills.json"
        self.embedder = embedder
        self._lock = RLock()
        self._index = None
        self._metadata: dict = {"documents": {}, "count": 0, "dimension": 0, "index_version": ""}
        self._load()

    def _load(self) -> None:
        """从原子快照加载索引和元数据，损坏时保持空索引。"""

        if not self.index_file.exists() or not self.metadata_file.exists():
            return
        try:
            import hnswlib

            metadata = json.loads(self.metadata_file.read_text(encoding="utf-8"))
            index = hnswlib.Index(space="cosine", dim=int(metadata["dimension"]))
            index.load_index(str(self.index_file), max_elements=max(int(metadata["count"]), 1))
            index.set_num_threads(1)
            index.set_ef(64)
            self._index, self._metadata = index, metadata
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            self._index = None

    @staticmethod
    def _text(document: dict) -> str:
        """使用稳定字段顺序构造索引文本。"""

        path = " > ".join(item["name"] for item in document["taxonomy_path"])
        return f"{path}\n{document['title']}\n{document['content']}"

    def rebuild(self, documents: list[dict]) -> dict:
        """单线程重建全量索引，并用临时文件原子替换生产快照。"""

        if len(documents) > 20_000:
            raise RuntimeError("有效 Skill 超过 2 万条，本地 hnswlib 方案已达到迁移边界")
        try:
            import hnswlib
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("轻量向量服务缺少 hnswlib/numpy") from exc
        if not documents:
            with self._lock:
                self._index = None
                self._metadata = {"documents": {}, "count": 0, "dimension": 0, "index_version": "empty"}
            self.metadata_file.write_text(json.dumps(self._metadata), encoding="utf-8")
            if self.index_file.exists():
                self.index_file.unlink()
            return self._metadata

        texts = [self._text(item) for item in documents]
        vectors = np.asarray(self.embedder.embed(texts, batch_size=4), dtype=np.float32)
        dimension = int(vectors.shape[1])
        labels = np.asarray([int(item["skill_id"]) for item in documents], dtype=np.int64)
        index = hnswlib.Index(space="cosine", dim=dimension)
        index.init_index(max_elements=len(documents), ef_construction=120, M=16)
        index.set_num_threads(1)
        index.add_items(vectors, labels, num_threads=1)
        index.set_ef(64)
        digest = hashlib.sha256()
        for item in documents:
            digest.update(f"{item['skill_id']}:{item['version']}:{self._text(item)}".encode("utf-8"))
        metadata = {
            "documents": {str(item["skill_id"]): item for item in documents},
            "count": len(documents),
            "dimension": dimension,
            "index_version": digest.hexdigest()[:16],
            "embedding_model": self.embedder.model_name,
        }
        temp_index = self.index_file.with_suffix(".hnsw.tmp")
        temp_metadata = self.metadata_file.with_suffix(".json.tmp")
        index.save_index(str(temp_index))
        temp_metadata.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        index_bytes = temp_index.stat().st_size
        snapshot_bytes = index_bytes + temp_metadata.stat().st_size
        if snapshot_bytes > MAX_INDEX_BYTES:
            # 先删除尚未发布的临时快照，再触发迁移边界；线上旧索引继续可用。
            temp_index.unlink(missing_ok=True)
            temp_metadata.unlink(missing_ok=True)
            raise RuntimeError(
                f"hnswlib 索引快照达到 {snapshot_bytes / 1024 / 1024:.1f}MB，"
                "超过 150MB 本地方案迁移边界"
            )
        metadata["index_bytes"] = index_bytes
        metadata["snapshot_bytes"] = snapshot_bytes
        temp_metadata.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        os.replace(temp_index, self.index_file)
        os.replace(temp_metadata, self.metadata_file)
        with self._lock:
            self._index, self._metadata = index, metadata
        return metadata

    def search(self, query: str, allowed_ids: set[int], limit: int) -> list[dict]:
        """在数据库预先确定的允许标签集合内执行一跳向量召回。"""

        if not allowed_ids:
            return []
        with self._lock:
            if self._index is None:
                raise RuntimeError("轻量向量索引尚未构建")
            indexed_ids = {int(item) for item in self._metadata["documents"]}
            allowed_ids = allowed_ids & indexed_ids
            if not allowed_ids:
                return []
            vector = self.embedder.embed([query], batch_size=1)[0]
            count = min(max(1, int(limit)), len(allowed_ids), int(self._metadata["count"]))
            self._index.set_ef(max(64, count * 4))
            labels, distances = self._index.knn_query(
                vector,
                k=count,
                num_threads=1,
                filter=lambda label: int(label) in allowed_ids,
            )
            result = []
            for label, distance in zip(labels[0], distances[0]):
                document = self._metadata["documents"].get(str(int(label)))
                if document:
                    result.append({**document, "score": max(0.0, 1.0 - float(distance))})
            return result

    @property
    def diagnostics(self) -> dict:
        """返回不包含知识正文的索引健康信息。"""

        return {
            "ready": self._index is not None,
            "count": int(self._metadata.get("count") or 0),
            "dimension": int(self._metadata.get("dimension") or 0),
            "index_version": str(self._metadata.get("index_version") or ""),
            "embedding_model": self.embedder.model_name,
            "index_bytes": int(self._metadata.get("index_bytes") or 0),
            "snapshot_bytes": int(self._metadata.get("snapshot_bytes") or 0),
        }
