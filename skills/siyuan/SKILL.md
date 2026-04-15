---
name: siyuan
description: "管理思源笔记 (SiYuan Note) - 搜索、创建、编辑、删除笔记、笔记本和块。当用户提到思源、思源笔记、SiYuan、或需要管理本地思源笔记内容时触发。支持关键字搜索、语义搜索（OpenAI Embedding + FAISS）、文档导出、块级 CRUD、笔记本管理和文件操作。"
---

# 思源笔记 Skill

本 skill 帮助你管理本地的思源笔记，提供搜索、创建、编辑和删除功能。

## 前置条件

1. **Python 3.9+** 和 **uv** 已安装
2. **思源笔记** 正在运行（默认 `http://127.0.0.1:6806`）
3. **.env 文件** 已配置（见下方配置说明）

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置（复制示例文件并编辑）
cp .env.example .env
# 编辑 .env，填入 SIYUAN_API_TOKEN 等

# 3. 测试连通性
uv run python scripts/siyuan.py system version

# 4. 搜索笔记
uv run python scripts/siyuan.py search keyword "测试"
```

## 配置说明

在项目目录创建 `.env` 文件：

```env
# 思源连接配置
SIYUAN_HOST=127.0.0.1
SIYUAN_PORT=6806
SIYUAN_API_TOKEN=your-token-here

# OpenAI Embedding 配置（语义搜索需要）
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=              # 可选，自定义端点
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# 排除规则（API 层面过滤）
SIYUAN_EXCLUDE_NOTEBOOKS=     # 排除的笔记本 ID（逗号分隔）
SIYUAN_EXCLUDE_PATHS=/daily note,/templates  # 排除的路径前缀
```

获取 API Token：思源笔记 → 设置 → 关于 → API Token

## 任务路由表

| 任务 | 命令 |
|------|------|
| **搜索 - 关键字** | `search keyword` |
| **搜索 - 全文分析** | `search doc` |
| **搜索 - 语义** | `search semantic`（自动增量更新索引） |
| **搜索 - 最近** | `search recent` |
| **笔记本管理** | `notebook list/create/rename/delete/open/close` |
| **文档管理** | `doc create/rename/delete/move/get-path/get-content` |
| **块管理** | `block insert/prepend/append/update/delete/move/get/children` |
| **属性管理** | `attr get/set` |
| **文件操作** | `file get/put/delete/rename/ls` |
| **导出** | `export md/resources` |
| **格式化** | `format auto-space`（优化排版） |

## 搜索工作流

**推荐策略**：优先使用语义搜索（`search semantic`），它能理解意图而非仅仅匹配关键词。

### L1 语义搜索（`search semantic`）⭐ 推荐

使用 OpenAI Embedding + FAISS，按块索引，理解搜索意图：

**向量化策略**：
- **粒度**：按块索引（每块 1000 字符）
- **重叠**：20% 重叠（200 字符）
- **阈值**：只返回相似度 > 0.5 的结果
- **返回**：块内容 + doc_id + hpath + chunk_index

搜索后可用 `doc get-content --id <doc_id>` 获取完整文档。

```bash
# 首次使用或需要全量重建时
uv run python scripts/siyuan.py index build --force

# 日常使用：增量更新（只处理变化的文档）
uv run python scripts/siyuan.py index build

# 搜索（默认返回 8 条）
uv run python scripts/siyuan.py search semantic "项目进度和里程碑"

