# 项目管理 CRUD、对话历史恢复、RAG 来源追溯 · 接口与数据模型设计

> 状态：**已实现（2026-11-09）**。  
> 本文只描述契约、数据结构、原子性方案和交互，不包含实现代码。  
> 不改变五阶段工作台、RAG 检索核心、文件解析和导出模块的现有职责。

---

## 1. 当前数据结构与改造边界

当前 `projects.json` 中一个项目的结构是：

```json
{
  "id": "prj_xxx",
  "client_name": "客户",
  "industry": "行业",
  "production_mode": "生产模式",
  "pain_points": "痛点",
  "active_stage": 1,
  "created_at": "ISO8601",
  "updated_at": "ISO8601",
  "last_active_at": "ISO8601",
  "last_summary": "",
  "stages": {
    "1": {
      "status": "in_progress",
      "messages": [
        {
          "role": "user",
          "content": "问题",
          "ts": "ISO8601"
        },
        {
          "role": "assistant",
          "content": "回答",
          "ts": "ISO8601",
          "sources": []
        }
      ],
      "archive": {
        "content": "",
        "archived_at": null,
        "version": 0
      }
    }
  }
}
```

本次改造的核心是：

- 阶段对话存储从 `messages` 迁移为语义更明确的 `conversation_history`。
- 来源结构从当前的 `file/snippet/doc_id/stage/score` 扩展为可定位、可追溯的完整来源。
- 项目卡片增加管理操作，但保留是否展示、如何展示的现有视觉。

---

## 2. 项目管理 CRUD

### 2.1 数据模型

每个项目增加/保留以下字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 项目 ID，主键 |
| `client_name` | string | 客户名称，1–80 字符 |
| `industry` | string | 行业，可为空，最多 80 字符 |
| `production_mode` | string | 生产模式，可为空，最多 80 字符 |
| `pain_points` | string | 核心痛点，保留 |
| `created_at` | ISO8601 | 创建时间，新增到项目卡片 |
| `updated_at` | ISO8601 | 任意项目修改时间 |
| `last_active_at` | ISO8601 | 最近对话/归档时间 |
| `last_summary` | string | 最近对话摘要，最多 50 字 + 省略号 |
| `active_stage` | int | 当前阶段 |
| `stages` | object | 五阶段状态、历史、归档 |

`created_at` 兼容规则：

1. 已有 `created_at`：直接使用。
2. 缺失：依次回退 `updated_at` → `last_active_at` → 当前时间。
3. 缺失时补写回 `projects.json`，不因缺字段报错。
4. `GET /api/dashboard` 的每个项目卡片增加 `created_at`。

### 2.2 PATCH /api/project/{project_id}

用途：重命名项目、修改行业/生产模式。

#### 请求

```http
PATCH /api/project/prj_ac7bcc665f48
Content-Type: application/json
```

```json
{
  "client_name": "华东精密制造有限公司",
  "industry": "汽车零部件"
}
```

字段均为可选，但至少提供一个：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `client_name` | string | 必填非空（如果提供），1–80 字符 |
| `industry` | string | 可为空，最多 80 字符 |
| `production_mode` | string | 可为空，最多 80 字符 |

- 不允许通过该接口修改 `pain_points`、`stages`、`active_stage`。
- 未知字段返回 `400 VALIDATION_ERROR`，避免静默忽略。
- 更新 `updated_at`。
- **不更新 `last_active_at`**：项目改名不是对话/归档活动，避免影响“最近 7 天活跃”和排序。

#### 响应

HTTP `200`

```json
{
  "ok": true,
  "project_id": "prj_ac7bcc665f48",
  "project": {
    "id": "prj_ac7bcc665f48",
    "client_name": "华东精密制造有限公司",
    "industry": "汽车零部件",
    "production_mode": "备货型 MTS",
    "active_stage": 1,
    "created_at": "2026-11-09T09:57:59+08:00",
    "updated_at": "2026-11-09T13:45:00+08:00",
    "last_active_at": "2026-11-09T09:57:59+08:00",
    "last_summary": "",
    "stages": {}
  },
  "summary": {
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
    "last_summary": "",
    "created_at": "2026-11-09T09:57:59+08:00"
  }
}
```

