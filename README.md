# APS 实施顾问 AI 工作台

面向 APS 实施顾问的五阶段 AI 工作台：需求调研、数据梳理、方案设计、配置验证、上线验收。
在阶段 Agent 之外，提供独立的全局 AI 小助手和项目知识库 RAG 问答。

## 功能概览

- 首页项目总览：统计卡片、项目卡片、五阶段状态、最近更新与对话摘要。
- 项目重命名、删除；删除时清理上下文、对话、内存向量索引、文档、上传文件和助手历史。
- 阶段对话历史恢复、清空当前阶段历史、重新生成不追加重复历史。
- RAG 来源可点击查看原文、前后片段和页码/行号定位。
- 任意阶段归档后均可导出 Word / Excel；Markdown 标题、列表、表格自适应排版，解析失败自动降级为纯文本。
- 售前快速评估：独立表单、APS 价值点初稿、Word 导出和本地历史记录。
- 上线后跟踪：阶段五归档后解锁问题看板、问题状态管理、Agent 分析和 Excel 导出。
- 阶段一/三归档人工确认门禁与审计记录；阶段五上线清单固定检查项。
- 历史模板复用：从已归档阶段一/五带入初始上下文。
- 五阶段导航、阶段上下文、对话历史、手动归档和来源展示。
- 工作台独立路由 `/workbench/<project_id>`，项目不存在时回到首页提示。
- 文档上传：DOCX / XLSX / 数字版 PDF，自动解析、切片，并写入内存切片索引。
- 外部 OpenAI-compatible Embedding API（默认 SiliconFlow），不加载本地模型；未配置 Key 时使用关键词检索兜底。
- 阶段 Agent 与全局 AI 小助手均支持 SSE 流式输出。
- 多轮历史按 `project_id + stage` 保存，阶段切换后可恢复。
- 全局小助手历史单独存储，不污染阶段历史。
- 无 `DEEPSEEK_API_KEY` 时仍可运行，知识库回答使用抽取式兜底，阶段生成使用本地演示稿。
- API 请求日志包含耗时、请求 ID、token 消耗；错误格式统一。
- 49 个 pytest 测试覆盖首页/工作台、项目 CRUD、售前评估、上线跟踪、人工门禁、模板复用、历史恢复、来源详情、Word/Excel 导出和错误契约。

## 技术栈

- Flask + 原生 HTML/JS/CSS，无前端构建工具
- OpenAI Python SDK，`base_url=https://api.deepseek.com`
- 内存切片索引 + JSON 快照（不使用 Chroma）
- 外部 Embedding API（OpenAI-compatible）
- Python 3.12
- 生产使用 Gunicorn

## 页面路由

- `/`：首页项目总览与仪表盘。
- `/workbench/<project_id>`：指定项目的五阶段工作台。
- `/presales/<presales_id>`：售前快速评估独立页面。
- 项目不存在时自动回到首页并提示。

## 目录结构

```text
app.py                  Flask 入口、Blueprint 注册、请求日志与异常处理
runtime_config.py       开发/生产运行配置
config.py               路径、外部 Embedding、RAG、上传限制
routes/                 路由层（只做参数校验与响应）
services/               服务层（对话、检索、文档、LLM、助手、指标）
data/                   数据层（项目上下文、文档登记、助手历史、内存索引）
templates/ static/      单页前端
storage/                本地运行时数据（默认）
tests/                  pytest
docs/api.md             API 契约
samples/                验收用模拟排产 Excel
gunicorn.conf.py        Gunicorn 生产配置
Dockerfile / compose.yaml
```

## 本地运行

```bash
cd aps-workbench
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 可选：编辑 .env，填入 DEEPSEEK_API_KEY 和 EMBEDDING_API_KEY
python3 app.py
```

打开 **http://127.0.0.1:5001**，进入首页项目总览。点击项目卡片进入 `/workbench/<project_id>`。

启动时会自动加载项目根目录的 `.env`（已存在的环境变量优先级更高）。
文档向量化调用外部 Embedding API，默认是 SiliconFlow 的 `BAAI/bge-m3`：

```text
EMBEDDING_API_BASE=https://api.siliconflow.cn/v1
EMBEDDING_API_KEY=你的 Embedding Key
EMBEDDING_MODEL=BAAI/bge-m3
```

没有配置 Embedding Key 时，文档仍会入库，但检索降级为内存关键词匹配。
不会下载或加载任何本地 sentence-transformers / torch 模型。

### 验证 RAG

1. 新建项目。
2. 上传 [samples/排产计划示例.xlsx](samples/排产计划示例.xlsx)。
3. 等待知识库状态变为“已入库”，确认切片数。
4. 提问“这个文件里排产周期是多少？”，应返回 `排产计划示例.xlsx` 的来源和 `7 天`。
5. 提问“这个文件里食堂菜单是什么？”，阶段 Agent 应明确回答“当前知识库中没有相关信息”。

### 验证多轮与阶段切换

在阶段一连续问三轮，例如：

1. “本项目有哪些关键排产约束？”
2. “换型时间应该怎么建模？”
3. “刚才说的那个约束，对排程方案有什么影响？”

切到阶段二再切回，阶段一的对话和 Agent 输出应恢复。真实语义指代需要有效的
`DEEPSEEK_API_KEY`；无 Key 时本地演示稿无法真正理解语义指代。

