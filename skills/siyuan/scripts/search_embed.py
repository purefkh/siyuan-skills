#!/usr/bin/env python3
"""
思源笔记 Embedding 语义搜索模块
使用 OpenAI Embedding API + FAISS 向量索引
按块索引，支持重叠和 score 阈值
支持增量更新
使用 SQLite 存储元数据
"""

import sqlite3
import sys
from pathlib import Path
from typing import Any, List, Dict, Optional, Tuple

INDEX_DIR = Path.cwd() / ".index"
DB_FILE = INDEX_DIR / "index.db"
FAISS_INDEX_FILE = INDEX_DIR / "index.faiss"

# 分块配置
CHUNK_SIZE = 1000  # 每块字符数
CHUNK_OVERLAP = 200  # 重叠字符数
MIN_SCORE_THRESHOLD = 0.5  # 最小相似度阈值


def get_db_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # 支持字典访问
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 文档表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            hpath TEXT,
            box TEXT,
            updated TEXT,
            embedding_time INTEGER
        )
    """)

    # chunk 表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT,
            chunk_index INTEGER,
            content TEXT,
            faiss_id INTEGER,
            FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
        )
    """)

    # 创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_faiss_id ON chunks(faiss_id)")

    conn.commit()
    conn.close()


def get_next_faiss_id(conn: sqlite3.Connection) -> int:
    """获取下一个可用的 faiss_id"""
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(MAX(faiss_id), -1) + 1 as next_id FROM chunks")
    result = cursor.fetchone()
    return result["next_id"] if result else 0


def get_openai_client(openai_config: dict[str, Any]):
    """获取 OpenAI 客户端"""
    try:
        from openai import OpenAI
    except ImportError:
        print("Error: openai package required. Run: uv sync", file=sys.stderr)
        sys.exit(1)

    kwargs = {}
    if openai_config.get("openai_api_key"):
        kwargs["api_key"] = openai_config["openai_api_key"]
    if openai_config.get("openai_base_url"):
        kwargs["base_url"] = openai_config["openai_base_url"]
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


def build_embeddings(openai_config: dict[str, Any], texts: List[str]) -> List[List[float]]:
    """批量构建 Embedding 向量"""
    client = get_openai_client(openai_config)
    model = openai_config.get("openai_model", "text-embedding-3-small")

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