`summary` 与 `GET /api/dashboard` 的项目卡片结构一致，前端可直接替换卡片。

#### 错误码

| code | HTTP | 场景 |
| --- | --- | --- |
| `PROJECT_NOT_FOUND` | 404 | 项目不存在 |
| `VALIDATION_ERROR` | 400 | 无可更新字段、字段过长、未知字段 |
| `PERSIST_FAILED` | 500 | `projects.json` 写入失败 |

### 2.3 DELETE /api/project/{project_id}

删除范围：

- Chroma 中 `metadata.project_id == project_id` 的所有向量。
- `storage/documents.json` 中该项目文档登记。
- `storage/uploads/` 中该项目上传的原始文件。
- `storage/assistant_history.json` 中该项目助手历史。
- `projects.json` 中项目记录，其中包含阶段上下文、`conversation_history`、归档内容。
- 项目删除后，任何 `/workbench/<id>` 请求返回首页并提示。

#### 响应

HTTP `200`

```json
{
  "ok": true,
  "project_id": "prj_ac7bcc665f48",
  "deleted": {
    "vectors": 12,
    "documents": 2,
    "uploads": 2,
    "assistant_messages": 6,
    "conversation_messages": 8
  }
}
```

`deleted` 为诊断信息，前端不依赖具体数量。

#### 错误码

| code | HTTP | 场景 |
| --- | --- | --- |
| `PROJECT_NOT_FOUND` | 404 | 项目不存在 |
| `PROJECT_DELETE_IN_PROGRESS` | 409 | 已有删除任务 |
| `VECTOR_DELETE_FAILED` | 500 | Chroma 删除失败 |
| `DOCUMENT_DELETE_FAILED` | 500 | 文档登记/上传文件删除失败 |
| `ASSISTANT_HISTORY_DELETE_FAILED` | 500 | 助手历史删除失败 |
| `PROJECT_DELETE_FAILED` | 500 | 项目记录最终删除失败；`data.failed_step` 指明阶段 |

错误响应示例：

```json
{
  "ok": false,
  "code": "PROJECT_DELETE_FAILED",
  "message": "删除项目失败：项目上下文写入失败，请重试。",
  "data": {
    "project_id": "prj_ac7bcc665f48",
    "failed_step": "projects_json",
    "retryable": true
  },
  "error": "删除项目失败：项目上下文写入失败，请重试。"
}
```

### 2.4 删除原子性方案

#### 先说明限制

Chroma、文件系统、`projects.json` 不在同一个事务中，**无法实现数据库意义上的严格 ACID 原子删除**。如果声称“绝对原子”，实现会掩盖失败风险。

建议采用：

> **删除日志 + 有序删除 + 单项目锁 + 失败可重试 + 启动恢复**

保证效果：

- 接口只有在全部步骤成功后才返回 `200`。
- 前端只有收到 `200` 才移除卡片。
- 任一步失败，项目记录仍保留在 `projects.json`，卡片不会消失，接口返回明确失败步骤。
- 不会出现“卡片已经消失，但项目数据仍在”的 UI 不一致。

#### 当前结构的特殊说明

目前项目上下文、阶段 `conversation_history`、归档内容都存放在 `projects.json` 的同一项目对象中，因此不可能“先物理删除上下文，再单独删除 projects.json 记录”。建议将删除顺序解释为：

1. 删 Chroma 向量。
2. 删文档登记、原始上传文件、独立助手历史。
3. 原子移除 `projects.json` 中的项目记录（上下文、阶段历史、归档随记录一起移除）。
4. 清理删除日志。

这保留了原需求的意图：**先断开外部引用，最后提交项目记录删除**。

#### 删除日志

路径：

```text
storage/deletions/<project_id>.json
```

内容：