### 验证全局 AI 小助手

点击右下角机器人 FAB：

- 提问项目文档问题，会优先检索知识库并显示来源。
- 知识库未命中时转通用回答。
- 刷新页面后，独立助手历史仍可从服务端恢复。
- 助手历史不会出现在五阶段对话中。

## 环境变量

复制 `.env.example` 为 `.env`，常用变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek Key；为空时使用本地演示/抽取式兜底 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 对话模型 |
| `APP_ENV` | `development` | `development` / `production` |
| `PORT` | `5001` | 服务端口 |
| `EMBEDDING_API_BASE` | `https://api.siliconflow.cn/v1` | 外部 OpenAI-compatible Embedding 接口 |
| `EMBEDDING_API_KEY` | 空 | Embedding API Key；也支持 `SILICONFLOW_API_KEY` |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Embedding 模型名 |
| `EMBEDDING_TIMEOUT` | `60` | Embedding 请求超时秒数 |
| `APS_DATA_DIR` | 项目目录 | 运行时数据根目录（Docker 使用 `/data`） |
| `RAG_TOP_K` | `5` | 检索条数 |
| `RAG_MIN_SCORE` | `0.35` | 语义召回最低分 |
| `RAG_RELEVANCE_SCORE` | `0.50` | 无语义高分时要求关键词命中的门槛 |
| `MAX_UPLOAD_MB` | `20` | 单文件上限 |

### 开发 / 生产配置

- 开发：`APP_ENV=development`，`python3 app.py`，默认监听 `127.0.0.1:5001`。
- 生产：`APP_ENV=production`，由 Gunicorn 监听 `0.0.0.0:5001`。

直接使用 Gunicorn：

```bash
APP_ENV=production gunicorn -c gunicorn.conf.py app:app
```

Gunicorn 默认 `1 worker + 8 threads`。内存索引和本地 JSON 快照需要单进程一致性，
不建议开启多 worker 或多实例。

## Docker 运行

### 一条命令

```bash
docker compose up --build
```

打开 **http://127.0.0.1:5001**。需要 DeepSeek Key 时：

```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY 和 EMBEDDING_API_KEY
docker compose up --build
```

Compose 会：

- 使用 `APP_ENV=production` 和 Gunicorn 启动。
- 把项目数据挂载到命名卷 `aps_data`（容器内 `/data`）。
- 通过外部 Embedding API 生成向量，不把模型打包进镜像。
- 暴露 `5001` 端口并配置健康检查。

停止：

```bash
docker compose down
```

数据仍在 `aps_data` 卷中。清空运行时数据：

```bash
docker compose down -v
```

> Docker 镜像不再包含 PyTorch、transformers、sentence-transformers 或本地模型，
> 构建体积和内存需求显著降低。

## 测试

```bash
pytest -q
```

当前 49 个用例覆盖：

- 文档解析、切片长度、sheet/行号/chunk index。
- 检索结果映射、相似度阈值、关键词兜底。
- 对话命中/未命中、多轮历史、阶段 SSE。
- 上下文读写、归档、阶段切换历史保留、客户端 history 替换。
- 统一错误格式、`X-Request-ID`、token 统计。
- 仪表盘统计、排序、阶段映射与旧数据字段兼容。
- 首页/工作台页面路由、缺失项目重定向、返回首页确认元素。
- 项目 PATCH/DELETE、删除失败保留项目与日志、历史迁移/清空/重生成、来源详情。
- Word/Excel 标题、列表、表格、文件名与解析失败降级。
- 售前评估、上线问题、门禁确认、模板注入与旧数据兼容。

## 运行日志

每个 API 请求返回 `X-Request-ID`，日志包含：

```text
api_request method=POST path=/api/chat/stream status=200 duration_ms=... request_id=...
prompt_tokens=... completion_tokens=... total_tokens=... tokens_estimated=...
stream=True stream_error=False
```

- 非流式请求记录真实 DeepSeek usage。
- 流式请求优先使用上游 usage；缺失时按字符估算并标记 `tokens_estimated=true`。
- SSE 日志在流结束时记录，覆盖完整耗时。

## 数据说明

默认本地路径：

- `projects.json`：项目、阶段消息、归档内容。
- `storage/documents.json`：文档登记。
- `storage/uploads/`：原始上传文件。
- `storage/vector_index.json`：内存切片索引快照。
- `storage/assistant_history.json`：全局 AI 小助手独立历史。

Docker 中统一放在 `/data` 持久卷。

## 从旧版本升级

旧版本的 `storage/chroma/` 和 `storage/hf-cache/` 不再被读取，也不会自动迁移。
升级后请重新上传文档，系统会在 `storage/vector_index.json` 中重建内存切片索引。

如果配置了 `EMBEDDING_API_KEY`，重新上传时会调用外部 Embedding API；
未配置时自动使用内存关键词索引。

## API 文档

完整接口、SSE 事件、错误码和历史行为见 [docs/api.md](docs/api.md)。

## 明确不做

- 多用户、登录、权限系统。
- 独立部署的数据库服务。
- 扫描版 PDF OCR。
- 内置登录、权限和多租户；公网部署前需要自行增加访问控制。