def build_index(openai_config: dict[str, Any], docs_data: List[dict],
                force: bool = False, server_time: int = 0):
    """
    构建 FAISS 索引（支持增量更新，使用 SQLite 存储）

    Args:
        openai_config: OpenAI 配置字典，包含:
            - openai_api_key: API key
            - openai_base_url: API base URL (可选)
            - openai_model: Embedding 模型
        docs_data: 文档数据列表，每项包含:
            - id: 文档ID
            - hpath: 文档路径
            - box: 笔记本ID
            - updated: 更新时间 (YYYYMMDDHHmmss)
            - content: 文档完整内容
        force: 是否强制重建
        server_time: 服务器时间（毫秒）
    """
    import numpy as np

    faiss = ensure_faiss()

    # 初始化数据库
    init_db()
    conn = get_db_connection()
    cursor = conn.cursor()

    # 从数据库读取已索引的文档
    cursor.execute("SELECT doc_id, updated, embedding_time FROM documents")
    indexed_docs = {row["doc_id"]: {"updated": row["updated"], "embedding_time": row["embedding_time"]} for row in cursor.fetchall()}

    if not force and indexed_docs:
        print(f"[Incremental mode] Old index has {len(indexed_docs)} documents", file=sys.stderr)
    else:
        print("[Full rebuild mode]", file=sys.stderr)
        if force:
            # 清空数据库
            cursor.execute("DELETE FROM chunks")
            cursor.execute("DELETE FROM documents")
            conn.commit()
            indexed_docs = {}

    print(f"Total {len(docs_data)} documents", file=sys.stderr)

    if not docs_data:
        print("No documents to process", file=sys.stderr)
        conn.close()
        return

    # 构建当前文档集合
    current_doc_ids = set(doc["id"] for doc in docs_data)

    # 1. 找出需要处理的文档（新增或更新）
    docs_to_process = []
    for doc in docs_data:
        doc_id = doc["id"]
        doc_updated = str(doc.get("updated", ""))

        if doc_id not in indexed_docs:
            # 新文档
            docs_to_process.append(doc)
        elif doc_updated != indexed_docs[doc_id]["updated"]:
            # 文档已更新
            docs_to_process.append(doc)

    # 2. 找出已删除的文档
    deleted_doc_ids = set(indexed_docs.keys()) - current_doc_ids

    print(f"Changes: {len(docs_to_process)} to update, {len(deleted_doc_ids)} deleted", file=sys.stderr)

    # 如果没有变化，直接返回
    if not docs_to_process and not deleted_doc_ids:
        print("[No changes detected] Index up to date", file=sys.stderr)
        conn.close()
        return

    # 3. 删除已删除文档的 chunks
    if deleted_doc_ids:
        placeholders = ",".join("?" * len(deleted_doc_ids))
        cursor.execute(f"DELETE FROM chunks WHERE doc_id IN ({placeholders})", list(deleted_doc_ids))
        cursor.execute(f"DELETE FROM documents WHERE doc_id IN ({placeholders})", list(deleted_doc_ids))
        conn.commit()

    # 4. 获取变化文档的旧 faiss_id
    updated_doc_ids = [d["id"] for d in docs_to_process]
    old_faiss_ids_to_remove = []
    if updated_doc_ids and FAISS_INDEX_FILE.exists() and not force:
        placeholders = ",".join("?" * len(updated_doc_ids))
        cursor.execute(f"SELECT faiss_id FROM chunks WHERE doc_id IN ({placeholders})", updated_doc_ids)
        old_faiss_ids_to_remove = [row["faiss_id"] for row in cursor.fetchall()]

    # 5. 删除变化文档的旧 chunks
    if updated_doc_ids:
        placeholders = ",".join("?" * len(updated_doc_ids))
        cursor.execute(f"DELETE FROM chunks WHERE doc_id IN ({placeholders})", updated_doc_ids)
        cursor.execute(f"DELETE FROM documents WHERE doc_id IN ({placeholders})", updated_doc_ids)
        conn.commit()

    # 6. 为新文档/变化文档生成 chunks
    chunks_to_add = []
    for doc in docs_to_process:
        content = doc.get("content", "")
        chunks = split_text_into_chunks(content)

        for chunk_idx, chunk_text in enumerate(chunks):
            chunk_id = build_chunk_id(doc["id"], chunk_idx)
            chunks_to_add.append({
                "chunk_id": chunk_id,
                "doc_id": doc["id"],
                "chunk_index": chunk_idx,
                "content": chunk_text,
            })

    # 7. 只对变化文档的 chunks 生成 embeddings
    if chunks_to_add:
        print(f"Building embeddings for {len(chunks_to_add)} new/changed chunks...", file=sys.stderr)
        texts = [chunk["content"] for chunk in chunks_to_add]
        new_embeddings = build_embeddings(openai_config, texts)
    else:
        new_embeddings = []

    # 8. 从旧 FAISS 索引提取未变化文档的 embeddings
    old_embeddings_map = {}  # faiss_id -> embedding
    if FAISS_INDEX_FILE.exists() and not force:
        old_index = faiss.read_index(str(FAISS_INDEX_FILE))
        # 获取当前数据库中所有 faiss_id（已排除变化文档的）
        cursor.execute("SELECT faiss_id FROM chunks")
        all_current_faiss_ids = set(row["faiss_id"] for row in cursor.fetchall())

        # 排除需要删除的旧 faiss_id
        unchanged_faiss_ids = all_current_faiss_ids - set(old_faiss_ids_to_remove)

        if unchanged_faiss_ids:
            # 从旧索引提取 embeddings
            for faiss_id in unchanged_faiss_ids:
                try:
                    vector = old_index.reconstruct(int(faiss_id))
                    old_embeddings_map[faiss_id] = vector
                except Exception as e:
                    print(f"[Warning] Failed to reconstruct faiss_id {faiss_id}: {e}", file=sys.stderr)

    print(f"[Incremental update] Reusing {len(old_embeddings_map)} old embeddings", file=sys.stderr)

    # 9. 将新 chunks 写入数据库
    next_faiss_id = get_next_faiss_id(conn)
    for chunk, embedding in zip(chunks_to_add, new_embeddings):
        cursor.execute("""
            INSERT INTO chunks (chunk_id, doc_id, chunk_index, content, faiss_id)
            VALUES (?, ?, ?, ?, ?)
        """, (chunk["chunk_id"], chunk["doc_id"], chunk["chunk_index"], chunk["content"], next_faiss_id))
        next_faiss_id += 1

    # 写入文档信息
    for doc in docs_to_process:
        cursor.execute("""
            INSERT OR REPLACE INTO documents (doc_id, hpath, box, updated, embedding_time)
            VALUES (?, ?, ?, ?, ?)
        """, (doc["id"], doc["hpath"], doc["box"], doc["updated"], server_time))

    conn.commit()

    # 10. 收集所有 chunks 并按旧的 faiss_id 排序
    cursor.execute("SELECT chunk_id, doc_id, chunk_index, content, faiss_id FROM chunks ORDER BY faiss_id")
    all_chunks = cursor.fetchall()

    # 11. 准备所有 embeddings（复用旧的 + 新生成的）
    new_embeddings_index = 0
    final_embeddings = []
    for chunk in all_chunks:
        old_faiss_id = chunk["faiss_id"]
        if old_faiss_id in old_embeddings_map:
            # 复用旧的 embedding
            final_embeddings.append(old_embeddings_map[old_faiss_id])
        else:
            # 这是新 chunk
            if new_embeddings_index < len(new_embeddings):
                final_embeddings.append(new_embeddings[new_embeddings_index])
                new_embeddings_index += 1
            else:
                # 兜底：重新生成（不应该到这里）
                print(f"[Warning] Missing embedding for chunk {chunk['chunk_id']}, regenerating...", file=sys.stderr)
                embedding = build_embeddings(config, [chunk["content"]])[0]
                final_embeddings.append(embedding)

    print(f"Building FAISS index with {len(final_embeddings)} total chunks...", file=sys.stderr)

    # 12. 构建 FAISS 索引
    if final_embeddings:
        dimension = len(final_embeddings[0])
        index = faiss.IndexFlatIP(dimension)
        index_id_map = faiss.IndexIDMap(index)

        embeddings_array = np.array(final_embeddings, dtype="float32")
        faiss.normalize_L2(embeddings_array)

        # 使用连续的 ID (0, 1, 2, ...)
        index_id_map.add_with_ids(embeddings_array, np.arange(len(final_embeddings)))

        # 13. 更新数据库中的 faiss_id 为连续的 ID
        for i, chunk in enumerate(all_chunks):
            cursor.execute("UPDATE chunks SET faiss_id = ? WHERE chunk_id = ?", (i, chunk["chunk_id"]))
        conn.commit()

        # 14. 保存 FAISS 索引
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index_id_map, str(FAISS_INDEX_FILE))

    conn.close()

    print(f"[Index build complete] mode={'incremental' if indexed_docs else 'full'}, docs={len(docs_data)}, chunks={len(all_chunks)}, processed={len(docs_to_process)}, deleted={len(deleted_doc_ids)}", file=sys.stderr)