```json
{
  "project_id": "prj_xxx",
  "started_at": "ISO8601",
  "step": "vector_store",
  "snapshot": {
    "project": {},
    "documents": [],
    "assistant_history": []
  }
}
```

日志用于：

- 崩溃后知道删除停在哪个步骤。
- 启动时恢复/继续删除。
- 失败后重试。

日志包含快照，但快照可能含客户信息，仍保存在本地 `storage/` 下，不进入 `projects.json`。

#### 删除步骤

0. **校验与加锁**
   - 项目存在且不在删除中。
   - 获取该项目的删写锁；将项目标记为 `deleting`（内存状态）。
   - 期间 `/api/chat`、`/api/upload`、`/api/archive`、PATCH 对同一项目返回 `409 PROJECT_BUSY`。
   - 写入删除日志，`step=prepared`。

1. **删 Chroma 向量**

```text
collection.delete(where={"project_id": project_id})
```

   - 这是外部数据中风险最高的一步，先做。
   - 失败：项目/文档/历史都未改动；写日志 `failed_step=vector_store`；返回 `VECTOR_DELETE_FAILED`。

2. **删文档登记、原始文件和助手历史**
   - 删除 `documents.json` 中该项目的文档记录。
   - 删除 `storage/uploads/` 中对应文件。
   - 删除 `assistant_history.json` 中该项目记录。
   - 任一步失败：写日志；项目记录保留；返回对应错误。
   - 建议每个删除操作幂等：文件已不存在视为成功。

3. **删 projects.json 记录**
   - 使用现有 `temp + os.replace` 原子写入。
   - 这一步同时删除阶段上下文、`conversation_history` 和归档。
   - 失败：项目记录仍在；写日志；返回 `PROJECT_DELETE_FAILED`，`failed_step=projects_json`。
   - 成功：项目从上下文存储消失。

4. **清理删除日志**
   - 删除日志失败只记录警告，不影响接口成功；下次启动可清理。

#### 失败与恢复

- 删除日志保留在所有非最终失败场景。
- 服务启动时扫描 `storage/deletions/*.json`：
  - 项目仍在 `projects.json`：从日志步骤继续删除。
  - 项目已不在：继续清理文档/文件/助手历史和日志。
- 由于 `projects.json` 最后删除，只要项目记录存在，就不会出现“项目已消失但向量/文档仍在”的最终状态。
- 步骤 1 成功、步骤 2 失败时，项目会保留但知识库向量已丢失；这是跨存储删除无法完全避免的中间态。接口会明确返回失败步骤，且项目卡片保留，用户可重试完成删除。不会把它伪装成成功。

#### 并发控制

- 单进程使用项目级 `RLock` + `deleting` 集合。
- 当前 Gunicorn 为单 worker，满足本地单用户部署。
- 未来多 worker 需要文件锁；本次明确不做。

### 2.5 前端项目卡片操作

- 每张卡片右上角增加 `⋯` 更多按钮，点击阻止卡片跳转。
- 菜单项：`重命名`、`删除`。
- 菜单外点击、Esc 关闭。
- 重命名：
  - 轻量弹窗，字段：客户名称、行业。
  - 保存调用 PATCH。
  - 成功用响应 `summary` 替换当前卡片，并刷新统计（统计值不变，但统一走渲染）。
  - `created_at` 不展示为操作项，仅作为数据字段保留。
- 删除：
  - 弹确认框，文案固定：
    > 删除后项目上下文、对话历史、已入库文档将一并移除，且不可恢复，确定删除？
  - 确认期间按钮禁用。
  - 成功：从本地数组移除卡片，重新计算 4 个统计值；统计基于本地剩余卡片即可，不依赖二次请求。
  - 失败：卡片保留，Toast 显示接口 `message`，浮层不关闭。
- 删除或重命名失败时不清空列表、不移除卡片。

---

## 3. 对话历史存储与恢复

### 3.1 存储结构

每个阶段：

