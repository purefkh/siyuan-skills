#!/usr/bin/env python3
"""
思源笔记 Embedding 语义搜索模块
使用 OpenAI Embedding API + FAISS 向量索引
按块索引，支持重叠和 score 阈值
支持增量更新
"""

import json
import sys
from pathlib import Path
from typing import Any, List, Dict

INDEX_DIR = Path.cwd() / ".index"
METADATA_FILE = INDEX_DIR / "metadata.json"
FAISS_INDEX_FILE = INDEX_DIR / "index.faiss"

# 分块配置
CHUNK_SIZE = 1000  # 每块字符数
CHUNK_OVERLAP = 200  # 重叠字符数
MIN_SCORE_THRESHOLD = 0.5  # 最小相似度阈值


def get_openai_client(config: dict[str, Any]):
    """获取 OpenAI 客户端"""
    try:
        from openai import OpenAI
    except ImportError:
        print("Error: openai package required. Run: uv sync", file=sys.stderr)
        sys.exit(1)

    kwargs = {}
    if config.get("openai_api_key"):
        kwargs["api_key"] = config["openai_api_key"]
    if config.get("openai_base_url"):
        kwargs["base_url"] = config["openai_base_url"]
    return OpenAI(**kwargs)


def ensure_faiss():
    """确保 faiss-cpu 已安装"""
    try:
        import faiss
        return faiss
    except ImportError:
        print(f"[Error] faiss-cpu required: uv sync", file=sys.stderr)
        sys.exit(1)


def split_text_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """将文本分割成重叠的块"""
    if not text:
        return []

    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = start + chunk_size
        chunk = text[start:end]

        # 尝试在段落边界分割
        if end < text_len:
            # 寻找最近的换行符
            newline_pos = chunk.rfind('\n')
            if newline_pos > chunk_size // 2:  # 确保块不会太小
                chunk = chunk[:newline_pos]
                end = start + newline_pos + 1

        chunks.append(chunk.strip())
        start = end - overlap

    return [c for c in chunks if c]


def get_documents_from_siyuan(config: dict[str, Any]) -> List[dict]:
    """从思源获取所有文档（应用排除过滤）"""
    import importlib.util
    siyuan_path = Path(__file__).parent / "siyuan.py"
    spec = importlib.util.spec_from_file_location("siyuan", siyuan_path)
    if spec is None:
        return []
    siyuan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(siyuan)

    exclude_filter = siyuan.build_exclude_filter(config)

    # 查询所有文档
    sql = f"""
        SELECT id, hpath, box, updated
        FROM blocks
        WHERE type = 'd'{exclude_filter}
        ORDER BY updated DESC
    """.strip().replace("\n", " ")

    docs = siyuan.api_call(config, "/api/query/sql", {"stmt": sql})
    return docs if docs else []


def export_document_content(config: dict[str, Any], doc_id: str) -> str:
    """导出文档的完整内容"""
    import importlib.util
    siyuan_path = Path(__file__).parent / "siyuan.py"
    spec = importlib.util.spec_from_file_location("siyuan", siyuan_path)
    if spec is None:
        return ""
    siyuan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(siyuan)

    result = siyuan.api_call(config, "/api/export/exportMdContent", {"id": doc_id})
    return result.get("content", "")


def build_embeddings(config: dict[str, Any], texts: List[str]) -> List[List[float]]:
    """批量构建 Embedding 向量"""
    client = get_openai_client(config)
    model = config.get("openai_model", "text-embedding-3-small")

    # OpenAI API 批量限制（硅基流动限制是 64）
    batch_size = 50
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            response = client.embeddings.create(input=batch, model=model)
            embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(embeddings)
        except Exception as e:
            print(f"[Error] Embedding API call failed: {str(e)}", file=sys.stderr)
            sys.exit(1)

    return all_embeddings


def build_chunk_id(doc_id: str, chunk_index: int) -> str:
    """生成块的唯一 ID"""
    return f"{doc_id}#chunk{chunk_index}"


