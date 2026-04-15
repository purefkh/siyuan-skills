# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Claude Code skill for managing SiYuan Note (思源笔记) - a local note-taking application. The project provides a Python CLI tool for searching, creating, editing, and deleting notes, notebooks, and blocks.

## Architecture

### Core Components

**`skills/siyuan/scripts/siyuan.py`** - Main CLI tool (~770 lines)
- Pure standard library implementation (no external dependencies for core functionality)
- Uses `argparse` for command structure with subcommands
- Uses `urllib.request` for HTTP API calls to SiYuan
- Manual .env parsing (no python-dotenv dependency)
- Commands: config, search, notebook, doc, block, attr, file, export, system, sync, index, sql

**`skills/siyuan/scripts/search_embed.py`** - Semantic search module
- Requires `openai` and `faiss-cpu` packages
- Chunk-based indexing (1000 chars per chunk, 20% overlap)
- FAISS IndexIDMap for incremental updates
- Min score threshold: 0.5
- Index stored in `.index/` directory (metadata.json + index.faiss)

### Key Design Decisions

**Zero dependencies for core**: CRUD operations and SQL search work with only Python standard library. Only semantic search requires external packages.

**Incremental indexing**: Index builds track document `updated` timestamps and only process changed documents.

**Exclusion filtering**: All searches automatically apply `SIYUAN_EXCLUDE_NOTEBOOKS` and `SIYUAN_EXCLUDE_PATHS` filters at SQL level.

**Output format**: Commands use plain text output (not JSON) for human readability. Search results show full content without truncation.

## Development Commands

### Setup
```bash
cd skills/siyuan

# Install dependencies
uv sync

# Configure .env file
cp .env.example .env
# Edit .env with SIYUAN_HOST, SIYUAN_PORT, SIYUAN_API_TOKEN
```

### Testing
```bash
cd skills/siyuan

# Check connectivity
uv run python scripts/siyuan.py system version

# Test keyword search
uv run python scripts/siyuan.py search keyword "test" --limit 3

# Test semantic search (requires index first)
uv run python scripts/siyuan.py index build
uv run python scripts/siyuan.py search semantic "test query"

# Test notebook list
uv run python scripts/siyuan.py notebook list
```

### Build Index
```bash
cd skills/siyuan

# Incremental update (default)
uv run python scripts/siyuan.py index build

# Force full rebuild
uv run python scripts/siyuan.py index build --force

# Check index status
uv run python scripts/siyuan.py index status
```

## Configuration

All configuration via `.env` file in project root:

```env
SIYUAN_HOST=127.0.0.1
SIYUAN_PORT=6806
SIYUAN_API_TOKEN=your-token-here

OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=              # Optional, for custom endpoints
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

SIYUAN_EXCLUDE_NOTEBOOKS=     # Comma-separated notebook IDs
SIYUAN_EXCLUDE_PATHS=/daily note,/templates
```

Config priority: command-line args > environment variables > .env file

## Search Strategy

Use semantic search (`search semantic`) when index is available. Fallback order:
1. **semantic** - OpenAI Embedding + FAISS (best for intent matching)
2. **keyword** - SQL LIKE (fast, exact matching)
3. **doc** - Full document export for analysis
4. **recent** - Recently modified documents
5. **sql** - Raw SQL queries for complex cases

## Important Notes

- SiYuan API has no sync endpoint - sync must be done via SiYuan application
- Delete operations require `--force` flag - always confirm with user first
- Block/document operations prefer IDs over paths for reliability
- The skill is installed as a symlink: `~/.claude/skills/siyuan` → this repository
- **Block-level editing**: SiYuan is block-based - always locate the smallest target block before updating
- Use `block children` to view document structure and find precise block IDs for editing

## Development Guidelines

### Adding New Features

**Use CLI commands, not curl**: When adding new features or API interactions, always implement them as CLI commands in `scripts/siyuan.py` rather than using raw `curl` calls. This ensures:

1. **Consistent interface** - All operations go through the same CLI tool
2. **Proper error handling** - Centralized error messages and validation
3. **Environment management** - Automatic .env configuration loading
4. **Maintainability** - Easier to update and test

**Example pattern**:
```bash
# ❌ Don't do this
curl -X POST "http://127.0.0.1:6806/api/xxx" ...

# ✅ Do this - add a CLI command
uv run python scripts/siyuan.py <command> <subcommand> ...
```