def load_index():
    """加载 FAISS 索引"""
    faiss = ensure_faiss()

    if not FAISS_INDEX_FILE.exists():
        return None, None

    index = faiss.read_index(str(FAISS_INDEX_FILE))
    return index, None  # metadata 不再需要


def get_index_status() -> dict:
    """获取索引状态"""
    if not DB_FILE.exists() or not FAISS_INDEX_FILE.exists():
        return {"status": "not_built", "message": "Index not built"}

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as count FROM documents")
    doc_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM chunks")
    chunk_count = cursor.fetchone()["count"]

    conn.close()

    return {
        "status": "ready",
        "document_count": doc_count,
        "chunk_count": chunk_count,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "min_score_threshold": MIN_SCORE_THRESHOLD,
    }


def semantic_search(openai_config: dict[str, Any], query: str, limit: int = 8) -> List[dict]:
    """语义搜索（返回匹配的块）"""
    index, _ = load_index()

    if index is None:
        return [{"error": "Index not built, run first: uv run python scripts/siyuan.py index build"}]

    # 构建查询向量
    client = get_openai_client(openai_config)
    model = openai_config.get("openai_model", "text-embedding-3-small")

    try:
        response = client.embeddings.create(input=[query[:8000]], model=model)
        query_embedding = response.data[0].embedding
    except Exception as e:
        return [{"error": f"Embedding API call failed: {str(e)}"}]

    # 搜索（多取一些，后续过滤）
    import numpy as np
    query_array = np.array([query_embedding], dtype="float32")
    faiss = ensure_faiss()

    # 获取总 chunk 数
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM chunks")
    total_chunks = cursor.fetchone()["count"]

    # 搜索更多结果，后续按阈值过滤
    search_k = min(limit * 3, total_chunks)
    distances, indices = index.search(query_array, search_k)

    conn.close()

    # 格式化结果并应用阈值过滤
    results = []
    rank = 1

    for idx, score in zip(indices[0], distances[0]):
        if idx < 0:  # FAISS 可能返回 -1
            continue

        # 应用 score 阈值
        if float(score) < MIN_SCORE_THRESHOLD:
            continue

        if len(results) >= limit:
            break

        # 从数据库获取 chunk 信息
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.chunk_id, c.doc_id, c.content, d.hpath
            FROM chunks c
            JOIN documents d ON c.doc_id = d.doc_id
            WHERE c.faiss_id = ?
        """, (int(idx),))
        chunk = cursor.fetchone()
        conn.close()

        if chunk:
            results.append({
                "rank": rank,
                "score": float(score),
                "doc_id": chunk["doc_id"],
                "hpath": chunk["hpath"],
                "chunk_index": int(chunk["chunk_id"].split("#chunk")[1]) if "#chunk" in chunk["chunk_id"] else 0,
                "content": chunk["content"],
            })
            rank += 1

    return results

