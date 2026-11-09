# APS 实施顾问 AI 工作台 · RAG 改造 API 契约

> 版本：`v1.9`  
> 状态：**全生命周期工作台 + 外部 Embedding / 内存索引部署优化**（2026-11-09）  
> 适用范围：全生命周期工作台与外部 Embedding / 内存索引  
> 最后更新：2026-11-09

---

## 1. 目标与边界

本次改造把 `/api/chat` 从“按阶段拼提示词”升级为“检索增强生成（RAG）”：

1. 文档上传后进入解析、切片、可选外部向量化、内存索引管道。
2. 对话时先按 `project_id` 检索相关片段，再拼系统提示词、检索上下文、对话历史和用户问题。
3. 回答必须可追溯：返回 `sources`，正文使用 `[1]`、`[2]` 引用来源。
4. 检索不到可靠内容时，明确回答“当前知识库中没有相关信息”，不编造。
5. 保留现有五阶段导航、项目上下文、手动归档、Word/Excel 导出，不重写 UI。

明确不做：

- 多用户、登录、权限。
- 独立部署的数据库服务。
- 扫描件 PDF 的 OCR；扫描件返回友好错误。
- 重写现有五阶段 UI 和导出功能。

---

## 2. 通用约定

### 2.1 基础信息

| 项目 | 约定 |
| --- | --- |
| Base URL | `http://127.0.0.1:5001`（本地） |
| 数据格式 | JSON；上传接口为 `multipart/form-data` |
| 字符编码 | UTF-8 |
| 鉴权 | 无（单用户本地工作台） |
| 时间格式 | ISO 8601，带时区，例如 `2026-11-09T11:03:01+08:00` |
| 项目 ID | 字符串，格式 `prj_` + 12 位十六进制 |
| 文档 ID | 字符串，格式 `doc_` + 12 位十六进制 |
| 阶段 | 整数 `1..5` |
| 成功响应 | 保留现有 `ok: true` 风格 |
| 错误响应 | 统一为 `{ok, code, message, data, error}`，见 2.3 |

### 2.2 成功响应约定

所有成功响应都包含 `ok: true`。不同接口在此之上返回业务字段，例如：

```json
{
  "ok": true,
  "reply": "……",
  "sources": []
}
```

### 2.3 统一错误格式

目标统一结构如下；第二阶段开始返回 `code`、`message`、`data`，同时保留现有前端依赖的 `error` 字段作为兼容别名，第四阶段完成全量迁移：