# 获取完整文档
uv run python scripts/siyuan.py doc get-content --id <doc_id>
```

### L2 关键字搜索（`search keyword`）

快速关键词匹配，返回块级结果（默认 10 条）：

```bash
uv run python scripts/siyuan.py search keyword "项目管理"
uv run python scripts/siyuan.py search keyword "项目 管理" --type d
```

### L3 全文搜索（`search doc`）

导出完整文档内容，Claude 在内存中分析（默认 5 条）：

```bash
uv run python scripts/siyuan.py search doc "项目进度"
```

### L4 最近修改（`search recent`）

查看最近修改的文档（默认 10 条）：

```bash
uv run python scripts/siyuan.py search recent
```

### L5 原始 SQL（`sql`）

复杂自定义查询：

```bash
uv run python scripts/siyuan.py sql "SELECT id, hpath FROM blocks WHERE type = 'd' AND updated > 1704067200000 ORDER BY updated DESC LIMIT 10"
```

## 编辑工作流

**核心理念**：思源笔记基于**块**（Block）设计，所有内容（段落、标题、列表项、代码块等）都是独立的块。**关键字搜索**直接返回块 ID，可以直接精准更新该块。

### 块级编辑流程

```bash
# 1. search keyword → 找到精准的块 id
uv run python scripts/siyuan.py search keyword "要修改的内容" --type p

# 输出：
# [1] /文档路径
#     id: 20260410155541-8dqsqvr | type: p
#     doc: 20260410155541-xxxxxx | box: 20231211105622-ztp25z5
#     块内容...

# 2. 直接用块 id 更新
uv run python scripts/siyuan.py block update --id 20260410155541-8dqsqvr --data "新内容"
```

**就这么简单**：关键字搜索 → 找到 `id` → 更新

### 块类型过滤

- `--type p` = 仅段落块
- `--type h` = 仅标题块
- `--type d` = 仅文档块
- `--type c` = 仅代码块

### 查看文档结构（可选）

如果需要查看块所在的文档结构，使用搜索结果中的 `doc`（文档 ID）：

```bash
uv run python scripts/siyuan.py block children --id <doc_id>
```

注意：`block children` 查看的是块的子块，代码块、段落等叶子节点没有子块。

### 创建文档

创建文档也是创建块的过程：

```bash
uv run python scripts/siyuan.py doc create \
  --notebook 20210817205410-2kvfpfn \
  --path "/test/doc" \
  --markdown "# 标题\n\n内容"
```

### 插入块

在现有块之间插入新块：

```bash
# 在指定块后插入
uv run python scripts/siyuan.py block insert \
  --data "新段落内容" \
  --previous-id <block_id>

# 作为子块追加
uv run python scripts/siyuan.py block append \
  --data "子内容" \
  --parent-id <parent_block_id>

# 作为第一个子块插入
uv run python scripts/siyuan.py block prepend \
  --data "首个子内容" \
  --parent-id <parent_block_id>
```

## 删除工作流

**重要**：删除操作需要用户确认！

### 删除前先搜索确认

```bash
# 搜索确认目标
uv run python scripts/siyuan.py search keyword "要删除的文档名"
```

### 删除时显示确认信息

CLI 的 delete 命令没有 `--force` 时会显示目标信息并退出：

```bash
uv run python scripts/siyuan.py doc delete --id 20210902210113-0avi12f
# 输出：{"error": "删除文档需要 --force 确认", "target": {...}}

# 确认后重新执行
uv run python scripts/siyuan.py doc delete --id 20210902210113-0avi12f --force
```

**Claude 规则**：在执行任何带 `--force` 的删除命令前，**必须**向用户显示将要删除的内容并征求确认。

## 同步说明

思源笔记支持同步 API。同步是异步执行的，API 触发后立即返回。

### 同步命令

```bash
# 获取状态和同步信息
uv run python scripts/siyuan.py sync status

# 执行同步（异步）
uv run python scripts/siyuan.py sync perform
```

`sync status` 会显示：
- SiYuan Status: 在线状态和版本
- Server time: 服务器当前时间
- Kernel: 同步密钥 ID
- Sync status: 上传/下载文件数、分块数、字节数
- Last synced: 最后同步时间

### 同步时机

**重要**：在修改思源笔记内容后，应该提示用户备份/同步，在得到用户允许的情况下执行同步：

```bash
# 修改操作完成后
uv run python scripts/siyuan.py sync perform
uv run python scripts/siyuan.py sync status
```

## 命令参考

### 配置

```bash
uv run python scripts/siyuan.py config set --host 127.0.0.1 --port 6806 --token xxx
uv run python scripts/siyuan.py config show
```

### 搜索

```bash
# 关键字搜索
uv run python scripts/siyuan.py search keyword "词1 词2" [--type TYPE] [--limit N]

