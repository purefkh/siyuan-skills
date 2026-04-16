#!/usr/bin/env python3
"""
思源笔记 CLI 工具
管理思源笔记 - 搜索、创建、编辑、删除笔记、笔记本和块
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
import time
from pathlib import Path
from typing import Any, Optional

# =============================================================================
# 配置和 .env 加载
# =============================================================================

ENV_FILE = Path.cwd() / ".env"
INDEX_DIR = Path.cwd() / ".index"


def load_env() -> dict[str, str]:
    """加载项目级 .env 文件（手动解析，不依赖 python-dotenv）"""
    env = {}
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # 跳过空行和注释
                if not line or line.startswith("#"):
                    continue
                # 解析 KEY=VALUE，支持引号包裹
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    # 去除引号
                    if (value.startswith('"') and value.endswith('"')) or \
                       (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    env[key] = value
    # 合并系统环境变量（环境变量优先级更高）
    for key, value in os.environ.items():
        if key.startswith(("SIYUAN_", "OPENAI_")):
            env[key] = value
    return env


def get_config() -> dict[str, Any]:
    """获取配置（命令行参数 > 环境变量 > .env）"""
    env = load_env()
    host = env.get("SIYUAN_HOST", "127.0.0.1")
    port = env.get("SIYUAN_PORT", "6806")
    token = env.get("SIYUAN_API_TOKEN", "")
    return {
        "endpoint": f"http://{host}:{port}",
        "token": token,
        "openai_api_key": env.get("OPENAI_API_KEY", ""),
        "openai_base_url": env.get("OPENAI_BASE_URL", ""),
        "openai_model": env.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        "openai_embedding_dimension": env.get("OPENAI_EMBEDDING_DIMENSION", ""),  # 可选
        "exclude_notebooks": env.get("SIYUAN_EXCLUDE_NOTEBOOKS", ""),
        "exclude_docs": env.get("SIYUAN_EXCLUDE_DOCS", ""),
        "exclude_paths": env.get("SIYUAN_EXCLUDE_PATHS", ""),
    }


def build_exclude_filter(config: dict[str, Any]) -> str:
    """构建排除过滤的 SQL WHERE 子句"""
    clauses = []
    # 排除笔记本（box 字段）
    notebooks = config["exclude_notebooks"].strip()
    if notebooks:
        ids = "','".join([nb.strip() for nb in notebooks.split(",") if nb.strip()])
        clauses.append(f"box NOT IN ('{ids}')")
    # 排除文档（id 字段）
    docs = config["exclude_docs"].strip()
    if docs:
        ids = "','".join([doc.strip() for doc in docs.split(",") if doc.strip()])
        clauses.append(f"id NOT IN ('{ids}')")
    # 排除路径（hpath 字段）
    paths = config["exclude_paths"].strip()
    if paths:
        for p in paths.split(","):
            p = p.strip()
            if p:
                # 转义 LIKE 通配符
                p_escaped = p.replace("%", "\\%").replace("_", "\\_")
                clauses.append(f"hpath NOT LIKE '{p_escaped}%'")
    if clauses:
        return " AND " + " AND ".join(clauses)
    return ""


# =============================================================================
# API 调用函数
# =============================================================================

def api_call(config: dict[str, Any], endpoint: str, data: Optional[dict] = None) -> Any:
    """调用思源 API"""
    url = f"{config['endpoint']}{endpoint}"
    headers = {
        "Content-Type": "application/json",
    }
    if config["token"]:
        headers["Authorization"] = f"Token {config['token']}"

    body = json.dumps(data).encode("utf-8") if data else b"{}"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.load(resp)
            if result.get("code", 0) != 0:
                print(json.dumps({"error": result.get("msg", "Unknown error"), "code": result.get("code")}))
                sys.exit(1)
            return result.get("data")
    except urllib.error.HTTPError as e:
        print(json.dumps({"error": f"HTTP {e.code}: {e.reason}", "code": e.code}))
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Connection failed: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e), "code": -1}))
        sys.exit(1)


def escape_sql_like(s: str) -> str:
    """转义 SQL LIKE 模式中的特殊字符"""
    return s.replace("'", "''").replace("%", "\\%").replace("_", "\\_")


def escape_sql(s: str) -> str:
    """转义 SQL 字符串"""
    return s.replace("'", "''")


def get_server_time(config: dict[str, Any]) -> int:
    """获取思源服务器时间（毫秒级时间戳）"""
    return api_call(config, "/api/system/currentTime", {})


# =============================================================================
# 命令实现
# =============================================================================

def cmd_config(args: argparse.Namespace):
    """配置管理"""
    if args.action == "set":
        # 写入 .env 文件
        env_file = ENV_FILE
        env_file.parent.mkdir(parents=True, exist_ok=True)
        existing_lines = []
        if env_file.exists():
            with open(env_file, "r", encoding="utf-8") as f:
                existing_lines = f.readlines()

        # 更新或添加配置
        config_map = {}
        for line in existing_lines:
            if "=" in line and not line.strip().startswith("#"):
                key, _ = line.split("=", 1)
                config_map[key.strip()] = True

        new_lines = []
        added_keys = set()
        for line in existing_lines:
            if "=" in line and not line.strip().startswith("#"):
                key = line.split("=", 1)[0].strip()
                if key == "SIYUAN_HOST" and args.host:
                    new_lines.append(f"SIYUAN_HOST={args.host}\n")
                    added_keys.add("SIYUAN_HOST")
                elif key == "SIYUAN_PORT" and args.port:
                    new_lines.append(f"SIYUAN_PORT={args.port}\n")
                    added_keys.add("SIYUAN_PORT")
                elif key == "SIYUAN_API_TOKEN" and args.token:
                    new_lines.append(f"SIYUAN_API_TOKEN={args.token}\n")
                    added_keys.add("SIYUAN_API_TOKEN")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # 添加新配置
        if "SIYUAN_HOST" not in added_keys and args.host:
            new_lines.append(f"SIYUAN_HOST={args.host}\n")
        if "SIYUAN_PORT" not in added_keys and args.port:
            new_lines.append(f"SIYUAN_PORT={args.port}\n")
        if "SIYUAN_API_TOKEN" not in added_keys and args.token:
            new_lines.append(f"SIYUAN_API_TOKEN={args.token}\n")

        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print(f"Config saved to: {env_file}")

    elif args.action == "show":
        config = get_config()
        # 遮蔽 token
        token = config["token"]
        if token:
            token = token[:3] + "***" + token[-3:] if len(token) > 6 else "***"

        print(f"SiYuan Endpoint: {config['endpoint']}")
        print(f"API Token: {token}")
        print(f"OpenAI Configured: {'yes' if config['openai_api_key'] else 'no'}")
        if config['openai_base_url']:
            print(f"OpenAI Base URL: {config['openai_base_url']}")
        if config['exclude_notebooks']:
            print(f"Exclude Notebooks: {config['exclude_notebooks']}")
        if config['exclude_docs']:
            print(f"Exclude Docs: {config['exclude_docs']}")
        if config['exclude_paths']:
            print(f"Exclude Paths: {config['exclude_paths']}")


def cmd_search_keyword(args: argparse.Namespace):
    """关键字搜索（SQL LIKE）"""
    config = get_config()
    exclude_filter = build_exclude_filter(config)

    # 解析搜索词
    terms = args.terms.split()
    if not terms:
        print("Error: Search query required", file=sys.stderr)
        sys.exit(1)

    # 构建 SQL（优先使用 markdown 字段）
    limit = args.limit or 10
    type_filter = ""
    if args.type:
        type_filter = f" AND type = '{escape_sql(args.type)}'"

    like_clauses = [f"markdown LIKE '%{escape_sql_like(term)}%'" for term in terms]
    sql = f"""
        SELECT id, root_id, markdown, type, hpath, box, updated
        FROM blocks
        WHERE {' AND '.join(like_clauses)}
        {type_filter}
        {exclude_filter}
        ORDER BY updated DESC
        LIMIT {limit}
    """.strip().replace("\n", " ")

    result = api_call(config, "/api/query/sql", {"stmt": sql})
    if not result:
        print("No results found")
        return
    for i, r in enumerate(result, 1):
        print(f"[{i}] {r['hpath']}")
        print(f"    id: {r['id']} | type: {r['type']}")
        print(f"    doc: {r['root_id']} | box: {r['box']}")
        print(r.get("markdown", r.get("content", "")))
        if i < len(result):
            print("\n" + "=" * 60 + "\n")
        else:
            print()


def cmd_search_doc(args: argparse.Namespace):
    """全文搜索（导出完整文档供 Claude 分析）"""
    config = get_config()
    exclude_filter = build_exclude_filter(config)

    terms = args.terms.split()
    if not terms:
        print("Error: Search query required", file=sys.stderr)
        sys.exit(1)

    limit = args.limit or 5
    like_clauses = [f"markdown LIKE '%{escape_sql_like(term)}%'" for term in terms]
    sql = f"""
        SELECT id, hpath, box
        FROM blocks
        WHERE type = 'd' AND {' AND '.join(like_clauses)}
        {exclude_filter}
        ORDER BY updated DESC
        LIMIT {limit}
    """.strip().replace("\n", " ")

    # 先搜索文档 ID
    docs = api_call(config, "/api/query/sql", {"stmt": sql})
    if not docs:
        print("No documents found")
        return

    # 导出每个文档的完整内容
    results = []
    for doc in docs:
        doc_id = doc["id"]
        exported = api_call(config, "/api/export/exportMdContent", {"id": doc_id})
        results.append({
            "id": doc_id,
            "hpath": doc["hpath"],
            "content": exported.get("content", ""),
        })

    # 友好的输出格式
    for i, r in enumerate(results, 1):
        print(f"[{i}] {r['hpath']}")
        print(f"    id: {r['id']}")
        print(r["content"])
        if i < len(results):
            print("\n" + "=" * 60 + "\n")
        else:
            print()


def cmd_search_recent(args: argparse.Namespace):
    """最近修改"""
    config = get_config()
    exclude_filter = build_exclude_filter(config)

    limit = args.limit or 10
    sql = f"""
        SELECT id, content, type, hpath, box, updated
        FROM blocks
        WHERE type = 'd'{exclude_filter}
        ORDER BY updated DESC
        LIMIT {limit}
    """.strip().replace("\n", " ")

    result = api_call(config, "/api/query/sql", {"stmt": sql})
    if not result:
        print("No recent documents found")
        return
    for i, r in enumerate(result, 1):
        print(f"[{i}] {r['hpath']}")
        print(f"    id: {r['id']} | updated: {r['updated']}")
        if i < len(result):
            print("\n" + "=" * 60 + "\n")
        else:
            print()


def cmd_sql(args: argparse.Namespace):
    """原始 SQL 查询"""
    config = get_config()
    exclude_filter = build_exclude_filter(config)

    stmt = args.stmt
    # 如果是 SELECT，自动应用排除过滤
    if stmt.strip().upper().startswith("SELECT"):
        stmt = stmt.rstrip(";") + exclude_filter + ";"

    result = api_call(config, "/api/query/sql", {"stmt": stmt})
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_notebook(args: argparse.Namespace):
    """笔记本管理"""
    config = get_config()

    if args.action == "list":
        result = api_call(config, "/api/notebook/lsNotebooks", {})
        notebooks = result.get("notebooks", [])
        if not notebooks:
            print("No notebooks found")
            return
        for nb in notebooks:
            status = "closed" if nb.get("closed") else "open"
            print(f"[{nb['id']}] {nb['name']} ({status})")

    elif args.action == "create":
        data = {"name": args.name}
        result = api_call(config, "/api/notebook/createNotebook", data)
        print(f"Notebook created: {result['name']}")
        print(f"    id: {result['id']}")

    elif args.action == "rename":
        data = {"notebook": args.id, "name": args.name}
        api_call(config, "/api/notebook/renameNotebook", data)
        print("OK")

    elif args.action == "delete":
        if not args.force:
            # 先获取笔记本信息
            result = api_call(config, "/api/notebook/lsNotebooks", {})
            target = next((nb for nb in result.get("notebooks", []) if nb["id"] == args.id), None)
            if target:
                print(json.dumps({
                    "error": f"Use --force to confirm deletion",
                    "target": {"id": target["id"], "name": target["name"]},
                    "code": 1
                }, ensure_ascii=False))
            else:
                print(f"Error: Notebook not found", file=sys.stderr)
            sys.exit(1)
        api_call(config, "/api/notebook/removeNotebook", {"notebook": args.id})
        print(f"OK (deleted: {args.id})")

    elif args.action == "open":
        api_call(config, "/api/notebook/openNotebook", {"notebook": args.id})
        print("OK")

    elif args.action == "close":
        api_call(config, "/api/notebook/closeNotebook", {"notebook": args.id})
        print("OK")


def cmd_doc(args: argparse.Namespace):
    """文档管理"""
    config = get_config()

    if args.action == "list":
        exclude_filter = build_exclude_filter(config)
        limit = args.limit or 50

        sql = f"""
            SELECT id, content, hpath, box, updated
            FROM blocks
            WHERE type = 'd'{exclude_filter}
            ORDER BY updated DESC
            LIMIT {limit}
        """.strip().replace("\n", " ")

        result = api_call(config, "/api/query/sql", {"stmt": sql})
        if not result:
            print("No documents found")
            return

        for i, r in enumerate(result, 1):
            print(f"[{i}] {r['hpath']}")
            print(f"    id: {r['id']} | updated: {r['updated']}")
            if i < len(result):
                print("\n" + "=" * 60 + "\n")
            else:
                print()

    elif args.action == "create":
        data = {
            "notebook": args.notebook,
            "path": args.path,
            "markdown": args.markdown or "",
        }
        result = api_call(config, "/api/filetree/createDocWithMd", data)
        print(f"Document created")
        print(f"    id: {result}")
        print(f"    notebook: {args.notebook}")
        print(f"    path: {args.path}")

    elif args.action == "rename":
        data = {"id": args.id, "title": args.title}
        api_call(config, "/api/filetree/renameDocByID", data)
        print("OK")

    elif args.action == "delete":
        if not args.force:
            # 先获取文档信息
            try:
                hpath = api_call(config, "/api/filetree/getHPathByID", {"id": args.id})
                print(json.dumps({
                    "error": f"Use --force to confirm deletion",
                    "target": {"id": args.id, "hpath": hpath},
                    "code": 1
                }, ensure_ascii=False))
            except:
                print(f"Error: Document not found", file=sys.stderr)
            sys.exit(1)
        api_call(config, "/api/filetree/removeDocByID", {"id": args.id})
        print(f"OK (deleted: {args.id})")

    elif args.action == "move":
        data = {
            "fromIDs": args.from_ids.split(","),
            "toID": args.to_id,
        }
        api_call(config, "/api/filetree/moveDocsByID", data)
        print("OK")

    elif args.action == "get-path":
        result = api_call(config, "/api/filetree/getHPathByID", {"id": args.id})
        print(f"Document path: {result}")
        print(f"    id: {args.id}")

    elif args.action == "get-content":
        result = api_call(config, "/api/export/exportMdContent", {"id": args.id})
        print(json.dumps({
            "id": args.id,
            "hpath": result.get("hpath"),
            "content": result.get("content")
        }, ensure_ascii=False))


def cmd_block(args: argparse.Namespace):
    """块管理"""
    config = get_config()

    if args.action == "insert":
        data = {
            "dataType": args.data_type or "markdown",
            "data": args.data,
        }
        # 位置锚点
        if args.next_id:
            data["nextID"] = args.next_id
        elif args.previous_id:
            data["previousID"] = args.previous_id
        elif args.parent_id:
            data["parentID"] = args.parent_id
        else:
            print("Error: Must specify next_id, previous_id, or parent_id", file=sys.stderr)
            sys.exit(1)

        result = api_call(config, "/api/block/insertBlock", data)
        # 返回新块的 ID
        if result and len(result) > 0:
            ops = result[0].get("doOperations", [])
            if ops:
                new_id = ops[0].get("id")
                print(f"Block inserted")
                print(f"    id: {new_id}")

    elif args.action == "prepend":
        data = {
            "dataType": args.data_type or "markdown",
            "data": args.data,
            "parentID": args.parent_id,
        }
        result = api_call(config, "/api/block/prependBlock", data)
        if result and len(result) > 0:
            ops = result[0].get("doOperations", [])
            if ops:
                new_id = ops[0].get("id")
                print(f"Block prepended")
                print(f"    id: {new_id}")

    elif args.action == "append":
        data = {
            "dataType": args.data_type or "markdown",
            "data": args.data,
            "parentID": args.parent_id,
        }
        result = api_call(config, "/api/block/appendBlock", data)
        if result and len(result) > 0:
            ops = result[0].get("doOperations", [])
            if ops:
                new_id = ops[0].get("id")
                print(f"Block appended")
                print(f"    id: {new_id}")

    elif args.action == "update":
        data = {
            "dataType": args.data_type or "markdown",
            "data": args.data,
            "id": args.id,
        }
        api_call(config, "/api/block/updateBlock", data)
        print("OK")

    elif args.action == "delete":
        if not args.force:
            # 先获取块信息
            try:
                result = api_call(config, "/api/block/getBlockKramdown", {"id": args.id})
                content = result.get("kramdown", "")[:100]
                print(json.dumps({
                    "error": f"Use --force to confirm deletion",
                    "target": {"id": args.id, "content_preview": content},
                    "code": 1
                }, ensure_ascii=False))
            except:
                print(f"Error: Block not found", file=sys.stderr)
            sys.exit(1)
        api_call(config, "/api/block/deleteBlock", {"id": args.id})
        print(f"OK (deleted: {args.id})")

    elif args.action == "move":
        data = {"id": args.id}
        if args.previous_id:
            data["previousID"] = args.previous_id
        if args.parent_id:
            data["parentID"] = args.parent_id
        if not args.previous_id and not args.parent_id:
            print("Error: Must specify previous_id or parent_id", file=sys.stderr)
            sys.exit(1)
        api_call(config, "/api/block/moveBlock", data)
        print("OK")

    elif args.action == "get":
        result = api_call(config, "/api/block/getBlockKramdown", {"id": args.id})
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "children":
        result = api_call(config, "/api/block/getChildBlocks", {"id": args.id})
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "assets":
        # 获取块的 kramdown 内容，提取其中的资源链接
        result = api_call(config, "/api/block/getBlockKramdown", {"id": args.id})
        kramdown = result.get("kramdown", "")

        # 提取资源链接（图片、附件等）
        # 匹配 assets/xxx 格式的链接
        import re
        assets = []
        for match in re.finditer(r"assets/([^\s\)]+)", kramdown):
            asset_path = match.group(0)
            # 判断资源类型
            ext = Path(asset_path).suffix.lower()
            asset_type = "image" if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg") else "file"
            assets.append({
                "path": asset_path,
                "type": asset_type
            })

        if assets:
            print(json.dumps(assets, indent=2, ensure_ascii=False))
        else:
            print("[]")


def cmd_attr(args: argparse.Namespace):
    """属性管理"""
    config = get_config()

    # 思源内建属性（不需要 custom- 前缀）
    BUILTIN_ATTRS = {
        "tags", "name", "alias", "memo", "bookmark",
        "fold", "heading", "id", "type", "content",
        "markdown", "created", "updated", "sort"
    }

    if args.action == "get":
        result = api_call(config, "/api/attr/getBlockAttrs", {"id": args.id})
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "set":
        # 解析 KEY=VALUE 格式
        attrs = {}
        for attr in args.attrs:
            if "=" in attr:
                key, value = attr.split("=", 1)
                # 内建属性不需要 custom- 前缀
                if key not in BUILTIN_ATTRS and not key.startswith("custom-"):
                    key = "custom-" + key
                attrs[key] = value
        if not attrs:
            print("Error: Attributes required (format: KEY=VALUE)", file=sys.stderr)
            sys.exit(1)
        data = {"id": args.id, "attrs": attrs}
        api_call(config, "/api/attr/setBlockAttrs", data)
        print("OK")


def cmd_file(args: argparse.Namespace):
    """文件操作"""
    config = get_config()

    if args.action == "get":
        # 直接返回文件内容
        url = f"{config['endpoint']}/api/file/getFile?path={urllib.parse.quote(args.path)}"
        headers = {}
        if config["token"]:
            headers["Authorization"] = f"Token {config['token']}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status == 200:
                    print(resp.read().decode("utf-8"))
                else:
                    print(json.dumps({"error": f"HTTP {resp.status}", "code": resp.status}))
                    sys.exit(1)
        except Exception as e:
            print(json.dumps({"error": str(e), "code": -1}))
            sys.exit(1)

    elif args.action == "put":
        # multipart/form-data 上传
        boundary = f"----SiYuanBoundary{int(time.time())}"
        lines = []
        lines.append(f"--{boundary}")
        lines.append(f'Content-Disposition: form-data; name="path"')
        lines.append("")
        lines.append(args.path)
        lines.append(f"--{boundary}")
        lines.append(f'Content-Disposition: form-data; name="file"; filename="{Path(args.file).name}"')
        lines.append("Content-Type: application/octet-stream")
        lines.append("")
        with open(args.file, "rb") as f:
            file_content = f.read()
        body = "\r\n".join(lines).encode("utf-8") + b"\r\n" + file_content + f"\r\n--{boundary}--\r\n".encode("utf-8")

        url = f"{config['endpoint']}/api/file/putFile"
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        if config["token"]:
            headers["Authorization"] = f"Token {config['token']}"

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                result = json.load(resp)
                if result.get("code", 0) != 0:
                    print(json.dumps({"error": result.get("msg", "Unknown error"), "code": result.get("code")}))
                    sys.exit(1)
                print("OK")
        except Exception as e:
            print(json.dumps({"error": str(e), "code": -1}))
            sys.exit(1)

    elif args.action == "delete":
        if not args.force:
            print(json.dumps({"error": f"Use --force to confirm deletion", "target": args.path, "code": 1}), file=sys.stderr)
            sys.exit(1)
        api_call(config, "/api/file/removeFile", {"path": args.path})
        print("OK")

    elif args.action == "rename":
        data = {"path": args.path, "newPath": args.new_path}
        api_call(config, "/api/file/renameFile", data)
        print("OK")

    elif args.action == "ls":
        result = api_call(config, "/api/file/readDir", {"path": args.path})
        print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_export(args: argparse.Namespace):
    """导出"""
    config = get_config()

    if args.action == "md":
        result = api_call(config, "/api/export/exportMdContent", {"id": args.id})
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "resources":
        paths = args.paths.split(",") if args.paths else []
        data = {"paths": paths}
        if args.name:
            data["name"] = args.name
        result = api_call(config, "/api/export/exportResources", data)
        print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_asset(args: argparse.Namespace):
    """资源文件管理"""
    config = get_config()

    if args.action == "get-path":
        # 获取 workspace 目录
        workspace_info = api_call(config, "/api/system/getWorkspaceInfo", {})
        workspace_dir = workspace_info.get("workspaceDir", "")

        # 获取块的 kramdown 内容，提取资源路径
        result = api_call(config, "/api/block/getBlockKramdown", {"id": args.id})
        kramdown = result.get("kramdown", "")

        import re

        if args.asset:
            # 查找指定的资源文件
            pattern = args.asset
            if pattern.startswith("assets/"):
                pattern = pattern[8:]  # 移除 assets/ 前缀
            if pattern in kramdown or f"assets/{pattern}" in kramdown:
                # 拼接绝对路径: workspaceDir/data/assets/xxx
                asset_name = pattern.split("/")[-1] if "/" in pattern else pattern
                abs_path = str(Path(workspace_dir) / "data" / "assets" / asset_name)
                # 统一使用 / 作为路径分隔符
                abs_path = abs_path.replace("\\", "/")
                print(abs_path)
            else:
                print(json.dumps({"error": "Asset not found in block"}, ensure_ascii=False), file=sys.stderr)
                sys.exit(1)
        else:
            # 列出所有资源的绝对路径
            for match in re.finditer(r"assets/([^\s\)]+)", kramdown):
                asset_name = match.group(1)
                abs_path = str(Path(workspace_dir) / "data" / "assets" / asset_name)
                # 统一使用 / 作为路径分隔符
                abs_path = abs_path.replace("\\", "/")
                print(abs_path)


def cmd_system(args: argparse.Namespace):
    """系统命令"""
    config = get_config()

    if args.action == "version":
        result = api_call(config, "/api/system/version", {})
        print(f"SiYuan Version: {result}")

    elif args.action == "boot":
        result = api_call(config, "/api/system/bootProgress", {})
        print(f"Boot Progress: {result}")


def cmd_sync(args: argparse.Namespace):
    """同步管理"""
    config = get_config()

    if args.action == "status":
        try:
            # 获取服务器版本和当前时间
            server_time_ms = get_server_time(config)
            from datetime import datetime
            server_time = datetime.fromtimestamp(server_time_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")

            print(f"Server time: {server_time}")

            # 获取同步信息
            sync_result = api_call(config, "/api/sync/getSyncInfo", {})
            kernel = sync_result.get("kernel", "N/A")
            stat = sync_result.get("stat", "No sync info")
            synced = sync_result.get("synced", 0)
            synced_time = datetime.fromtimestamp(synced / 1000).strftime("%Y-%m-%d %H:%M:%S") if synced else "Never"

            print(f"Kernel: {kernel}")
            # 格式化同步状态：移除 HTML 标签并统一缩进
            stat_clean = stat.replace("<br>", "\n").replace("&emsp;", "").replace("<br/>", "\n").strip()
            for line in stat_clean.split("\n"):
                print(f"  {line}")
            print(f"Last synced: {synced_time}")
        except Exception as e:
            print(f"SiYuan Status: offline ({e})")

    elif args.action == "perform":
        result = api_call(config, "/api/sync/performSync", {})
        print("Sync triggered (async execution)")


def cmd_format(args: argparse.Namespace):
    """格式化和排版"""
    config = get_config()

    if args.action == "auto-space":
        # 获取块信息，找到 rootID（文档 ID）
        try:
            block_info = api_call(config, "/api/block/getBlockInfo", {"id": args.id})
            doc_id = block_info.get("rootID", args.id)
        except Exception:
            # 如果获取失败，假设传入的就是文档 ID
            doc_id = args.id

        # 对文档 ID 应用优化排版
        data = {"id": doc_id}
        api_call(config, "/api/format/autoSpace", data)
        print(f"OK (auto-space applied to: {doc_id})")


def cmd_refs(args: argparse.Namespace):
    """引用查询（反链检索）"""
    config = get_config()
    exclude_filter = build_exclude_filter(config)

    limit = args.limit or 20

    # 查询引用了指定文档/块的所有块（在 markdown 字段中）
    sql = f"""
        SELECT id, markdown, type, hpath, box, updated, root_id
        FROM blocks
        WHERE markdown LIKE '%(({args.id}%'
        {exclude_filter}
        ORDER BY updated DESC
        LIMIT {limit}
    """.strip().replace("\n", " ")

    result = api_call(config, "/api/query/sql", {"stmt": sql})
    if not result:
        print("No references found")
        return

    for i, r in enumerate(result, 1):
        print(f"[{i}] {r['hpath']}")
        print(f"    id: {r['id']} | type: {r['type']} | updated: {r['updated']}")
        markdown = r.get("markdown", "")
        # 高亮引用部分
        if f"(({args.id}" in markdown:
            # 提取包含引用的行
            lines = markdown.split('\n')
            for line in lines:
                if f"(({args.id}" in line:
                    print(f"    ref: {line.strip()[:200]}")
                    break
        if i < len(result):
            print("\n" + "=" * 60 + "\n")
        else:
            print()


def cmd_tag(args: argparse.Namespace):
    """标签管理"""
    config = get_config()

    if args.action == "list":
        data = {"sort": args.sort, "app": "siyuan", "ignoreMaxListHint": True}
        result = api_call(config, "/api/tag/getTag", data)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "search":
        data = {"k": args.keyword}
        result = api_call(config, "/api/search/searchTag", data)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "rename":
        data = {"oldLabel": args.old_label, "newLabel": args.new_label}
        api_call(config, "/api/tag/renameTag", data)
        print(f"OK (renamed: {args.old_label} -> {args.new_label})")

    elif args.action == "remove":
        data = {"label": args.label}
        api_call(config, "/api/tag/removeTag", data)
        print(f"OK (removed: {args.label})")


# =============================================================================
# Embedding 索引命令（调用 search_embed.py）
# =============================================================================

def cmd_index(args: argparse.Namespace):
    """索引管理"""
    try:
        from search_embed import build_index, get_index_status
    except ImportError:
        print(json.dumps({
            "error": "Embedding feature requires dependencies: uv sync",
            "code": 1
        }))
        sys.exit(1)

    config = get_config()

    if args.action == "build":
        # 获取服务器时间
        server_time = get_server_time(config)

        # 获取所有文档（包含内容）
        exclude_filter = build_exclude_filter(config)
        sql = f"""
            SELECT id, hpath, box, updated
            FROM blocks
            WHERE type = 'd'{exclude_filter}
            ORDER BY updated DESC
        """.strip().replace("\n", " ")

        docs = api_call(config, "/api/query/sql", {"stmt": sql})
        if not docs:
            print("No documents found")
            return

        # 为每个文档获取完整内容
        docs_data = []
        for doc in docs:
            doc_id = doc["id"]
            exported = api_call(config, "/api/export/exportMdContent", {"id": doc_id})
            docs_data.append({
                "id": doc["id"],
                "hpath": doc["hpath"],
                "box": doc["box"],
                "updated": doc["updated"],
                "content": exported.get("content", ""),
            })

        # 准备 OpenAI 配置
        openai_config = {
            "openai_api_key": config["openai_api_key"],
            "openai_base_url": config["openai_base_url"],
            "openai_model": config["openai_model"],
            "openai_embedding_dimension": config.get("openai_embedding_dimension", ""),
        }

        # 调用 build_index
        build_index(openai_config, docs_data, force=args.force, server_time=server_time)
        print("OK")

    elif args.action == "status":
        status = get_index_status()
        print(json.dumps(status, indent=2, ensure_ascii=False))


def cmd_search_semantic(args: argparse.Namespace):
    """语义搜索（Embedding + FAISS）"""
    try:
        from search_embed import semantic_search
    except ImportError:
        print(json.dumps({
            "error": "Embedding feature requires dependencies: uv sync",
            "code": 1
        }))
        sys.exit(1)

    config = get_config()

    # 准备 OpenAI 配置
    openai_config = {
        "openai_api_key": config["openai_api_key"],
        "openai_base_url": config["openai_base_url"],
        "openai_model": config["openai_model"],
        "openai_embedding_dimension": config.get("openai_embedding_dimension", ""),
    }

    limit = args.limit or 8
    results = semantic_search(openai_config, args.terms, limit)

    # 检查是否是错误
    if results and isinstance(results, list) and results[0].get("error"):
        print(f"Error: {results[0]['error']}", file=sys.stderr)
        sys.exit(1)

    # 友好的输出格式
    for i, r in enumerate(results):
        print(f"[{r['rank']}] {r['hpath']}")
        print(f"    id: {r['doc_id']} | chunk: {r['chunk_index']} | score: {r['score']:.3f}")
        print(r["content"])
        if i < len(results):
            print("\n" + "=" * 60 + "\n")
        else:
            print()


# =============================================================================
# 主入口
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="SiYuan Note CLI Tool")
    parser.set_defaults(func=None)

    # config 命令
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    config_parser = subparsers.add_parser("config", help="Configuration management")
    config_sub = config_parser.add_subparsers(dest="action", required=True)

    config_set = config_sub.add_parser("set", help="Set configuration")
    config_set.add_argument("--host", help="SiYuan host")
    config_set.add_argument("--port", help="SiYuan port")
    config_set.add_argument("--token", help="API Token")

    config_show = config_sub.add_parser("show", help="Show configuration")

    # search 命令
    search_parser = subparsers.add_parser("search", help="Search")
    search_sub = search_parser.add_subparsers(dest="action", required=True)

    search_keyword = search_sub.add_parser("keyword", help="Keyword search")
    search_keyword.add_argument("terms", help="Search terms (space separated)")
    search_keyword.add_argument("--type", help="Block type filter (d=document, h=heading, p=paragraph, etc.)")
    search_keyword.add_argument("--limit", type=int, help="Result limit")

    search_doc = search_sub.add_parser("doc", help="Full-text search")
    search_doc.add_argument("terms", help="Search terms")
    search_doc.add_argument("--limit", type=int, help="Result limit")

    search_recent = search_sub.add_parser("recent", help="Recently modified")
    search_recent.add_argument("--limit", type=int, help="Result limit")

    search_semantic = search_sub.add_parser("semantic", help="Semantic search")
    search_semantic.add_argument("terms", help="Search terms")
    search_semantic.add_argument("--limit", type=int, help="Result limit")

    # sql 命令
    sql_parser = subparsers.add_parser("sql", help="Raw SQL query")
    sql_parser.add_argument("stmt", help="SQL statement")

    # notebook 命令
    notebook_parser = subparsers.add_parser("notebook", help="Notebook management")
    notebook_sub = notebook_parser.add_subparsers(dest="action", required=True)

    notebook_sub.add_parser("list", help="List notebooks")

    nb_create = notebook_sub.add_parser("create", help="Create notebook")
    nb_create.add_argument("--name", required=True, help="Notebook name")

    nb_rename = notebook_sub.add_parser("rename", help="Rename notebook")
    nb_rename.add_argument("--id", required=True, help="Notebook ID")
    nb_rename.add_argument("--name", required=True, help="New name")

    nb_delete = notebook_sub.add_parser("delete", help="Delete notebook")
    nb_delete.add_argument("--id", required=True, help="Notebook ID")
    nb_delete.add_argument("--force", action="store_true", help="Force deletion")

    nb_open = notebook_sub.add_parser("open", help="Open notebook")
    nb_open.add_argument("--id", required=True, help="Notebook ID")

    nb_close = notebook_sub.add_parser("close", help="Close notebook")
    nb_close.add_argument("--id", required=True, help="Notebook ID")

    # doc 命令
    doc_parser = subparsers.add_parser("doc", help="Document management")
    doc_sub = doc_parser.add_subparsers(dest="action", required=True)

    doc_list = doc_sub.add_parser("list", help="List all documents")
    doc_list.add_argument("--limit", type=int, help="Result limit (default: 50)")

    doc_create = doc_sub.add_parser("create", help="Create document")
    doc_create.add_argument("--notebook", required=True, help="Notebook ID")
    doc_create.add_argument("--path", required=True, help="Document path (e.g. /foo/bar)")
    doc_create.add_argument("--markdown", default="", help="Markdown content")

    doc_rename = doc_sub.add_parser("rename", help="Rename document")
    doc_rename.add_argument("--id", required=True, help="Document ID")
    doc_rename.add_argument("--title", required=True, help="New title")

    doc_delete = doc_sub.add_parser("delete", help="Delete document")
    doc_delete.add_argument("--id", required=True, help="Document ID")
    doc_delete.add_argument("--force", action="store_true", help="Force deletion")

    doc_move = doc_sub.add_parser("move", help="Move document")
    doc_move.add_argument("--from-ids", required=True, help="Source document IDs (comma separated)")
    doc_move.add_argument("--to-id", required=True, help="Target document/notebook ID")

    doc_get = doc_sub.add_parser("get-path", help="Get document path")
    doc_get.add_argument("--id", required=True, help="Document ID")

    doc_content = doc_sub.add_parser("get-content", help="Get document full content")
    doc_content.add_argument("--id", required=True, help="Document ID")

    # block 命令
    block_parser = subparsers.add_parser("block", help="Block management")
    block_sub = block_parser.add_subparsers(dest="action", required=True)

    for cmd_name in ("insert", "prepend", "append"):
        cmd = block_sub.add_parser(cmd_name, help=f"{cmd_name} block")
        cmd.add_argument("--data", required=True, help="Block content")
        cmd.add_argument("--data-type", choices=["markdown", "dom"], default="markdown", help="Data type")
        if cmd_name == "insert":
            cmd.add_argument("--next-id", help="Next block ID")
            cmd.add_argument("--previous-id", help="Previous block ID")
            cmd.add_argument("--parent-id", help="Parent block ID")
        else:
            cmd.add_argument("--parent-id", required=True, help="Parent block ID")

    block_update = block_sub.add_parser("update", help="Update block")
    block_update.add_argument("--id", required=True, help="Block ID")
    block_update.add_argument("--data", required=True, help="New content")
    block_update.add_argument("--data-type", choices=["markdown", "dom"], default="markdown")

    block_delete = block_sub.add_parser("delete", help="Delete block")
    block_delete.add_argument("--id", required=True, help="Block ID")
    block_delete.add_argument("--force", action="store_true", help="Force deletion")

    block_move = block_sub.add_parser("move", help="Move block")
    block_move.add_argument("--id", required=True, help="Block ID")
    block_move.add_argument("--previous-id", help="Previous block ID")
    block_move.add_argument("--parent-id", help="Parent block ID")

    block_get = block_sub.add_parser("get", help="Get block content")
    block_get.add_argument("--id", required=True, help="Block ID")

    block_children = block_sub.add_parser("children", help="Get child blocks")
    block_children.add_argument("--id", required=True, help="Parent block ID")

    block_assets = block_sub.add_parser("assets", help="Extract assets from block")
    block_assets.add_argument("--id", required=True, help="Block ID")

    # attr 命令
    attr_parser = subparsers.add_parser("attr", help="Attribute management")
    attr_sub = attr_parser.add_subparsers(dest="action", required=True)

    attr_get = attr_sub.add_parser("get", help="Get attributes")
    attr_get.add_argument("--id", required=True, help="Block ID")

    attr_set = attr_sub.add_parser("set", help="Set attributes")
    attr_set.add_argument("--id", required=True, help="Block ID")
    attr_set.add_argument("--attrs", nargs="+", required=True, help="Attributes (KEY=VALUE format)")

    # refs 命令
    refs_parser = subparsers.add_parser("refs", help="Reference search (backlinks)")
    refs_parser.add_argument("--id", required=True, help="Target document/block ID")
    refs_parser.add_argument("--limit", type=int, help="Result limit (default: 20)")

    # file 命令
    file_parser = subparsers.add_parser("file", help="File operations")
    file_sub = file_parser.add_subparsers(dest="action", required=True)

    file_get = file_sub.add_parser("get", help="Get file")
    file_get.add_argument("--path", required=True, help="File path")

    file_put = file_sub.add_parser("put", help="Upload file")
    file_put.add_argument("--path", required=True, help="Target path")
    file_put.add_argument("--file", required=True, help="Local file path")

    file_delete = file_sub.add_parser("delete", help="Delete file")
    file_delete.add_argument("--path", required=True, help="File path")
    file_delete.add_argument("--force", action="store_true", help="Force deletion")

    file_rename = file_sub.add_parser("rename", help="Rename file")
    file_rename.add_argument("--path", required=True, help="Old path")
    file_rename.add_argument("--new-path", required=True, help="New path")

    file_ls = file_sub.add_parser("ls", help="List directory")
    file_ls.add_argument("--path", required=True, help="Directory path")

    # export 命令
    export_parser = subparsers.add_parser("export", help="Export")
    export_sub = export_parser.add_subparsers(dest="action", required=True)

    export_md = export_sub.add_parser("md", help="Export Markdown")
    export_md.add_argument("--id", required=True, help="Document ID")

    export_res = export_sub.add_parser("resources", help="Export resources")
    export_res.add_argument("--paths", help="Path list (comma separated)")
    export_res.add_argument("--name", help="Export file name")

    # asset 命令
    asset_parser = subparsers.add_parser("asset", help="Asset management")
    asset_sub = asset_parser.add_subparsers(dest="action", required=True)

    asset_get_path = asset_sub.add_parser("get-path", help="Get asset local path")
    asset_get_path.add_argument("--id", required=True, help="Block ID")
    asset_get_path.add_argument("--asset", help="Asset file name (e.g. assets/image.png)")

    # system 命令
    system_parser = subparsers.add_parser("system", help="System commands")
    system_sub = system_parser.add_subparsers(dest="action", required=True)

    system_sub.add_parser("version", help="Get version")
    system_sub.add_parser("boot", help="Boot progress")

    # sync 命令
    sync_parser = subparsers.add_parser("sync", help="Sync management")
    sync_sub = sync_parser.add_subparsers(dest="action", required=True)
    sync_sub.add_parser("status", help="Get status and sync info")
    sync_sub.add_parser("perform", help="Perform sync")

    # index 命令
    index_parser = subparsers.add_parser("index", help="Index management")
    index_sub = index_parser.add_subparsers(dest="action", required=True)
    index_build = index_sub.add_parser("build", help="Build index (incremental)")
    index_build.add_argument("--force", action="store_true", help="Force full rebuild")
    index_sub.add_parser("status", help="Index status")

    # format 命令
    format_parser = subparsers.add_parser("format", help="Format and layout")
    format_sub = format_parser.add_subparsers(dest="action", required=True)

    format_auto_space = format_sub.add_parser("auto-space", help="Apply auto-spacing (optimize layout)")
    format_auto_space.add_argument("--id", required=True, help="Document or block ID")

    # tag 命令
    tag_parser = subparsers.add_parser("tag", help="Tag management")
    tag_sub = tag_parser.add_subparsers(dest="action", required=True)

    tag_list = tag_sub.add_parser("list", help="List all tags")
    tag_list.add_argument("--sort", type=int, default=0, help="Sort method (0=alphabet, 1=usage)")

    tag_search = tag_sub.add_parser("search", help="Search tags (for auto-completion)")
    tag_search.add_argument("keyword", help="Search keyword")

    tag_rename = tag_sub.add_parser("rename", help="Rename tag")
    tag_rename.add_argument("--old-label", required=True, help="Old tag name")
    tag_rename.add_argument("--new-label", required=True, help="New tag name")

    tag_remove = tag_sub.add_parser("remove", help="Remove tag")
    tag_remove.add_argument("label", help="Tag name to remove")

    args = parser.parse_args()

    # 命令分发
    if args.command == "config":
        cmd_config(args)
    elif args.command == "search":
        if args.action == "keyword":
            cmd_search_keyword(args)
        elif args.action == "doc":
            cmd_search_doc(args)
        elif args.action == "recent":
            cmd_search_recent(args)
        elif args.action == "semantic":
            cmd_search_semantic(args)
    elif args.command == "sql":
        cmd_sql(args)
    elif args.command == "notebook":
        cmd_notebook(args)
    elif args.command == "doc":
        cmd_doc(args)
    elif args.command == "block":
        cmd_block(args)
    elif args.command == "attr":
        cmd_attr(args)
    elif args.command == "refs":
        cmd_refs(args)
    elif args.command == "file":
        cmd_file(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "asset":
        cmd_asset(args)
    elif args.command == "system":
        cmd_system(args)
    elif args.command == "sync":
        cmd_sync(args)
    elif args.command == "index":
        cmd_index(args)
    elif args.command == "format":
        cmd_format(args)
    elif args.command == "tag":
        cmd_tag(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