```json
{
  "status": "in_progress",
  "conversation_history": [
    {
      "role": "user",
      "content": "这个文件里排产周期是多少？",
      "timestamp": "2026-11-09T13:00:00+08:00"
    },
    {
      "role": "assistant",
      "content": "排产周期为 7 天。",
      "sources": [
        {
          "file_name": "排产计划示例.xlsx",
          "chunk_id": "doc_xxx:0000",
          "snippet": "排产周期 7 天",
          "row": 2,
          "score": 0.78
        }
      ],
      "timestamp": "2026-11-09T13:00:02+08:00"
    }
  ],
  "archive": {
    "content": "",
    "archived_at": null,
    "version": 0
  }
}
```

字段定义：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `role` | string | `user` / `assistant` |
| `content` | string | 用户可见内容；用户消息取界面显示文本，不存内部拼装 prompt |
| `sources` | Source[] | 仅 assistant；无来源时 `[]` |
| `timestamp` | ISO8601 | 消息时间 |

- 用户消息不写 `sources` 字段，或写空数组；建议 user 消息不出现 `sources`，保持最小结构。
- `conversation_history` 只存该阶段，顺序为追加顺序。
- 归档内容仍放在 `archive`，不混入历史。
- 清空历史不清空归档。

### 3.2 旧数据迁移

现有 `stages[*].messages` 迁移到 `conversation_history`：

- user：`content = display || content`，`timestamp = ts || 当前时间`。
- assistant：`content`、`sources` 标准化，`timestamp = ts || 当前时间`。
- 迁移后 `conversation_history` 为唯一持久化字段。
- 为兼容现有客户端，`GET /api/context` 同时返回：

```json
{
  "conversation_history": [],
  "messages": []
}
```

- `messages` 为**只读兼容别名**，由 `conversation_history` 映射生成，不再单独持久化。
- 旧项目缺少两者时返回空数组，不报错。

### 3.3 GET /api/context/{project_id}

每个阶段新增 `conversation_history`，其余字段保持不变：

```json
{
  "ok": true,
  "project": {
    "id": "prj_xxx",
    "stages": {
      "1": {
        "status": "in_progress",
        "conversation_history": [],
        "messages": [],
        "archive": {}
      }
    }
  }
}
```

`messages` 别名建议保留一个版本，便于现有前端平滑切换；切换完成后可在下一个主版本移除。

### 3.4 /api/chat 写入规则

每次成功生成后：

1. 追加一条 user 消息。
2. 追加一条 assistant 消息，包含 `sources`。
3. 同一事务中更新 `last_active_at`、`last_summary`。
4. 返回/SSE `done` 中携带更新后的项目对象。

前端以服务端返回的项目为唯一数据源：

- 非流式：请求成功后用响应 `project` 覆盖当前项目。
- 流式：收到 `done` 后用 `done.project` 覆盖。
- 不在客户端再次追加一份历史，避免重复。

### 3.5 重新生成

现有重新生成会再次发送一轮消息，造成历史重复。建议在 `/api/chat` 与 `/api/chat/stream` 增加可选字段：

```json
{
  "project_id": "prj_xxx",
  "stage": 1,
  "regenerate": true,
  "correction": "请更关注插单影响",
  "answer_mode": "stage"
}
```

规则：

- `regenerate=true` 时：
  - 从该阶段 `conversation_history` 找最后一条 user 消息作为本轮用户问题。
  - 找最后一条 assistant 消息；不存在则返回 `400 NO_ASSISTANT_MESSAGE`。
  - 只替换最后一条 assistant 的 `content`、`sources`、`timestamp`。
  - 不追加 user 消息。
  - 可携带 `correction`，仅用于本次生成。
  - 更新 `last_active_at`；`last_summary` 仍保持最后一条 user 问题。
- 非 `regenerate` 行为不变。
- SSE `done` 同样返回更新后的 `project`。

前端点击“重新生成”：

- 不再先本地追加 user 气泡。
- 调用 `regenerate=true`。
- 用返回的项目覆盖当前阶段历史。