# 全文搜索
uv run python scripts/siyuan.py search doc "搜索词" [--limit N]

# 语义搜索
uv run python scripts/siyuan.py search semantic "搜索词" [--limit N]

# 最近修改
uv run python scripts/siyuan.py search recent [--limit N]

# 原始 SQL
uv run python scripts/siyuan.py sql "SELECT ... FROM blocks WHERE ..."
```

### 笔记本

```bash
uv run python scripts/siyuan.py notebook list
uv run python scripts/siyuan.py notebook create --name "新笔记本"
uv run python scripts/siyuan.py notebook rename --id xxx --name "新名称"
uv run python scripts/siyuan.py notebook delete --id xxx --force
uv run python scripts/siyuan.py notebook open --id xxx
uv run python scripts/siyuan.py notebook close --id xxx
```

### 文档

```bash
uv run python scripts/siyuan.py doc create --notebook xxx --path "/foo/bar" --markdown "内容"
uv run python scripts/siyuan.py doc rename --id xxx --title "新标题"
uv run python scripts/siyuan.py doc delete --id xxx --force
uv run python scripts/siyuan.py doc move --from-ids id1,id2 --to-id xxx
uv run python scripts/siyuan.py doc get-path --id xxx
uv run python scripts/siyuan.py doc get-content --id xxx
```

### 块

```bash
uv run python scripts/siyuan.py block insert --data "内容" --previous-id xxx
uv run python scripts/siyuan.py block prepend --data "内容" --parent-id xxx
uv run python scripts/siyuan.py block append --data "内容" --parent-id xxx
uv run python scripts/siyuan.py block update --id xxx --data "新内容"
uv run python scripts/siyuan.py block delete --id xxx --force
uv run python scripts/siyuan.py block move --id xxx --parent-id xxx
uv run python scripts/siyuan.py block get --id xxx
uv run python scripts/siyuan.py block children --id xxx
```

### 属性

```bash
uv run python scripts/siyuan.py attr get --id xxx
uv run python scripts/siyuan.py attr set --id xxx --attrs "key1=value1" "key2=value2"
```

### 文件

```bash
uv run python scripts/siyuan.py file get --path /data/assets/file.png
uv run python scripts/siyuan.py file put --path /data/assets/ --file /local/file.png
uv run python scripts/siyuan.py file delete --path /data/assets/file.png --force
uv run python scripts/siyuan.py file rename --path /old/path --new-path /new/path
uv run python scripts/siyuan.py file ls --path /data/assets
```

### 导出

```bash
uv run python scripts/siyuan.py export md --id xxx
uv run python scripts/siyuan.py export resources --paths "/conf,/data" --name backup
```

### 索引

**增量更新**：默认只处理有变化的文档（基于 `updated` 时间戳）

```bash
# 增量更新（默认，只处理变化的文档）
uv run python scripts/siyuan.py index build

# 强制全量重建
uv run python scripts/siyuan.py index build --force

