# 思源笔记 Skill

管理思源笔记 (SiYuan Note) 的 Claude Code Skill，支持搜索、创建、编辑、删除笔记、笔记本和块。

思源笔记基于**块**（Block）设计，所有内容（段落、标题、列表、代码等）都是独立的块，本工具支持精准的块级操作。

## 功能特性

### 搜索
- **关键字搜索** - SQL LIKE 模糊匹配
- **全文搜索** - 导出完整文档内容分析
- **语义搜索** - OpenAI Embedding + FAISS 向量搜索（推荐）
- **最近修改** - 按更新时间排序
- **原始 SQL** - 自定义查询

### 笔记本管理
- 列出 / 创建 / 重命名 / 删除 / 打开 / 关闭

### 文档管理
- 创建 / 重命名 / 删除 / 移动
- 获取路径 / 获取完整内容

### 块管理
- 插入 / 前置插入 / 后置插入 / 更新 / 删除 / 移动
- 获取块内容 / 获取子块 / 提取资源文件

### 属性管理
- 获取 / 设置块属性

### 文件操作
- 获取 / 上传 / 删除 / 重命名 / 列出目录

### 资源管理
- 获取资源文件本地路径
- 提取块中的所有资源

### 引用查询
- 查询引用了指定文档/块的所有块（反链检索）

### 导出
- 导出 Markdown / 导出资源文件

### 系统命令
- 获取版本 / 启动进度 / 获取工作空间信息

### 同步管理
- 获取同步状态 / 执行同步

### 索引管理
- 构建索引（支持增量更新）/ 查看索引状态

## 快速开始

```bash
# 1. 安装 Skill
ln -s $(pwd)/skills/siyuan ~/.claude/skills/siyuan

# 2. 安装依赖
cd skills/siyuan
uv sync

# 3. 配置环境变量
cp .env.example skills/siyuan/.env
# 编辑 skills/siyuan/.env，填入 SIYUAN_API_TOKEN 等
```

## 配置说明

在 `skills/siyuan/.env` 文件中配置：

```env
# 思源连接配置
SIYUAN_HOST=127.0.0.1
SIYUAN_PORT=6806
SIYUAN_API_TOKEN=your-token-here

# OpenAI Embedding 配置（语义搜索需要）
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_EMBEDDING_DIMENSION=

# 排除规则（API 层面过滤）
SIYUAN_EXCLUDE_NOTEBOOKS=
SIYUAN_EXCLUDE_DOCS=
SIYUAN_EXCLUDE_PATHS=/daily note,/templates
```

## 依赖

- Python 3.9+
- uv
- 思源笔记正在运行

## 许可证

Apache-2.0