def parse_chunk_id(chunk_id: str) -> tuple[str, int]:
    """解析块 ID，返回 (doc_id, chunk_index)"""
    parts = chunk_id.split("#chunk")
    if len(parts) == 2:
        return parts[0], int(parts[1])
    return chunk_id, 0


def build_index(config: dict[str, Any], force: bool = False):
    """构建 FAISS 索引（支持增量更新）"""
    import time
    import numpy as np

    faiss = ensure_faiss()

    # 读取旧索引状态
    old_metadata = None
    old_chunks_map: Dict[str, dict] = {}
    old_doc_ids = set()

    if not force and METADATA_FILE.exists():
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            old_metadata = json.load(f)
        # 构建旧块映射
        for chunk in old_metadata.get("chunks", []):
            chunk_id = build_chunk_id(chunk["doc_id"], chunk["chunk_index"])
            old_chunks_map[chunk_id] = chunk
        old_doc_ids = set(chunk["doc_id"] for chunk in old_metadata.get("chunks", []))

        print(f"[Incremental mode] Old index has {len(old_chunks_map)} chunks", file=sys.stderr)
    else:
        print("[Full rebuild mode]", file=sys.stderr)

    docs = get_documents_from_siyuan(config)
    print(f"Fetching {len(docs)} documents from SiYuan...", file=sys.stderr)

    if not docs:
        print("No documents found", file=sys.stderr)
        return

    # 确定需要处理的文档
    docs_to_process = []
    last_built_at = old_metadata.get("built_at", 0) if old_metadata else 0

    for doc in docs:
        doc_updated = int(doc.get("updated", 0))
        # 如果文档是新 的或者已更新，需要处理
        if doc["id"] not in old_doc_ids or doc_updated > last_built_at:
            docs_to_process.append(doc)

    print(f"Total {len(docs)} documents, {len(docs_to_process)} changed", file=sys.stderr)

    # 合并旧块和新块
    new_chunks_map: Dict[str, dict] = {}
    new_doc_ids = set(doc["id"] for doc in docs)  # 当前所有文档 ID
    updated_doc_ids = set(d["id"] for d in docs_to_process)  # 变化的文档 ID

    # 保留未变化的文档的块
    for chunk_id, chunk in old_chunks_map.items():
        doc_id = chunk["doc_id"]
        # 只保留文档仍存在 且 未被标记为更新 的块
        if doc_id in new_doc_ids and doc_id not in updated_doc_ids:
            new_chunks_map[chunk_id] = chunk

    # 处理变化的文档（完全覆盖其所有块）
    chunks_to_add = []
    for doc in docs_to_process:
        content = export_document_content(config, doc["id"])
        chunks = split_text_into_chunks(content)

        for chunk_idx, chunk_text in enumerate(chunks):
            chunk_id = build_chunk_id(doc["id"], chunk_idx)
            chunk_data = {
                "doc_id": doc["id"],
                "hpath": doc["hpath"],
                "box": doc["box"],
                "updated": doc["updated"],
                "chunk_index": chunk_idx,
                "content": chunk_text,
            }
            new_chunks_map[chunk_id] = chunk_data
            chunks_to_add.append(chunk_data)

    if not new_chunks_map:
        print("No chunks to index", file=sys.stderr)
        return

    print(f"Building embeddings for {len(new_chunks_map)} chunks...", file=sys.stderr)

    # 构建/重建所有块的向量
    all_chunks = list(new_chunks_map.values())
    # 按文档 ID 和 chunk_index 排序，保证顺序一致
    all_chunks.sort(key=lambda x: (x["doc_id"], x["chunk_index"]))

    # 对所有块重新计算向量（简化实现，确保一致性）
    all_texts = [chunk["content"] for chunk in all_chunks]
    all_embeddings = build_embeddings(config, all_texts)

    print(f"Building FAISS index with {len(all_chunks)} chunks...", file=sys.stderr)

    # 构建 FAISS 索引（使用 IDMap 支持通过 ID 访问）
    dimension = len(all_embeddings[0])
    index = faiss.IndexFlatIP(dimension)
    index_id_map = faiss.IndexIDMap(index)

    # 添加向量
    embeddings_array = np.array(all_embeddings, dtype="float32")
    faiss.normalize_L2(embeddings_array)

    # 为每个向量分配 ID
    chunk_ids = [build_chunk_id(chunk["doc_id"], chunk["chunk_index"]) for chunk in all_chunks]
    index_id_map.add_with_ids(embeddings_array, np.arange(len(all_chunks)))

    # 保存索引
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index_id_map, str(FAISS_INDEX_FILE))

    # 保存元数据
    metadata = {
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "min_score_threshold": MIN_SCORE_THRESHOLD,
        "count": len(all_chunks),
        "document_count": len(docs),
        "dimension": dimension,
        "chunks": all_chunks,
        "built_at": int(time.time()),
    }
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"[Index build complete] mode={'incremental' if old_metadata else 'full'}, docs={len(docs)}, chunks={len(all_chunks)}, processed={len(docs_to_process)}", file=sys.stderr)