### 3.6 清空当前阶段对话

新增：

```http
POST /api/conversation/clear
Content-Type: application/json

{
  "project_id": "prj_xxx",
  "stage": 1
}
```

行为：

- 仅清空 `stages[stage].conversation_history`。
- `archive` 完全不变。
- 状态处理：
  - 有归档内容：保持 `archived`。
  - 无归档内容：改为 `not_started`。
- `updated_at` 更新。
- `last_active_at` 保持不变，避免清空历史被误判为活跃。
- `last_summary` 重新计算：
  - 从所有阶段剩余历史中找时间最新的 user 消息，生成 50 字摘要。
  - 没有剩余 user 消息则为空字符串。
- 不删除全局 AI 助手历史。

响应：

```json
{
  "ok": true,
  "project_id": "prj_xxx",
  "stage": 1,
  "conversation_history": [],
  "project": {}
}
```

错误码：

| code | HTTP | 场景 |
| --- | --- | --- |
| `PROJECT_NOT_FOUND` | 404 | 项目不存在 |
| `INVALID_STAGE` | 400 | stage 非 1–5 |
| `PERSIST_FAILED` | 500 | 写入失败 |

### 3.7 前端恢复与展示

- 进入 `/workbench/<project_id>`：加载 `/api/context`，初始化每个阶段的 `conversation_history`。
- 切换阶段：只切换当前展示数据，不修改历史，不清空。
- 对话区顶部固定显示：

```text
本阶段对话记录   4 条    [清空]
```

- 空历史显示“本阶段暂无对话记录”，而不是空白卡片。
- 清空按钮弹轻量确认：
  > 清空本阶段对话记录？已归档内容不会受影响。
- 清空成功后：
  - 当前阶段历史变为 `[]`；
  - 归档区域不变；
  - 对话区空状态显示；
  - 统计卡如果受到影响，后续回首页刷新即可。
- 每条 assistant 历史保留 `sources`，可复用来源标签/抽屉能力。

---

## 4. RAG 来源可追溯

### 4.1 Source 数据结构

`/api/chat`、`/api/chat/stream`、`conversation_history[*].sources` 使用同一结构：