```json
{
  "ok": false,
  "code": "PROJECT_NOT_FOUND",
  "message": "项目不存在，请刷新页面或重新创建项目。",
  "data": null,
  "error": "项目不存在，请刷新页面或重新创建项目。"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `ok` | boolean | 是 | 固定为 `false` |
| `code` | string | 是 | 稳定错误码，前端可据此分支处理 |
| `message` | string | 是 | 面向用户的中文提示 |
| `data` | any | 是 | 结构化错误详情；无详情时为 `null` |
| `error` | string | 是 | 兼容字段，当前值等于 `message`；后续前端迁移完成后可移除 |

`data` 示例：

```json
{
  "ok": false,
  "code": "PARSE_FAILED",
  "message": "Excel 文件解析失败，请确认文件未加密且格式正确。",
  "data": {
    "doc_id": "doc_a1b2c3d4e5f6",
    "stage": 2
  },
  "error": "Excel 文件解析失败，请确认文件未加密且格式正确。"
}
```

### 2.4 HTTP 状态码

| HTTP | 语义 | 使用场景 |
| --- | --- | --- |
| `200` | 成功 | 查询、对话、归档 |
| `201` | 已创建 | 创建项目、上传并入库成功 |
| `400` | 参数错误 | JSON 非法、字段缺失、长度/枚举不合法 |
| `404` | 资源不存在 | 项目不存在、路由不存在 |
| `405` | 方法不允许 | HTTP 方法错误 |
| `413` | 请求体过大 | 上传文件超过 20 MiB |
| `415` | 媒体类型不支持 | 非 docx/xlsx/pdf |
| `422` | 内容无法处理 | 文档解析失败、空文档、扫描件 PDF |
| `429` | 请求过多 | 上游限流 |
| `500` | 服务内部错误 | 未捕获异常、向量库写入失败 |
| `502` | 上游失败 | 嵌入模型、DeepSeek 调用失败或鉴权失败 |
| `504` | 上游超时 | LLM、嵌入、解析超时 |

---

## 3. 数据对象

### 3.1 HistoryMessage

```json
{
  "role": "user",
  "content": "刚才说的换型约束是什么？"
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `role` | string | 是 | 仅支持 `user`、`assistant` |
| `content` | string | 是 | 消息正文，1–8000 字符 |

### 3.2 Source

`sources` 中的每一项代表一个被检索并注入上下文的文档片段。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | string | 是 | 原始文件名，例如 `排产计划.xlsx` |
| `snippet` | string | 是 | 片段正文，最多 500 字符；前端点击来源标签时展示 |
| `doc_id` | string | 否 | 文档 ID |
| `stage` | integer | 否 | 文档上传时所属阶段 |
| `page` | integer | 否 | PDF 页码，从 1 开始 |
| `sheet` | string | 否 | Excel 工作表名 |
| `row_start` | integer | 否 | Excel 起始行号，从 1 开始 |
| `row_end` | integer | 否 | Excel 结束行号，从 1 开始 |
| `paragraph_start` | integer | 否 | DOCX 起始段落序号，从 1 开始 |
| `paragraph_end` | integer | 否 | DOCX 结束段落序号，从 1 开始 |
| `chunk_index` | integer | 否 | 文档内切片序号，从 0 开始 |
| `score` | number | 否 | 相似度分数，范围 `0..1`，越高越相关 |

正文引用约定：

- 检索上下文中的片段按顺序编号为 `[1]`、`[2]`……。
- `sources[0]` 对应 `[1]`，`sources[1]` 对应 `[2]`。
- 回答正文中的引用编号必须能在 `sources` 中找到对应项。
- `sources` 只表示上传文档的检索片段；项目归档内容不作为文件来源展示。

### 3.3 Document

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `doc_id` | string | 文档 ID |
| `file` | string | 原始文件名 |
| `file_type` | string | `docx`、`xlsx`、`pdf` |
| `stage` | integer | 上传时所属阶段 |
| `status` | string | `processing`、`indexed`、`failed` |
| `chunks_count` | integer | 已入库切片数 |
| `summary` | string | 阶段相关结构化摘要，Markdown |
| `size_bytes` | integer | 文件大小 |
| `created_at` | string | 上传时间 |
| `indexed_at` | string/null | 完成入库时间 |
| `error` | object/null | 失败时的 `{code, message}` |

### 3.4 ChatAnswer

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `reply` | string | 是 | Agent 回答，Markdown；包含 `[n]` 引用 |
| `sources` | Source[] | 是 | 检索到的文档片段；无检索结果时为 `[]` |
| `grounded` | boolean | 否 | 是否存在至少一条可用文档来源 |
| `answer_mode` | string | 否 | `rag` 或 `stage` |
| `mode` | string | 否 | `deepseek`、`demo`、`demo-fallback`、`no_context` |
| `project` | object | 否 | 更新后的项目上下文，兼容现有前端 |
| `warning` | string/null | 否 | 回退或降级提示 |
| `history_reset` | boolean | 否 | 兼容字段；当前固定为 `false`，阶段切换不再重置历史 |
| `ok` | boolean | 是 | 固定为 `true` |

---

## 4. 接口定义

### 4.1 POST /api/chat

基于项目文档知识库进行多轮问答。

#### 4.1.1 请求

```http
POST /api/chat
Content-Type: application/json
```

```json
{
  "project_id": "prj_ac7bcc665f48",
  "message": "这个文件里排产周期是多少？",
  "history": [
    {
      "role": "user",
      "content": "先看一下排产参数。"
    },
    {
      "role": "assistant",
      "content": "已找到排产参数表，请继续提问。"
    }
  ],
  "stage": 2,
  "answer_mode": "rag"
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `project_id` | string | 是 | 项目 ID |
| `message` | string | 条件必填 | 本轮用户问题，1–8000 字符；与 `user_input` 二选一 |
| `history` | HistoryMessage[] | 否 | 此前对话，最多 20 条，总字符数不超过 30000；不包含本轮 `message` |
| `stage` | integer | 否 | 1–5；省略时使用项目 `active_stage` |
| `answer_mode` | string | 否 | `rag`（见兼容规则）或 `stage`；见 4.1.3 |
| `user_input` | string | 否 | **兼容字段**，等价于 `message`；两者同时存在时 `message` 优先 |
| `correction` | string | 否 | **兼容字段**，仅 `stage` 模式使用；纠偏后重新生成 |

##### 校验与兼容规则

1. `message`、`user_input`、`correction` 至少有一个非空，否则返回 `400 VALIDATION_ERROR`。
2. `message` 与 `user_input` 同时存在时，以 `message` 为准。
3. 未显式传 `answer_mode` 时：
   - 传了 `message`：默认 `rag`。
   - 只传了旧字段 `user_input` 或 `correction`：默认 `stage`，保证现有阶段生成与纠偏功能不被破坏。
4. `answer_mode=rag` 时传 `correction`，返回 `400 INVALID_REQUEST`；纠偏重生成请使用 `stage` 模式。
5. `history` 中出现非法 `role`、超长单条内容、总条数超限，返回 `400 HISTORY_INVALID` 或 `400 HISTORY_TOO_LONG`。
6. 不做服务端会话 token；无鉴权。

#### 4.1.2 对话历史与持久化

- 对话历史按 `project_id + stage` 存储。
- 请求不传 `history`：读取服务端该阶段已存历史作为上下文。
- 请求传 `history`：经校验后以它作为本轮完整上文，并**替换**该阶段已存历史，再追加本轮 user/assistant；避免客户端与服务端各存一份导致重复。
- 即使检索为空，本轮“问题 + 无信息回答”也会写入历史，便于用户上传文档后继续追问。
- `/api/stage/switch` 只切换当前阶段，不再清空该阶段对话历史；切回时由前端从项目上下文恢复历史，归档内容不受影响。

> 这是本契约需要确认的决策之一，见第 8 节“待确认事项”。

#### 4.1.3 answer_mode 语义

| 模式 | 用途 | 检索行为 | 检索为空时 |
| --- | --- | --- | --- |
| `rag` | 文档知识库问答，默认模式 | 检索 top-k 文档片段 | 不调用 LLM，直接返回固定“无相关信息”答复 |
| `stage` | 保留现有五阶段交付物生成、纠偏重生成 | 有文档时仍会检索并注入上下文 | 允许基于项目基本信息与归档内容生成阶段交付物 |

`stage` 模式用于兼容现有阶段一表单生成、阶段交付物生成和“重新生成”。RAG 文档问答统一使用 `rag`。

#### 4.1.4 处理流程

1. 校验参数、项目、阶段、历史。
2. 调用检索服务，按 `project_id` 检索相关文档片段（默认 top-k=5，最低相似度 0.35，均可配置）。
3. 构建提示词：
   - 当前阶段系统提示词；
   - APS 领域约束；
   - 项目基本信息与已归档上下文；
   - 检索到的编号片段；
   - 校验后的对话历史；
   - 本轮用户问题。
4. 仅 `rag` 模式且无任何可用片段时，返回固定无信息答复，不调用 LLM。
5. 调用 DeepSeek 生成回答；要求正文使用 `[1]`、`[2]` 引用来源，禁止编造。
6. 保存本轮对话历史与项目更新时间。
7. 返回 `reply` 和 `sources`。

#### 4.1.5 响应

**检索命中示例：**

```json
{
  "ok": true,
  "reply": "根据《排产计划.xlsx》的参数表，排产周期为 7 天，计划冻结期为 1 天。[1]",
  "sources": [
    {
      "file": "排产计划.xlsx",
      "snippet": "排产参数：排产周期 7 天；计划冻结期 1 天；每日排程时点 08:30。",
      "doc_id": "doc_a1b2c3d4e5f6",
      "stage": 2,
      "sheet": "排产参数",
      "row_start": 3,
      "row_end": 4,
      "chunk_index": 0,
      "score": 0.83
    }
  ],
  "grounded": true,
  "answer_mode": "rag",
  "mode": "deepseek",
  "project": {},
  "warning": null,
  "history_reset": false
}
```

**检索为空示例（HTTP 200，不是异常）：**

```json
{
  "ok": true,
  "reply": "当前知识库中没有相关信息。请先上传包含该信息的文档，或补充项目上下文。",
  "sources": [],
  "grounded": false,
  "answer_mode": "rag",
  "mode": "no_context",
  "project": {},
  "warning": null,
  "history_reset": false
}
```

固定答复必须以 **“当前知识库中没有相关信息”** 开头，允许追加引导语。

**stage 模式示例：**

```json
{
  "ok": true,
  "reply": "## 阶段一 · 现状调研与需求诊断\n……",
  "sources": [],
  "grounded": false,
  "answer_mode": "stage",
  "mode": "deepseek",
  "project": {},
  "warning": null,
  "history_reset": false
}
```

#### 4.1.6 引用与防编造约束

- `rag` 模式下，事实依据仅限检索到的文档片段；项目上下文只用于理解客户背景，不能替代文档事实。
- 即使检索到了片段，只要片段不足以回答用户问题，回答也必须以「当前知识库中没有相关信息」开头，不得用常识补全。
- 每个基于文档片段的事实陈述必须带 `[n]` 引用；`n` 对应 `sources` 数组下标 + 1。
- 模型不得编造文件名、页码、行号、指标数值或客户事实。
- 后端会对引用编号做基础校验；无法对应到 `sources` 的引用编号应被移除或触发一次无信息降级。

#### 4.1.7 错误码

| code | HTTP | 说明 |
| --- | --- | --- |
| `INVALID_JSON` | 400 | 请求体不是合法 JSON 对象 |
| `VALIDATION_ERROR` | 400 | 必填字段缺失或为空 |
| `INVALID_STAGE` | 400 | stage 不在 1–5 |
| `MESSAGE_TOO_LONG` | 400 | 单条消息超过 8000 字符 |
| `HISTORY_INVALID` | 400 | history 不是数组，或 role 非法 |
| `HISTORY_TOO_LONG` | 400 | history 超过 20 条或总字符数超限 |
| `INVALID_REQUEST` | 400 | rag 模式传 correction 等非法组合 |
| `PROJECT_NOT_FOUND` | 404 | 项目不存在 |
| `LLM_TIMEOUT` | 504 | DeepSeek 超时 |
| `LLM_AUTH_FAILED` | 502 | API Key 无效 |
| `LLM_RATE_LIMITED` | 429 | 上游限流 |
| `LLM_UPSTREAM_ERROR` | 502 | DeepSeek 其它错误 |
| `EMBEDDING_FAILED` | 502 | 查询向量化失败 |
| `VECTOR_STORE_FAILED` | 500 | 内存索引读取失败 |
| `INTERNAL_ERROR` | 500 | 未捕获异常 |

`rag` 模式无检索结果**不返回错误**，返回 `200` 和固定答复。

#### 4.1.8 POST /api/chat/stream（阶段 Agent SSE）

请求体与 `POST /api/chat` 相同，不使用请求体里的 `stream` 字段；该路由固定返回 SSE。

```http
POST /api/chat/stream
Content-Type: application/json
Accept: text/event-stream
```

```json
{
  "project_id": "prj_ac7bcc665f48",
  "message": "刚才说的那个约束是什么？",
  "history": [],
  "stage": 2,
  "answer_mode": "stage"
}
```

事件顺序：

```text
event: meta
data: {"project_id":"prj_...","stage":2,"answer_mode":"stage","grounded":false}

event: sources
data: {"sources":[]}

event: delta
data: {"text":"## 阶段二"}

event: done
data: {"reply":"完整回答","sources":[],"grounded":false,"answer_mode":"stage","mode":"deepseek","project":{...},"warning":null,"history_reset":false}

event: error
data: {"ok":false,"code":"LLM_TIMEOUT","message":"……","data":null,"error":"……"}
```

- 发生检索、LLM、持久化错误时，SSE 流以 `error` 事件结束。
- `done.project` 为更新后的完整项目上下文，前端据此刷新阶段状态、消息和来源。
- 普通 JSON 调用继续使用 `POST /api/chat`，行为不变。

#### 4.1.9 多轮历史恢复

- 对话历史按 `project_id + stage` 写入项目的 `stages[stage].messages`。
- `/api/stage/switch` 不再清空历史；切换回某阶段时，前端从 `/api/context/{project_id}` 读取并恢复。
- 每轮请求把该阶段最近 20 条 `user/assistant` 消息传给 DeepSeek，因此可以理解“刚才说的那个约束”这类指代。
- 传入 `history` 时仍以客户端数组替换该阶段历史；不传时使用服务端已存历史。

---

### 4.2 POST /api/upload

上传文档，完成解析、切片、可选外部向量化，并写入内存切片索引；同时生成阶段相关摘要。

#### 4.2.1 请求

```http
POST /api/upload
Content-Type: multipart/form-data
```

| 表单字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | file | 是 | 待上传文件 |
| `project_id` | string | 是 | 所属项目 |
| `stage` | integer | 是 | 1–5，决定摘要模板 |

支持格式与限制：

| 项目 | 约定 |
| --- | --- |
| 支持扩展名 | `.docx`、`.xlsx`、`.pdf`（大小写不敏感） |
| 不支持 | `.doc`、`.xls`、图片、扫描件 OCR |
| 单文件大小 | 最大 20 MiB |
| 文件名长度 | 最大 255 字符 |
| 加密文件 | 返回 `422 PARSE_FAILED` 或 `422 EMPTY_DOCUMENT` |
| 重复上传 | 每次生成新的 `doc_id`，不做去重 |
| 原始文件 | 保存在本地 `storage/uploads/`，仅供重解析和问题排查，不通过 API 暴露 |

#### 4.2.2 解析与入库管道

1. **解析**
   - `.docx`：`wizarddocx.extract_text`。
   - `.xlsx`：`openpyxl.load_workbook(data_only=True)`，遍历所有 sheet，保留工作表名与行号。
   - `.pdf`：`pdfplumber-rs`，逐页 `extract_text()`；仅支持含文本层的数字版 PDF。
2. **切片**
   - 优先按标题/段落/表格行组切片，单切片目标 600–900 字符，重叠约 100 字符。
   - 每个切片必须携带来源元数据：文件名、`doc_id`、`stage`；PDF 带页码，Excel 带 sheet + 行号，DOCX 带段落范围。
3. **向量化（外部 API）**
   - 配置了 `EMBEDDING_API_KEY` 时，调用 OpenAI-compatible Embedding API。
   - 默认接口：`https://api.siliconflow.cn/v1`，默认模型：`BAAI/bge-m3`。
   - 未配置 Key 时跳过向量化，使用内存关键词检索兜底。
4. **写入内存切片索引**
   - 切片常驻内存，并快照到 `storage/vector_index.json` 以便进程重启恢复。
   - 不使用 Chroma 或本地向量数据库。
5. **摘要**
   - 按 `stage` 调 DeepSeek 生成结构化摘要，返回 Markdown。
   - 摘要生成失败不阻断入库：使用抽取式摘要，返回 `warnings`。
6. **登记**
   - 文档状态登记在本地 `storage/documents.json`，与内存索引分离，便于列表查询和失败重试。

#### 4.2.3 响应

HTTP `201 Created`

```json
{
  "ok": true,
  "doc_id": "doc_a1b2c3d4e5f6",
  "status": "indexed",
  "file": "排产计划.xlsx",
  "file_type": "xlsx",
  "stage": 2,
  "chunks_count": 18,
  "summary": "## 文档摘要\n- 排产周期：7 天\n- 设备数量：12 台\n- 产品族：3 个\n- 当前换型时间：无实测矩阵",
  "summary_format": "markdown",
  "size_bytes": 18240,
  "created_at": "2026-11-09T11:03:01+08:00",
  "indexed_at": "2026-11-09T11:03:04+08:00",
  "warnings": []
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `doc_id` | string | 是 | 文档 ID |
| `status` | string | 是 | 本同步接口成功时通常为 `indexed` |
| `file` | string | 是 | 原始文件名 |
| `file_type` | string | 是 | `docx` / `xlsx` / `pdf` |
| `stage` | integer | 是 | 上传时阶段 |
| `chunks_count` | integer | 是 | 入库切片数，必须大于 0 |
| `summary` | string | 是 | 阶段相关结构化摘要，Markdown |
| `summary_format` | string | 否 | 固定 `markdown` |
| `size_bytes` | integer | 否 | 文件大小 |
| `created_at` | string | 否 | 上传时间 |
| `indexed_at` | string | 否 | 入库时间 |
| `warnings` | array | 否 | 非阻断警告，例如摘要回退 |

`warnings` 元素：

```json
{
  "code": "SUMMARY_FALLBACK",
  "message": "DeepSeek 摘要生成失败，已使用抽取式摘要。"
}
```

#### 4.2.4 失败时的文档状态

- 解析失败、空文档、扫描件：不建立可检索切片；返回对应 `422` 错误。
- 若已生成 `doc_id`，错误响应 `data` 中返回 `doc_id`，文档列表状态为 `failed`，带 `error` 详情。
- 外部向量化失败：返回 `502 EMBEDDING_FAILED`，文档状态 `failed`。
- 内存索引快照写入失败：返回 `500 VECTOR_STORE_FAILED`，文档状态 `failed`。
- 摘要失败：不回滚入库，返回 `201`，`warnings` 中包含 `SUMMARY_FALLBACK`。

#### 4.2.5 错误码

| code | HTTP | 说明 |
| --- | --- | --- |
| `NO_FILE` | 400 | 未上传 file |
| `VALIDATION_ERROR` | 400 | 缺少 project_id 或 stage |
| `INVALID_STAGE` | 400 | stage 不在 1–5 |
| `PROJECT_NOT_FOUND` | 404 | 项目不存在 |
| `FILE_TOO_LARGE` | 413 | 超过 20 MiB |
| `UNSUPPORTED_FILE_TYPE` | 415 | 扩展名不是 docx/xlsx/pdf |
| `PARSE_FAILED` | 422 | 文件损坏、加密、解析异常 |
| `EMPTY_DOCUMENT` | 422 | 解析后无可入库文本 |
| `SCANNED_PDF_UNSUPPORTED` | 422 | PDF 无文本层，需要 OCR，明确不支持 |
| `EMBEDDING_FAILED` | 502 | 外部 Embedding 调用失败 |
| `VECTOR_STORE_FAILED` | 500 | 内存索引快照写入失败 |
| `INTERNAL_ERROR` | 500 | 未捕获异常 |

错误响应示例：

```json
{
  "ok": false,
  "code": "SCANNED_PDF_UNSUPPORTED",
  "message": "该 PDF 可能是扫描件，当前版本不支持 OCR。请上传含文本层的数字版 PDF，或先手动转为可复制文本的 PDF。",
  "data": {
    "doc_id": "doc_a1b2c3d4e5f6",
    "file": "扫描件.pdf"
  },
  "error": "该 PDF 可能是扫描件，当前版本不支持 OCR。请上传含文本层的数字版 PDF，或先手动转为可复制文本的 PDF。"
}
```

---

### 4.3 GET /api/documents/{project_id}

列出项目已上传文档及处理状态，按上传时间倒序。

#### 4.3.1 请求

```http
GET /api/documents/prj_ac7bcc665f48
```

无请求体。

#### 4.3.2 响应

```json
{
  "ok": true,
  "total": 2,
  "documents": [
    {
      "doc_id": "doc_a1b2c3d4e5f6",
      "file": "排产计划.xlsx",
      "file_type": "xlsx",
      "stage": 2,
      "status": "indexed",
      "chunks_count": 18,
      "summary": "## 文档摘要\n- 排产周期：7 天",
      "size_bytes": 18240,
      "created_at": "2026-11-09T11:03:01+08:00",
      "indexed_at": "2026-11-09T11:03:04+08:00",
      "error": null
    },
    {
      "doc_id": "doc_0f1e2d3c4b5a",
      "file": "扫描件.pdf",
      "file_type": "pdf",
      "stage": 1,
      "status": "failed",
      "chunks_count": 0,
      "summary": "",
      "size_bytes": 981204,
      "created_at": "2026-11-09T10:50:00+08:00",
      "indexed_at": null,
      "error": {
        "code": "SCANNED_PDF_UNSUPPORTED",
        "message": "PDF 无文本层，当前版本不支持 OCR。"
      }
    }
  ]
}
```

状态枚举：

| status | 说明 |
| --- | --- |
| `processing` | 正在解析或向量化；当前同步上传流程中通常不会出现在查询结果里，为后续异步化保留 |
| `indexed` | 已成功入库，可被检索 |
| `failed` | 处理失败，`error` 必填，不可被检索 |

#### 4.3.3 错误码

| code | HTTP | 说明 |
| --- | --- | --- |
| `PROJECT_NOT_FOUND` | 404 | 项目不存在 |
| `VECTOR_STORE_FAILED` | 500 | 读取文档登记或内存索引失败 |
| `INTERNAL_ERROR` | 500 | 未捕获异常 |

---

### 4.4 POST /api/project（保持现有契约）

创建项目，不需要改动现有前端。

#### 请求

```json
{
  "client_name": "华东精密制造有限公司",
  "industry": "汽车零部件",
  "production_mode": "订单型 MTO",
  "pain_points": "插单频繁，缺料发现晚，关键设备故障后计划无法快速恢复"
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `client_name` | string | 是 | 客户名称，最大 120 字符 |
| `industry` | string | 否 | 行业 |
| `production_mode` | string | 否 | 生产模式 |
| `pain_points` | string | 否 | 核心痛点 |

#### 响应

HTTP `201`

```json
{
  "ok": true,
  "project_id": "prj_ac7bcc665f48",
  "project": {
    "id": "prj_ac7bcc665f48",
    "client_name": "华东精密制造有限公司",
    "industry": "汽车零部件",
    "production_mode": "订单型 MTO",
    "pain_points": "插单频繁，缺料发现晚，关键设备故障后计划无法快速恢复",
    "active_stage": 1,
    "stages": {
      "1": {
        "status": "not_started",
        "messages": [],
        "archive": {
          "content": "",
          "archived_at": null,
          "version": 0
        }
      }
    },
    "created_at": "2026-11-09T11:03:01+08:00",
    "updated_at": "2026-11-09T11:03:01+08:00",
    "last_active_at": "2026-11-09T11:03:01+08:00",
    "last_summary": ""
  }
}
```

> `project` 保留现有契约；新增顶层 `project_id` 便捷字段。`stages` 实际包含 1–5 五个阶段，示例中只展示阶段 1。

#### 错误码

| code | HTTP | 说明 |
| --- | --- | --- |
| `VALIDATION_ERROR` | 400 | 客户名称为空或过长 |
| `INTERNAL_ERROR` | 500 | 持久化失败 |

---

### 4.5 GET /api/context/{project_id}（保持现有契约）

返回项目完整上下文，供五阶段导航、对话记录和归档展示使用。

#### 响应

```json
{
  "ok": true,
  "project": {
    "id": "prj_ac7bcc665f48",
    "client_name": "华东精密制造有限公司",
    "active_stage": 2,
    "stages": {
      "1": {
        "status": "archived",
        "messages": [],
        "archive": {
          "content": "阶段一归档内容……",
          "archived_at": "2026-11-09T10:30:00+08:00",
          "version": 1
        }
      }
    }
  },
  "stages": [
    {
      "id": 1,
      "name": "现状调研与需求诊断",
      "short_name": "需求调研",
      "description": "……",
      "deliverable": "《APS 现状调研与需求诊断纪要》",
      "export_type": "word"
    }
  ],
  "llm_mode": "deepseek"
}
```

> 契约保持不变。文档列表请使用 `/api/documents/{project_id}`，不塞入本接口，避免现有前端渲染逻辑变化。

#### 错误码

| code | HTTP | 说明 |
| --- | --- | --- |
| `PROJECT_NOT_FOUND` | 404 | 项目不存在 |

---

### 4.6 POST /api/archive（保持现有契约）

将当前阶段内容写入项目上下文并置为已归档。

#### 请求

```json
{
  "project_id": "prj_ac7bcc665f48",
  "stage": 1,
  "content": "阶段一调研纪要……"
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `project_id` | string | 是 | 项目 ID |
| `stage` | integer | 是 | 1–5 |
| `content` | string | 是 | 归档正文，最大 200000 字符 |

#### 响应

```json
{
  "ok": true,
  "project": {
    "id": "prj_ac7bcc665f48",
    "active_stage": 1,
    "stages": {
      "1": {
        "status": "archived",
        "messages": [],
        "archive": {
          "content": "阶段一调研纪要……",
          "archived_at": "2026-11-09T11:10:00+08:00",
          "version": 2
        }
      }
    }
  }
}
```

> 契约保持不变。手动编辑侧边栏中的历史阶段时，不改变项目当前的 `active_stage`。

#### 错误码

| code | HTTP | 说明 |
| --- | --- | --- |
| `VALIDATION_ERROR` | 400 | 缺少字段或内容为空 |
| `INVALID_STAGE` | 400 | stage 不在 1–5 |
| `PROJECT_NOT_FOUND` | 404 | 项目不存在 |
| `INTERNAL_ERROR` | 500 | 持久化失败 |

---

### 4.7 POST /api/assistant/chat（全局 AI 小助手）

独立于五阶段工作流的全局助手接口。不接收或绑定 `stage`，历史按 `project_id` 单独存储，不写入阶段 `messages`。

#### 请求

```http
POST /api/assistant/chat
Content-Type: application/json
```

```json
{
  "project_id": "prj_ac7bcc665f48",
  "message": "这个项目目前最关键的排产约束是什么？",
  "stream": true
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `project_id` | string | 是 | 当前项目 |
| `message` | string | 是 | 用户问题，1–8000 字符 |
| `stream` | boolean | 否 | `true` 返回 SSE；`false` 返回 JSON。也可用 `Accept: text/event-stream` 触发 SSE |

#### 检索与回答策略

1. 默认按 `answer_mode="rag"` 调同一个 `retrieval_service` 检索当前项目知识库。
2. 检索命中：返回 `sources`，要求 DeepSeek 使用 `[n]` 引用；无 Key 时使用本地抽取式回答。
3. 检索为空或失败：不返回“当前知识库中没有相关信息”作为最终答案，而是继续调用通用大模型回答；`sources: []`、`grounded: false`。无 Key 时使用本地通用兜底。
4. 历史只从 `storage/assistant_history.json` 读取，不读取阶段历史；与阶段 Agent 的检索服务、内存切片索引、top-k 和相似度门槛一致，区别在提示词、无命中兜底方式和历史隔离。
5. 每轮完成后写入独立助手历史；阶段对话完全不受影响。

#### JSON 响应

```json
{
  "ok": true,
  "reply": "……[1]",
  "sources": [
    {
      "file": "排产计划示例.xlsx",
      "snippet": "……",
      "score": 0.78
    }
  ],
  "grounded": true,
  "answer_mode": "rag",
  "mode": "deepseek",
  "warning": null
}
```

#### SSE 响应

`Content-Type: text/event-stream`，事件顺序：

```text
event: meta
data: {"project_id":"prj_...","answer_mode":"rag","grounded":true}

event: sources
data: {"sources":[...]}

event: delta
data: {"text":"根据"}

event: done
data: {"reply":"完整回答","sources":[...],"grounded":true,"answer_mode":"rag","mode":"deepseek","warning":null}
```

错误事件：

```text
event: error
data: {"ok":false,"code":"PROJECT_NOT_FOUND","message":"……","data":null,"error":"……"}
```

#### 错误码

| code | HTTP | 说明 |
| --- | --- | --- |
| `INVALID_JSON` | 400 | 请求体非法 |
| `VALIDATION_ERROR` | 400 | 缺少 project_id 或 message |
| `MESSAGE_TOO_LONG` | 400 | 问题超过 8000 字符 |
| `PROJECT_NOT_FOUND` | 404 | 项目不存在 |
| `INTERNAL_ERROR` | 500 | 未捕获异常（SSE 中以 error 事件返回） |

### 4.8 GET /api/assistant/history/{project_id}

返回全局助手独立历史，供悬浮窗重新打开或页面刷新后恢复。

```json
{
  "ok": true,
  "messages": [
    {"role": "user", "content": "这个文件里排产周期是多少？", "ts": "2026-11-09T12:08:21+08:00"},
    {
      "role": "assistant",
      "content": "根据检索到的文档片段……[1]",
      "sources": [{"file": "排产计划示例.xlsx", "snippet": "……"}],
      "mode": "demo",
      "ts": "2026-11-09T12:08:24+08:00"
    }
  ]
}
```

> 该历史与 `/api/context` 中五阶段 `messages` 完全分离。

---

### 4.9 现有辅助接口

以下接口不属于本次 RAG 契约的核心范围，但被现有 UI 使用，保持兼容：

| 方法 | 路径 | 说明 | 变更 |
| --- | --- | --- | --- |
| GET | `/api/health` | 返回运行模式与阶段元数据 | 后续可增加 `embedding_model`、`vector_store` 字段，不删除现有字段 |
| GET | `/api/projects` | 项目摘要列表 | 不变 |
| PUT | `/api/project/{project_id}` | 更新客户信息 | 不变 |
| POST | `/api/stage/switch` | 切换当前阶段 | **行为变更**：不再重置该阶段对话历史；按 `project_id + stage` 隔离并可恢复 |

---

### 4.10 GET /api/dashboard（首页仪表盘）

从 `projects.json` 实时计算统计和项目卡片，不单独保存冗余统计。

```http
GET /api/dashboard
```

响应不包 `ok`，严格使用：

```json
{
  "stats": {
    "total_projects": 1,
    "active_projects": 1,
    "archived_stages": 0,
    "active_last_7d": 1
  },
  "projects": [
    {
      "project_id": "prj_ac7bcc665f48",
      "client_name": "华东精密制造有限公司",
      "industry": "汽车零部件",
      "production_mode": "备货型 MTS",
      "current_stage": "stage1_research",
      "stage_status": {
        "stage1_research": "in_progress",
        "stage2_data": "not_started",
        "stage3_solution": "not_started",
        "stage4_config": "not_started",
        "stage5_golive": "not_started"
      },
      "last_active_at": "2026-11-09T09:57:59+08:00",
      "last_summary": "客户当前按周排产……",
      "created_at": "2026-11-09T09:57:59+08:00"
    }
  ]
}
```

字段与统计口径：

| 字段/统计 | 说明 |
| --- | --- |
| `stage1_research` … `stage5_golive` | 仪表盘稳定阶段键，与内部 1–5 阶段对应 |
| `current_stage` | 项目 `active_stage` 对应的阶段键 |
| `stage_status` | 五阶段状态：`not_started` / `in_progress` / `archived` |
| `active_projects` | 五阶段未全部归档的项目数 |
| `archived_stages` | 所有项目已归档阶段总数 |
| `active_last_7d` | `last_active_at` 在最近 7 天内的项目数 |
| 排序 | 按 `last_active_at` 倒序 |
| 旧数据 | 缺失时 `last_active_at` 回退 `created_at`，`last_summary` 为空字符串 |

数据更新规则：

- `/api/chat` 成功：更新 `last_active_at`，`last_summary` 优先取本轮用户问题，否则取纠偏内容或 Agent 回答，截断到 50 字。
- `/api/archive` 成功：更新 `last_active_at`，`last_summary` 优先取该阶段最近一条用户消息，否则取归档内容，截断到 50 字。
- `/api/context` 与 `/api/projects` 现有字段不变；项目对象新增字段是向后兼容的。

---

## 5. 错误码总表

| code | HTTP | 可重试 | 说明 |
| --- | --- | --- | --- |
| `INVALID_JSON` | 400 | 否 | 请求体非 JSON 对象 |
| `VALIDATION_ERROR` | 400 | 否 | 必填字段缺失或非法 |
| `INVALID_STAGE` | 400 | 否 | 阶段不合法 |
| `MESSAGE_TOO_LONG` | 400 | 否 | 消息超长 |
| `HISTORY_INVALID` | 400 | 否 | 历史格式非法 |
| `HISTORY_TOO_LONG` | 400 | 否 | 历史条数或总长度超限 |
| `INVALID_REQUEST` | 400 | 否 | 字段组合非法 |
| `NO_FILE` | 400 | 否 | 未上传文件 |
| `PROJECT_NOT_FOUND` | 404 | 否 | 项目不存在 |
| `ROUTE_NOT_FOUND` | 404 | 否 | 路由不存在 |
| `METHOD_NOT_ALLOWED` | 405 | 否 | HTTP 方法错误 |
| `FILE_TOO_LARGE` | 413 | 否 | 文件超过 20 MiB |
| `UNSUPPORTED_FILE_TYPE` | 415 | 否 | 不支持的文件类型 |
| `PARSE_FAILED` | 422 | 是 | 文件解析失败，可修复文件后重试 |
| `EMPTY_DOCUMENT` | 422 | 是 | 无可入库文本 |
| `SCANNED_PDF_UNSUPPORTED` | 422 | 否 | 扫描件 PDF，不支持 OCR |
| `EMBEDDING_FAILED` | 502 | 是 | 嵌入模型失败 |
| `VECTOR_STORE_FAILED` | 500 | 是 | 内存索引读写失败 |
| `LLM_TIMEOUT` | 504 | 是 | LLM 超时 |
| `LLM_AUTH_FAILED` | 502 | 否 | API Key 无效 |
| `LLM_RATE_LIMITED` | 429 | 是 | 上游限流 |
| `LLM_UPSTREAM_ERROR` | 502 | 是 | 上游其它错误 |
| `INTERNAL_ERROR` | 500 | 否 | 未捕获异常 |

非错误状态：

- `NO_RELEVANT_CONTEXT`：不是错误码；`rag` 模式检索为空时返回 `200`，`sources: []`，固定答复。
- `SUMMARY_FALLBACK`：不是错误码；上传成功但摘要降级时出现在 `warnings` 中。

---

## 6. 兼容性说明

### 6.1 现有前端不需要立刻重写

现有前端调用 `/api/chat` 的请求体为：

```json
{
  "project_id": "prj_xxx",
  "stage": 1,
  "user_input": "……",
  "correction": "……"
}
```

契约兼容处理：

- `user_input` 作为 `message` 的别名。
- 只传 `user_input` / `correction` 时默认走 `stage` 模式。
- 响应继续保留 `ok`、`reply`、`project`、`mode`、`warning`、`history_reset`。
- 错误响应继续保留 `error` 字符串，现有 `api()` 封装无需立即修改。
- `/api/context`、`/api/archive`、`/api/project` 不变。

### 6.2 第二阶段前端增量改动

- 对话区增加来源标签和片段展开；只读取新增的 `sources`。
- 增加上传区、解析状态和切片数展示；调用 `/api/upload`、`/api/documents/{project_id}`。
- 文档问答调用 `/api/chat` 时显式发送 `message`、`history`、`answer_mode: "rag"`。
- 阶段一表单生成和纠偏重生成继续发送 `answer_mode: "stage"`，保留现有能力。
- 不重写五阶段导航、归档和导出。

### 6.3 行为变化

| 项目 | 之前 | 之后 |
| --- | --- | --- |
| `/api/chat` 输入 | `stage + user_input + correction` | 新增 `message + history + answer_mode`；旧字段兼容 |
| `/api/chat` 输出 | 无 `sources` | 新增 `sources`、`grounded` |
| 无检索结果 | 仍由 LLM 基于上下文生成 | `rag` 模式固定返回“当前知识库中没有相关信息” |
| 错误格式 | `{ok, error}` | `{ok, code, message, data, error}` |
| 错误码 | 无稳定枚举 | 见第 5 节 |

---

## 7. RAG 实现约定（第二阶段固定选择）

| 项目 | 选择 | 说明 |
| --- | --- | --- |
| 嵌入方式 | 外部 OpenAI-compatible Embedding API | 默认 SiliconFlow `BAAI/bge-m3`，不加载本地模型 |
| 无 Embedding Key | 内存关键词检索兜底 | 仍可上传和检索，但语义能力降级 |
| 向量索引 | 内存切片索引 | 快照到 `storage/vector_index.json`，不使用 Chroma |
| 项目隔离 | 内存索引按 `project_id` 过滤 | 单进程一致性 |
| 切片长度 | 600–900 字符，重叠约 100 字符 | 标题/段落/表格行组优先 |
| 检索 | top-k=5，余弦相似度；语义分 ≥0.35 召回，<0.50 的结果需要问题关键词命中才保留 | 通过 `RAG_TOP_K`、`RAG_MIN_SCORE`、`RAG_RELEVANCE_SCORE` 调整 |
| 摘要 | DeepSeek 按阶段生成；失败时抽取式回退 | 不阻断文档入库；无 Key 时明确返回 warning |
| 扫描 PDF | 返回 `SCANNED_PDF_UNSUPPORTED` | 不实现 OCR |
| 无 Key 演示模式 | 用检索片段做抽取式回答 | 仍返回 `sources`，不编造 |

实际使用的环境变量：

```bash
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-chat
EMBEDDING_API_BASE=https://api.siliconflow.cn/v1
EMBEDDING_API_KEY=sk-...
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_TIMEOUT=60
RAG_TOP_K=5
RAG_MIN_SCORE=0.35
RAG_RELEVANCE_SCORE=0.50
MAX_UPLOAD_MB=20
```

> 不下载任何 Hugging Face / torch 模型。配置外部 Embedding Key 后使用语义检索；
> 未配置时使用内存关键词检索兜底。

---

## 8. 已确认事项

以下 5 项已由项目方确认并采纳：

1. `answer_mode` 兼容机制：默认 `rag`；旧字段 `user_input` / `correction` 自动走 `stage`，保留五阶段交付物生成。
2. `history` 传入时以客户端为准并替换该阶段已存历史；不传时读取服务端历史。
3. 错误响应统一返回 `{ok, code, message, data, error}`，`error` 保留为兼容别名。
4. 第三阶段 SSE 预留独立路由 `POST /api/chat/stream`，现有 `/api/chat` 保持非流式 JSON。
5. 嵌入模型使用外部 OpenAI-compatible API；未配置 Key 时使用关键词检索兜底。top-k=5，语义阈值 0.35，弱语义结果需关键词命中，避免“食堂菜单”类问题误召回。

---

## 8.1 第二阶段实现状态

- 已完成 `POST /api/upload`：DOCX / XLSX / 数字版 PDF 解析、切片、外部 Embedding（可选）、内存索引、阶段摘要。
- 已完成 `GET /api/documents/{project_id}`：文档状态、切片数、摘要和失败原因。
- 已完成 `POST /api/chat` 的 RAG 路径：检索、引用来源、无信息兜底、历史存储与兼容字段。
- 已完成前端增量：上传入口、知识库文档列表、`sources` 标签与片段弹窗、文档问答/阶段生成切换。
- 已验证：模拟排产 Excel 提问“排产周期”返回 `排产计划.xlsx` 片段；问“食堂菜单”返回“当前知识库中没有相关信息”。
- 当前限制：未实现 OCR；阶段 Agent 与全局助手均已支持 SSE；DeepSeek Key 未配置时使用抽取式/本地兜底回答；任意阶段归档后均可导出 Word / Excel。

### 8.2 全局 AI 小助手增量

- 前端右下角悬浮机器人 FAB + 独立抽屉，不改变五阶段布局。
- `POST /api/assistant/chat` 支持 JSON 与 SSE；`GET /api/assistant/history/{project_id}` 恢复独立历史。
- 检索复用阶段 Agent 的 `retrieval_service`、同一内存索引、同一 top-k 和相关性门槛。
- 无命中时转通用大模型回答；当前无 Key 环境下使用本地兜底，配置 Key 后由 DeepSeek 完成通用回答。
- 独立历史存放在 `storage/assistant_history.json`，不读写阶段 `messages`。

---

### 8.3 第三阶段实现状态

- `POST /api/chat/stream` 已实现，事件为 `meta / sources / delta / done / error`。
- 前端阶段对话已切换为 SSE，逐字渲染并展示来源；阶段一表单生成、纠偏重生成、归档前输出均走流式。
- `/api/stage/switch` 已改为保留历史；切回阶段后从项目上下文恢复对话。
- 阶段历史继续按 `project_id + stage` 隔离，最多保留最近 20 条传给模型。
- 验证：mock LLM 捕获到第三轮请求携带前两轮问答；真实 DeepSeek 环境需配置 Key 后可进一步验证语义指代。

---

## 9. 第三阶段 SSE 契约（已实现）

> 全局 AI 小助手 SSE 见 4.7；阶段 Agent SSE 见 4.1.8。本节保留作为接口速查。

如果确认采用独立路由，第三阶段将新增：

```http
POST /api/chat/stream
Content-Type: application/json
Accept: text/event-stream
```

请求体与 `POST /api/chat` 相同。

SSE 事件：

| event | data 示例 | 说明 |
| --- | --- | --- |
| `meta` | `{"project_id":"prj_...","stage":2,"answer_mode":"rag"}` | 流开始 |
| `sources` | `{"sources":[...]}` | 先返回来源，前端可提前渲染 |
| `delta` | `{"text":"根据"}` | 逐字/逐块文本 |
| `done` | `{"reply":"...","sources":[],"grounded":true}` | 完成 |
| `error` | `{"code":"LLM_TIMEOUT","message":"...","data":null}` | 流内错误 |

`/api/chat` 非流式接口保持现有 JSON 契约不变，供兼容与自动化测试使用。

---

## 10. 第四阶段：工程规范与可观测性

### 10.1 分层目录

```text
app.py                 # Flask 应用工厂、Blueprint 注册、统一异常处理、请求日志钩子
routes/                # 参数校验与响应；不写业务逻辑
services/              # 对话、文档、检索、嵌入、LLM、项目、指标与可观测性
data/                  # 项目上下文、文档登记、助手历史、内存索引
tests/                 # pytest 单元测试
```

### 10.2 请求日志与请求 ID

- 每个请求生成 `X-Request-ID`，并在响应头返回。
- 非流式 API 在 `after_request` 记录；SSE 在生成器结束时记录，覆盖完整耗时。
- 日志字段：

```text
api_request method=POST path=/api/chat/stream status=200 duration_ms=... request_id=...
prompt_tokens=... completion_tokens=... total_tokens=... tokens_estimated=... stream=True stream_error=False
```

- DeepSeek 非流式调用使用 API 返回的真实 usage。
- 流式调用优先使用上游 usage；缺失时按字符数估算，并在日志中标记 `tokens_estimated=true`。

### 10.3 统一错误格式

错误响应统一为：

```json
{
  "ok": false,
  "code": "PROJECT_NOT_FOUND",
  "message": "项目不存在。",
  "data": null,
  "error": "项目不存在。"
}
```

`code`、`message`、`data` 为稳定契约字段；`ok` 和 `error` 为兼容字段。

### 10.4 pytest 覆盖

运行：

```bash
pytest -q
```

当前覆盖：

- 文档解析与切片（sheet、行号、chunk index、长度）。
- 检索结果映射、相似度阈值、关键词兜底。
- 对话服务检索到/检索不到的行为。
- 多轮历史传递与阶段 SSE 事件持久化。
- 上下文读写、归档、阶段切换历史保留、客户端 history 替换。
- 统一错误格式与 `X-Request-ID`。


---

## 11. 项目管理、对话历史与来源追溯（v1.6）

详细方案见 [project-crud-history-sources-design.md](project-crud-history-sources-design.md)。

### 11.1 项目 CRUD

```http
PATCH /api/project/<project_id>
Content-Type: application/json

{"client_name": "客户名", "industry": "行业"}
```

- 允许 `client_name`、`industry`、`production_mode`；至少一个字段。
- 返回 `project` 与 `summary`；`summary` 含 `created_at`。
- `PUT /api/project/<project_id>` 保留原有契约（仍支持 `pain_points`）。

```http
DELETE /api/project/<project_id>
```

- 删除内存切片索引、文档登记、上传文件、助手历史、项目上下文和历史。
- 只有全部步骤成功才返回 200；失败保留项目记录并返回 `failed_step`。
- 删除日志位于 `storage/deletions/`，启动时恢复未完成删除。

### 11.2 对话历史

`GET /api/context/<project_id>` 的每个阶段新增：

```json
{
  "conversation_history": [
    {
      "role": "user",
      "content": "问题",
      "timestamp": "ISO8601"
    },
    {
      "role": "assistant",
      "content": "回答",
      "sources": [],
      "timestamp": "ISO8601"
    }
  ],
  "messages": []
}
```

- `conversation_history` 是唯一持久化字段。
- `messages` 为兼容别名，由服务端生成，不写入 storage。
- 旧 `messages` 自动迁移，缺字段返回空数组。

`POST /api/conversation/clear`：

```json
{"project_id": "prj_xxx", "stage": 1}
```

- 只清空该阶段历史；归档保留。
- 有归档保持 `archived`，无归档改为 `not_started`。

`POST /api/chat` / `/api/chat/stream` 新增可选：

```json
{"regenerate": true}
```

- 替换最后一条 assistant 消息，不追加 user 消息。
- 没有可替换回答返回 `NO_ASSISTANT_MESSAGE`。

### 11.3 来源

`sources` 新字段：

```json
{
  "file_name": "排产表.xlsx",
  "chunk_id": "doc_xxx:0002",
  "snippet": "命中片段",
  "page": 3,
  "row": 12,
  "score": 0.81,
  "file": "排产表.xlsx",
  "doc_id": "doc_xxx",
  "stage": 1,
  "sheet": "排产参数",
  "row_start": 12,
  "row_end": 14,
  "chunk_index": 2
}
```

旧字段保留，新字段优先。

新增只读来源详情接口：

```http
GET /api/source/<project_id>?chunk_id=doc_xxx:0002
```

返回当前切片全文、前后各一个切片及文件/页码/行号定位。用于前端来源弹窗，不提供原文编辑。

### 11.4 新增错误码

| code | HTTP | 说明 |
| --- | --- | --- |
| `PROJECT_DELETE_IN_PROGRESS` / `PROJECT_BUSY` | 409 | 项目正在删除 |
| `VECTOR_DELETE_FAILED` | 500 | 向量删除失败 |
| `DOCUMENT_DELETE_FAILED` | 500 | 文档/上传文件删除失败 |
| `ASSISTANT_HISTORY_DELETE_FAILED` | 500 | 助手历史删除失败 |
| `PROJECT_DELETE_FAILED` | 500 | 项目最终记录删除失败 |
| `NO_ASSISTANT_MESSAGE` | 400 | 重生成时无 Agent 回答 |
| `SOURCE_NOT_FOUND` | 404 | 来源不存在或不属于该项目 |


---

## 12. Word / Excel 导出（v1.7）

任意阶段归档后均可导出两种格式，不再受阶段 `export_type` 限制。

### 12.1 POST /api/export/word

```http
POST /api/export/word
Content-Type: application/json

{"project_id": "prj_xxx", "stage": 1}
```

返回 `application/vnd.openxmlformats-officedocument.wordprocessingml.document` 文件流。

### 12.2 POST /api/export/excel

```http
POST /api/export/excel
Content-Type: application/json

{"project_id": "prj_xxx", "stage": 1}
```

返回 `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` 文件流。

### 12.3 文件名

```text
{客户名称}_{阶段名称}_交付物.docx
{客户名称}_{阶段名称}_交付物.xlsx
```

服务端同时提供 RFC 5987 `filename*=UTF-8''...`，确保中文文件名下载正确。

### 12.4 自适应解析

Word：

- `#` / `##` / `###` → Heading 1/2/3。
- `-` → List Bullet；`1.` → List Number。
- Markdown 表格 → Word Table + Table Grid，表头加粗。
- 普通段落 → Normal。
- 顶部标题：`{客户名称} - {阶段名称} - 交付物`。

Excel：

- 标题写入第一列，加粗并设置背景色。
- 表格按行列写入，表头加粗、填充色并冻结表头行。
- 普通文本和列表写入第一列。
- 列宽按内容宽度自适应。

### 12.5 异常与降级

- 当前阶段未归档：`409 EXPORT_NOT_ARCHIVED`，提示“请先归档当前阶段内容，再导出。”
- 归档内容为空：`400 EMPTY_ARCHIVE`。
- 项目不存在：`404 PROJECT_NOT_FOUND`。
- Markdown 解析/排版异常：不返回 500，降级为原始纯文本写入 Word 或 Excel 第一列，确保文件可下载且内容不丢。

### 12.6 前端

- 归档按钮旁始终显示“导出 Word”和“导出 Excel”。
- 未归档时点击提示“请先归档当前阶段内容，再导出”。
- 已归档时先弹窗：“文件为 AI 初稿，请校验后发送客户。”
- 确认后直接触发浏览器文件下载。


---

## 13. 全生命周期扩展（v1.8）

### 13.1 售前快速评估

```http
POST /api/presales
Content-Type: application/json

{"industry":"汽车零部件","production_mode":"订单型 MTO","pain_points":"插单频繁","scale":"3 个工厂"}
```

- 创建轻量 `record_type=presales` 记录，不建立五阶段、不进入首页统计。
- 返回 `presales_id`、完整记录和 Markdown 评估初稿。

补充接口：

```http
GET /api/presales
GET /api/presales/{presales_id}
POST /api/presales/{presales_id}/export/word
```

页面：

```http
GET /presales/{presales_id}
```

### 13.2 上线后跟踪

阶段五归档后解锁。项目增加：

```json
"post_launch": {
  "unlocked_at": "ISO8601",
  "issues": []
}
```

```http
GET /api/issues/{project_id}
POST /api/issues/{project_id}
PATCH /api/issues/{project_id}/{issue_id}
POST /api/chat/issues
POST /api/export/issues
```

- 未解锁新增问题返回 `409 POST_LAUNCH_LOCKED`。
- 问题字段：`description`、`severity`（high/medium/low）、`status`（pending/in_progress/resolved）。
- `POST /api/chat/issues` 读取当前问题列表，支持需求池摘要和排查建议；无 Key 时本地兜底。
- `POST /api/export/issues` 返回上线问题 Excel。

### 13.3 人工确认门禁

`POST /api/archive` 增加可选 `confirmations`：

阶段一：

```json
{"constraint_priority_confirmed": true}
```

阶段三：

```json
{"confirmer_and_date": "张伟 / 2026-11-09"}
```

- 阶段一/三缺少确认返回 `400 GATE_CONFIRMATION_REQUIRED`。
- 确认记录写入 `stages[stage].gate_confirmations`。
- 手动归档同样受后端校验。
- 阶段五输出必须包含“跨部门上线协调人已确认切换时间”；缺失时服务端补齐。

### 13.4 历史模板

```http
GET /api/templates
```

- 只返回 delivery 项目阶段一/五的已归档内容。
- `template_id = {project_id}:{stage}:{version}`。

`POST /api/project` 增加可选 `template_id`，将来源内容写入新项目：

```json
"stages": {
  "1": {
    "initial_context": {
      "content": "历史模板内容",
      "source_template_id": "prj_old:1:1",
      "source_project_id": "prj_old",
      "source_stage": 1,
      "injected_at": "ISO8601"
    }
  }
}
```

- 阶段状态保持 `not_started`，不复制历史对话或归档状态。
- 工作台展示“历史模板带入（未归档）”。

### 13.5 兼容字段

旧项目自动补齐：

- `record_type = "delivery"`
- `post_launch = {"unlocked_at": null, "issues": []}`
- `stages[*].gate_confirmations = []`
- `stages[*].initial_context = null`