def load_index():
    """加载 FAISS 索引和元数据"""
    faiss = ensure_faiss()

    if not FAISS_INDEX_FILE.exists() or not METADATA_FILE.exists():
        return None, None

    index = faiss.read_index(str(FAISS_INDEX_FILE))
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    return index, metadata


def get_index_status() -> dict:
    """获取索引状态"""
    if not FAISS_INDEX_FILE.exists() or not METADATA_FILE.exists():
        return {"status": "not_built", "message": "Index not built"}

    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    return {
        "status": "ready",
        "document_count": metadata.get("document_count", 0),
        "chunk_count": metadata.get("count", 0),
        "chunk_size": metadata.get("chunk_size", CHUNK_SIZE),
        "chunk_overlap": metadata.get("chunk_overlap", CHUNK_OVERLAP),
        "min_score_threshold": metadata.get("min_score_threshold", MIN_SCORE_THRESHOLD),
        "built_at": metadata.get("built_at", 0),
    }


def semantic_search(config: dict[str, Any], query: str, limit: int = 8) -> List[dict]:
    """语义搜索（返回匹配的块）"""
    index, metadata = load_index()

    if index is None or metadata is None:
        return [{"error": "Index not built, run first: uv run python scripts/siyuan.py index build"}]

    # 构建查询向量
    client = get_openai_client(config)
    model = config.get("openai_model", "text-embedding-3-small")

    try:
        response = client.embeddings.create(input=[query[:8000]], model=model)
        query_embedding = response.data[0].embedding
    except Exception as e:
        return [{"error": f"Embedding API call failed: {str(e)}"}]

    # 搜索（多取一些，后续过滤）
    import numpy as np
    query_array = np.array([query_embedding], dtype="float32")
    faiss = ensure_faiss()

    # 搜索更多结果，后续按阈值过滤
    search_k = min(limit * 3, metadata["count"])
    distances, indices = index.search(query_array, search_k)

    # 格式化结果并应用阈值过滤
    min_threshold = metadata.get("min_score_threshold", MIN_SCORE_THRESHOLD)
    results = []
    rank = 1

    for idx, score in zip(indices[0], distances[0]):
        if idx < 0:  # FAISS 可能返回 -1
            continue

        # 应用 score 阈值
        if float(score) < min_threshold:
            continue

        if len(results) >= limit:
            break

        # 通过索引获取块信息
        if idx < len(metadata["chunks"]):
            chunk = metadata["chunks"][idx]
            results.append({
                "rank": rank,
                "score": float(score),
                "doc_id": chunk["doc_id"],
                "hpath": chunk["hpath"],
                "chunk_index": chunk["chunk_index"],
                "content": chunk["content"],
            })
            rank += 1

    return results