```json
{
  "file_name": "排产计划示例.xlsx",
  "chunk_id": "doc_ab12cd34:0002",
  "snippet": "排产周期 7 天；计划冻结期 1 天。",
  "page": 3,
  "row": 12,
  "score": 0.8123,

  "file": "排产计划示例.xlsx",
  "doc_id": "doc_ab12cd34",
  "stage": 2,
  "sheet": "排产参数",
  "row_start": 12,
  "row_end": 14,
  "paragraph_start": null,
  "paragraph_end": null,
  "chunk_index": 2
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `file_name` | 是 | 原始文件名 |
| `chunk_id` | 是 | Chroma 切片 ID，稳定定位 |
| `snippet` | 是 | 命中片段，最多 500 字 |
| `page` | 否 | PDF 页码 |
| `row` | 否 | XLSX 起始行 |
| `score` | 是 | 相似度，0–1 |
| `file` | 兼容 | 旧字段，等于 `file_name` |
| `doc_id` | 兼容 | 文档 ID |
| `stage` | 兼容 | 入库阶段 |
| `sheet` | 兼容 | XLSX sheet |
| `row_start` / `row_end` | 兼容 | 行范围 |
| `paragraph_start` / `paragraph_end` | 兼容 | DOCX 段落范围 |
| `chunk_index` | 兼容 | 文档内切片序号 |

实现要点：

- `data/vector_store.query_chunks()` 的 `include` 增加 `"ids"`。
- 返回 match 增加 `chunk_id`。
- `retrieval_service` 映射时同时输出新字段和旧字段。
- 旧客户端仍可读 `file/snippet/doc_id/score`。
- 无来源时 `sources = []`，不生成占位来源。

### 4.2 来源标签

标签文案：

| 来源类型 | 文案 |
| --- | --- |
| XLSX | `《排产表.xlsx》第 3 行` |
| XLSX 行范围 | `《排产表.xlsx》第 3–5 行` |
| PDF | `《方案.pdf》第 3 页` |
| DOCX | `《调研纪要.docx》第 3 段` |
| 无定位信息 | `《文件名》片段 3` |

- 标签只展示定位信息，不展示分数。
- 多个来源按检索顺序展示。
- 来源为空时显示轻量提示：

```text
本次回答未引用知识库内容
```

- 不渲染空标签容器。

### 4.3 来源详情接口（新增，供弹窗使用）

为了让弹窗显示“前后各扩展一段”并高亮命中片段，聊天响应不应塞入整篇上下文。新增只读接口：

```http
GET /api/source/{project_id}?chunk_id=doc_ab12cd34:0002
```

响应：

```json
{
  "ok": true,
  "source": {
    "file_name": "排产计划示例.xlsx",
    "chunk_id": "doc_ab12cd34:0002",
    "snippet": "排产周期 7 天；计划冻结期 1 天。",
    "text": "完整切片原文……",
    "page": null,
    "row": 12,
    "sheet": "排产参数",
    "before": {
      "chunk_id": "doc_ab12cd34:0001",
      "text": "前一段原文……",
      "row": 10,
      "page": null
    },
    "after": {
      "chunk_id": "doc_ab12cd34:0003",
      "text": "后一段原文……",
      "row": 15,
      "page": null
    }
  }
}
```

规则：

- 校验 chunk 的 `project_id` 与路径一致。
- `before/after` 取同一文档相邻 `chunk_index`；不存在则为 `null`。
- 不返回向量，不返回其他项目数据。
- 错误：
  - `SOURCE_NOT_FOUND` 404
  - `VECTOR_STORE_FAILED` 500
- 不要求打开原始文件预览。

### 4.4 前端来源抽屉

将现有“来源片段”弹窗升级为右侧抽屉，或保留弹窗但增加上下文区：

```text
Source Drawer
├── Header
│   ├── 《排产表.xlsx》第 3 行
│   └── 关闭
├── Location
│   ├── 文件：排产表.xlsx
│   ├── 页码 / Sheet / 行号 / 段落
│   └── chunk_id
├── Body
│   ├── 上一段（弱化）
│   ├── 命中片段（高亮 mark）
│   └── 下一段（弱化）
└── Footer
    └── 只读提示：来源仅用于核实，不支持编辑原文