# 查看索引状态
uv run python scripts/siyuan.py index status
```

**向量化策略**：
- 按块索引（每块 1000 字符，20% 重叠）
- 相似度阈值 > 0.5
- 使用 FAISS IndexIDMap 支持增量更新

### 格式化

思源笔记提供**优化排版**功能，自动在中英文之间添加空格、优化标点符号等。

```bash
# 优化排版（自动空格）
uv run python scripts/siyuan.py format auto-space --id <doc_id>
```

**重要**：优化排版功能会修改文档内容，执行前必须征得用户同意。

**使用场景**：
- 创建新文档后
- 修改文档内容后
- 导入外部 Markdown 内容后

## 思源笔记内容块语法

### 核心概念

思源笔记基于**内容块**（Content Block）构建，所有内容都是块，通过排版格式形成块级结构。

### 块引用语法

引用其他内容块，建立知识关联：

```markdown
((id "锚文本"))
```

- `((` - 触发块引用搜索（在所有块类型中搜索）
- `[[` - 仅搜索文档块
- `((ID))` - 使用动态锚文本（跟随定义块内容变化）
- `((ID "静态文本"))` - 使用静态锚文本（固定不变）

**链接方向**：
- 正向链接：当前块引用了哪些其他块
- 反向链接：当前块被哪些其他块引用

### 嵌入块语法

使用 SQL 查询动态汇总内容块：

```markdown
{{ SELECT * FROM blocks WHERE content LIKE '%关键字%' AND type = 'p' }}
```

**常用查询示例**：
- 查询包含关键词的段落：`WHERE content LIKE '%关键词%' AND type = 'p'`
- 查询包含标签的块：`WHERE content LIKE '%#标签#%'`
- 按更新时间排序：`ORDER BY updated DESC`
- 限制结果数量：`LIMIT 10`

### 思源特有元素

- **超级块**：通过缩进将多个块组合成容器
- **引述块**：`> 引用内容`
- **提示块**：
  ```markdown
  > [!NOTE]
  > [!TIP]
  > [!IMPORTANT]
  > [!WARNING]
  > [!CAUTION]
  ```
- **脑图**：
  ````markdown
  ```mindmap
  - 根节点
    - 子节点
  ```
  ````
- **数学公式块**：
  ````markdown
  $$
  \frac{1}{2}
  $$
  ````

### 块类型速查

| 类型码 | 名称 | 说明 |
|--------|------|------|
| `d` | 文档块 | Document |
| `h` | 标题块 | Heading |
| `p` | 段落块 | Paragraph |
| `l` | 列表块 | List |
| `i` | 列表项 | List Item |
| `t` | 表格 | Table |
| `b` | 引述块 | Blockquote |
| `s` | 超级块 | SuperBlock |
| `c` | 代码块 | Code Block |
| `m` | 数学公式 | Math Formula |
| `a` | 音频 | Audio |
| `v` | 视频 | Video |
| `iframe` | 嵌入网页 | IFrame |

### 内容块属性

每个块都可以设置自定义属性（`custom-*` 前缀）：

```bash
# 设置块属性
uv run python scripts/siyuan.py attr set --id <块ID> --attrs "key1=value1" "key2=value2"

# 获取块属性
uv run python scripts/siyuan.py attr get --id <块ID>
```

**常用内置属性**：
- `id` - 块 ID
- `type` - 块类型
- `content` - 块内容
- `updated` - 更新时间戳
- `created` - 创建时间戳

### 创建文档示例

```markdown
# 项目文档

## 概述

这是项目的核心概念说明。

## 关键引用

引用相关文档：((20210808180117-czj9bvb "思源笔记用户指南"))

## 动态汇总

{{ SELECT * FROM blocks WHERE content LIKE '%项目%' AND type = 'p' ORDER BY updated DESC LIMIT 5 }}

## 任务列表

- [x] 完成需求分析
- [ ] 进行技术选型
- [ ] 编写设计文档
```

## 关键规则

1. **块级编辑优先**：思源笔记是块级设计，修改内容时应先精准定位到最小块，然后更新该块
2. **先搜索再操作**：编辑或删除前，先搜索确认目标正确
3. **删除必须确认**：CLI 需 `--force`，Claude 需向用户确认
4. **语义搜索优先级**：有索引用 `search semantic`，否则用 `search doc`
5. **优先用 ID**：文档和块操作优先使用 ID 而非路径
6. **排除过滤自动应用**：所有搜索自动应用排除规则（排除笔记本和路径）
7. **修改后提示同步**：执行修改操作后，应提示用户是否同步，在用户允许后执行 `sync perform`
8. **优化排版需征得同意**：创建或修改笔记后，应主动询问用户是否需要优化排版（`format auto-space`），仅在用户同意后执行