```

交互：

1. 点击来源标签，先展示骨架/加载。
2. 请求 `GET /api/source/...`。
3. 渲染 `before → current → after`；当前片段中的 `snippet` 子串用 `<mark>` 高亮。
4. 若 `snippet` 在 `text` 中找不到（截断/换行差异），在顶部单独显示命中摘录，不强行高亮。
5. 请求失败：Toast 显示具体原因，抽屉保留“重试”。
6. 关闭：右上角、Esc、遮罩点击。
7. 只读，不出现编辑、保存、打开原文件按钮。

### 4.5 无来源边界

- 检索为空：
  - `sources = []`
  - `answer_mode=rag` 时回答固定为“当前知识库中没有相关信息。……”
  - 前端不显示来源标签，显示“本次回答未引用知识库内容”。
- 不允许从归档、项目上下文或 LLM 自行生成 `sources`。
- `sources` 只能由 `retrieval_service.search()` 从 Chroma 返回。

---

## 5. 接口变更汇总

| 接口 | 变更 |
| --- | --- |
| `PATCH /api/project/<id>` | 新增；更新客户名/行业/生产模式，返回 `project` 与 `summary` |
| `DELETE /api/project/<id>` | 新增；删除项目、向量、文档、上传文件、助手历史 |
| `POST /api/conversation/clear` | 新增；只清空指定阶段对话 |
| `GET /api/source/<project_id>?chunk_id=` | 新增；来源详情、前后上下文 |
| `GET /api/context/<id>` | 阶段增加 `conversation_history`；保留 `messages` 兼容别名 |
| `GET /api/dashboard` | 项目卡片增加 `created_at` |
| `POST /api/chat` | `sources` 增加 `file_name/chunk_id/page/row`；可选 `regenerate` |
| `POST /api/chat/stream` | 同上；`done.project` 返回更新后的历史 |

现有上传、文档列表、归档、阶段切换、助手接口保持兼容。

---

## 6. 错误码补充

| code | HTTP | 说明 |
| --- | --- | --- |
| `INVALID_FIELD` | 400 | PATCH 未知字段 |
| `PROJECT_DELETE_IN_PROGRESS` | 409 | 项目正在删除 |
| `PROJECT_BUSY` | 409 | 删除期间禁止对话/上传/归档 |
| `VECTOR_DELETE_FAILED` | 500 | Chroma 删除失败 |
| `DOCUMENT_DELETE_FAILED` | 500 | 文档/上传文件删除失败 |
| `ASSISTANT_HISTORY_DELETE_FAILED` | 500 | 助手历史删除失败 |
| `PROJECT_DELETE_FAILED` | 500 | 项目最终记录删除失败 |
| `NO_ASSISTANT_MESSAGE` | 400 | 重新生成时没有可替换的 Agent 回答 |
| `SOURCE_NOT_FOUND` | 404 | 来源切片不存在或不属于该项目 |
| `CONVERSATION_CLEAR_FAILED` | 500 | 清空对话写入失败 |

---

## 7. 验证计划

### 项目 CRUD

- PATCH 客户名/行业后，卡片即时更新，`updated_at` 变化，`last_active_at` 不变。
- PATCH 空字段、超长、未知字段返回 400。
- DELETE 成功：`projects.json` 无记录、Chroma 无该项目向量、文档登记/上传文件/助手历史均清理。
- DELETE 任一步模拟失败：接口返回明确 code/message，卡片保留。
- 删除后访问 `/workbench/<id>` 重定向首页并提示。
- 旧项目缺 `created_at` 自动补齐。

### 对话历史

- 旧 `messages` 迁移为 `conversation_history`，`GET /api/context` 同时返回两者。
- 进入工作台恢复当前阶段历史；切走再切回顺序不变。
- 清空当前阶段：历史为空、归档保留、状态符合规则。
- 重新生成：历史长度不变，最后一条 assistant 被替换，user 不重复。
- 多轮对话后刷新/切阶段，历史完整。
- `sources` 随历史恢复。

### 来源

- `/api/chat` sources 包含 `file_name/chunk_id/snippet/page|row/score`。
- 来源标签显示页码/行号/段落。
- 点击标签打开详情，展示前后片段并高亮。
- 检索无结果：固定无信息回答，`sources=[]`，前端显示无引用提示。
- 来源详情越权/不存在返回 404。
- 旧字段 `file/doc_id/score` 仍可读。

---

## 8. 需要你确认的决策

1. **严格原子性**：接受“删除日志 + 有序删除 + 单项目锁 + 启动恢复”的方案，而不是声称跨 Chroma/文件/JSON 的 ACID 事务？
2. **上下文删除位置**：当前上下文/阶段历史都在 `projects.json`，是否接受“最后一步原子删除 projects.json 记录即删除上下文/阶段历史”，而文档登记、上传文件、助手历史在倒数第二步删除？
3. **`messages` 兼容别名**：`conversation_history` 作为唯一持久化字段，`GET /api/context` 暂时同时返回 `messages` 兼容字段，是否接受？
4. **重新生成接口**：在 `/api/chat` 和 `/api/chat/stream` 增加可选 `regenerate: true`，不新增独立接口，是否接受？
5. **来源详情接口**：为支持“前后各扩展一段”，新增只读 `GET /api/source/<project_id>?chunk_id=...`，是否接受？
6. **清空历史后的阶段状态**：有归档保持 `archived`，无归档改为 `not_started`，是否接受？

确认后我再开始改数据层、服务层、路由和前端。
