# 实施顾问全生命周期工作台 · 接口设计与前端布局说明

> 状态：**已实现（2026-11-09）**。  
> 原则：只在现有首页、五阶段工作台、RAG、导出、仪表盘上增量扩展，不重写已有模块。
> 本文只提供接口、数据模型、服务拆分和 UI 布局，不包含实现代码。

---

## 1. 总体数据模型

### 1.1 projects.json 顶层结构保持不变

`projects.json` 仍是一个项目 ID 到记录的映射：

```json
{
  "prj_xxx": { "...": "..." },
  "presales_xxx": { "...": "..." }
}
```

为了支持售前评估，项目记录增加 `record_type`：

- `record_type = "delivery"`：现有五阶段项目。
- `record_type = "presales"`：轻量售前评估记录，不进入五阶段工作台。

旧项目缺失 `record_type` 时默认视为 `delivery`。

`GET /api/dashboard`、`GET /api/projects`、`GET /api/templates` 都要过滤掉 `presales`，避免售前记录污染项目列表和统计。

> 备选方案：把售前记录放到 projects.json 的保留键 `presales` 下。该方案会让顶层结构不再只是项目映射，影响面更大；本设计推荐轻量项目记录方案。

### 1.2 交付项目新增字段

```json
{
  "record_type": "delivery",
  "post_launch": {
    "unlocked_at": "2026-11-09T14:00:00+08:00",
    "issues": []
  },
  "stages": {
    "1": {
      "initial_context": {
        "content": "历史模板内容",
        "source_template_id": "prj_old:1:1",
        "source_project_id": "prj_old",
        "source_stage": 1,
        "injected_at": "2026-11-09T14:00:00+08:00"
      },
      "gate_confirmations": [
        {
          "gate": "constraint_priority",
          "confirmed_at": "2026-11-09T14:00:00+08:00",
          "payload": {
            "constraint_priority_confirmed": true
          }
        }
      ]
    },
    "3": {
      "gate_confirmations": [
        {
          "gate": "business_rule",
          "confirmed_at": "2026-11-09T14:00:00+08:00",
          "payload": {
            "confirmer_and_date": "张伟 / 2026-11-09"
          }
        }
      ]
    }
  }
}
```

旧数据兼容默认值：

- `record_type = "delivery"`
- `post_launch = {"unlocked_at": null, "issues": []}`
- `stages[*].gate_confirmations = []`
- `stages[*].initial_context = null`

### 1.3 售前记录

```json
{
  "id": "presales_ab12cd34",
  "record_type": "presales",
  "industry": "汽车零部件",
  "production_mode": "订单型 MTO",
  "pain_points": "插单频繁，缺料发现晚",
  "scale": "3 个工厂 / 1200 人 / 年产值 8 亿",
  "assessment": {
    "content": "Markdown 格式的 APS 价值点初稿",
    "mode": "deepseek",
    "generated_at": "2026-11-09T14:00:00+08:00"
  },
  "created_at": "2026-11-09T14:00:00+08:00",
  "updated_at": "2026-11-09T14:00:00+08:00"
}
```

- 不创建五阶段、不进入仪表盘项目统计。
- 不强制关联项目上下文。
- 写入 `projects.json`，以便后续查看和统一备份。
- 记录 ID 使用 `presales_` 前缀，避免与 `prj_` 混淆。

---

## 2. 模块一：售前快速评估

### 2.1 接口

#### POST /api/presales

```http
POST /api/presales
Content-Type: application/json
```

```json
{
  "industry": "汽车零部件",
  "production_mode": "订单型 MTO",
  "pain_points": "插单频繁，缺料发现晚",
  "scale": "3 个工厂 / 1200 人 / 年产值 8 亿"
}
```

响应：

```json
{
  "ok": true,
  "presales_id": "presales_ab12cd34",
  "record": {},
  "assessment": {
    "content": "Markdown 格式的 APS 价值点初稿",
    "mode": "deepseek"
  }
}
```

生成逻辑：

1. 校验四个字段，至少行业和痛点不能为空。
2. 调用 `presales_service.generate_assessment()`。
3. 提示词要求输出：
   - 核心痛点
   - 对应 APS 能力
   - 类似行业预期收益方向
   - 建议下一步
   - 需进一步核实的信息
4. 无 `DEEPSEEK_API_KEY` 时用本地结构化模板兜底。
5. 保存到 `projects.json` 的轻量 presales 记录。

#### GET /api/presales

用于“后续查看”和历史列表：

```json
{
  "ok": true,
  "presales": [
    {
      "presales_id": "presales_ab12cd34",
      "industry": "汽车零部件",
      "production_mode": "订单型 MTO",
      "created_at": "2026-11-09T14:00:00+08:00",
      "preview": "痛点对应的 APS 能力……"
    }
  ]
}
```

> 该接口是对需求中“后续查看”的必要补充；否则只能保存但无法在 UI 中找回。

#### GET /api/presales/{presales_id}

返回一条完整记录，供售前评估页面初始化。

#### POST /api/presales/{presales_id}/export/word

- 复用现有 Markdown → Word 导出能力。
- 文件名为 `{industry}_售前评估_APS价值点初稿.docx`。
- 不建立项目、不进入五阶段导出。

### 2.2 前端 UI

首页顶部栏：

```text
+ 新建项目     售前快速评估
```

点击打开轻量表单弹窗：

```text
Presales Quick Assessment
├── 客户行业
├── 生产模式
├── 核心痛点（多行）
├── 预计规模
├── 最近售前评估（可选列表）
└── [开始评估]
```

提交后跳转：

```text
/presales/<presales_id>
```

评估页采用类似对话的两段式布局：

```text
┌ 售前快速评估 ─────────────────────────────┐
│ 客户行业 / 生产模式 / 预计规模              │
├───────────────────────────────────────────┤
│ 顾问（表单摘要）                           │
│ 汽车零部件，订单型 MTO，插单频繁……          │
│                                           │
│ APS 售前助手                               │
│ ## APS 价值点初稿                          │
│ ### 痛点与能力映射                         │
│ ...                                       │
│ ### 类似行业收益方向                       │
│ ...                                       │
├───────────────────────────────────────────┤
│ [导出 Word]        [返回首页]              │
└───────────────────────────────────────────┘
```

- 单轮评估，不做多轮自由聊天；后续如需多轮，再增加 `POST /api/presales/{id}/chat`。
- 页面显示“AI 初稿，需顾问核实”的免责声明。
- 历史入口放在首页售前表单弹窗内，避免新增一级导航。

---

## 3. 模块二：上线后跟踪看板

### 3.1 解锁条件

- 当项目 `stages["5"].status == "archived"` 时解锁。
- 归档阶段五时写入 `post_launch.unlocked_at`。
- 旧项目阶段五已归档但没有该字段时，按 `stage5.status == archived` 动态判断为已解锁。

### 3.2 数据模型

```json
{
  "post_launch": {
    "unlocked_at": "ISO8601",
    "issues": [
      {
        "issue_id": "iss_ab12cd34",
        "description": "客户反馈插单后计划重排时间过长",
        "severity": "high",
        "status": "pending",
        "created_at": "ISO8601",
        "updated_at": "ISO8601"
      }
    ]
  }
}
```

枚举：

| 字段 | 值 |
| --- | --- |
| `severity` | `high` / `medium` / `low` |
| `status` | `pending` / `in_progress` / `resolved` |

### 3.3 接口

#### GET /api/issues/{project_id}

- 项目不存在：404。
- 未解锁：仍返回 200，但 `unlocked=false`，`issues=[]`，便于前端显示锁定态。
- 已解锁：返回问题列表，按创建时间倒序。

```json
{
  "ok": true,
  "unlocked": true,
  "issues": []
}
```

#### POST /api/issues/{project_id}

```json
{
  "description": "客户反馈插单后计划重排时间过长",
  "severity": "high",
  "status": "pending"
}
```

- 未解锁：409 `POST_LAUNCH_LOCKED`。
- 描述为空：400。
- 返回新问题及更新时间。

#### PATCH /api/issues/{project_id}/{issue_id}

> 需求只列了 GET/POST，但“状态：待处理/处理中/已解决”必须可修改。建议补充该接口。

```json
{
  "description": "可选",
  "severity": "high",
  "status": "in_progress"
}
```

- 只允许项目内问题。
- 不存在的 issue 返回 404。

#### POST /api/chat/issues

```json
{
  "project_id": "prj_xxx",
  "prompt": "基于当前问题记录，生成本次迭代的需求池摘要"
}
```

- 未解锁：409。
- 无问题：返回“当前没有问题记录”，不调用 LLM。
- 读取当前问题列表，构造分析提示词。
- 返回：

```json
{
  "ok": true,
  "reply": "Markdown 格式分析",
  "mode": "deepseek"
}
```

- 无 Key 时使用本地规则兜底：
  - 按严重程度和状态汇总；
  - 高频问题给出排查方向；
  - 明确说明为本地演示输出。
- 分析结果不自动写入问题列表，避免把 AI 建议当成客户问题。

#### POST /api/export/issues

> 需求要求“一键导出 Excel”，但接口清单未列出。建议补充。

```json
{
  "project_id": "prj_xxx"
}
```

- 返回 xlsx 文件流。
- 文件名：`{客户名称}_上线问题跟踪_{YYYYMMDD}.xlsx`。
- 列：问题描述、严重程度、状态、创建时间、更新时间。
- 复用 openpyxl 导出样式。

### 3.4 前端 UI

左侧导航栏底部：

```text
...五阶段...
──────────────
上线跟踪        （仅在阶段五归档后显示）
```

点击后主工作区切换为上线跟踪看板，不新开浏览器页面；可通过 `?view=tracking` 或 hash 保持刷新状态。

布局：

```text
┌ 上线后跟踪 ──────────────────────────────────────────────┐
│ 客户：悠桦林信息科技有限公司   阶段五已归档              │
│ [新增问题]                                   [导出 Excel] │
├──────────────┬──────────────┬──────────────┬─────────────┤
│ 问题总数 12  │ 高严重 3     │ 待处理 5     │ 已解决 6    │
├──────────────────────────────────────────────────────────┤
│ 问题列表                                                  │
│ 高 │ 客户反馈插单后计划重排时间过长 │ 处理中 │ 2026-... │
│ 中 │ 物料替代后齐套率下降           │ 待处理 │ 2026-... │
├──────────────────────────────────────────────────────────┤
│ Agent 辅助                                                │
│ [基于当前问题记录，生成本次迭代的需求池摘要] [生成]       │
│ [给出针对高频问题的排查建议]                              │
│                                                          │
│ Agent 回复（Markdown）                                    │
└──────────────────────────────────────────────────────────┘
```

添加问题使用弹窗：

- 问题描述
- 严重程度：高 / 中 / 低
- 状态：待处理 / 处理中 / 已解决

问题列表支持行内更新状态（调用 PATCH），状态颜色与工作台状态色一致。

---

## 4. 模块三：人工确认门禁

### 4.1 阶段一归档

#### 前端

点击“归档本阶段”时打开确认弹窗：

```text
ARCHIVE GATE · 阶段一
☑ 我确认已与客户对齐排产约束的优先级

[取消] [确认归档]
```

- 未勾选时“确认归档”禁用。
- 确认后调用 `/api/archive`。
- 关闭弹窗不归档。

#### 后端请求扩展

```json
{
  "project_id": "prj_xxx",
  "stage": 1,
  "content": "归档内容",
  "confirmations": {
    "constraint_priority_confirmed": true
  }
}
```

校验：

- `constraint_priority_confirmed` 必须为 `true`。
- 否则返回 `400 GATE_CONFIRMATION_REQUIRED`，message：
  “请确认已与客户对齐排产约束的优先级。”

#### 记录

写入：

```json
"stages": {
  "1": {
    "gate_confirmations": [
      {
        "gate": "constraint_priority",
        "confirmed_at": "ISO8601",
        "payload": {
          "constraint_priority_confirmed": true
        }
      }
    ]
  }
}
```

### 4.2 阶段三归档

#### 前端

```text
ARCHIVE GATE · 阶段三
关键业务规则确认人及确认日期
[例如：张伟 / 2026-11-09]
[取消] [确认归档]
```

- 输入为空时“确认归档”禁用。
- 后端同样校验，不能绕过前端。

#### 后端请求扩展

```json
{
  "project_id": "prj_xxx",
  "stage": 3,
  "content": "归档内容",
  "confirmations": {
    "confirmer_and_date": "张伟 / 2026-11-09"
  }
}
```

校验：

- 非空、最多 200 字符。
- 否则 `400 GATE_CONFIRMATION_REQUIRED`。

#### 记录

```json
{
  "gate": "business_rule",
  "confirmed_at": "ISO8601",
  "payload": {
    "confirmer_and_date": "张伟 / 2026-11-09"
  }
}
```

### 4.3 手动归档编辑

上下文面板中的“保存归档”同样调用 `/api/archive`，所以：

- 阶段一/三的手动保存也必须通过同一门禁弹窗。
- 不允许只在前端“归档本阶段”按钮上加校验。
- 后端 `gate_service.validate_archive()` 是唯一可信校验点。

### 4.4 阶段五强制检查项

在阶段五提示词与本地演示稿中加入固定检查项：

> 跨部门上线协调人已确认切换时间

- 系统提示词明确要求上线检查清单必须包含此项。
- 生成后做一次校验：若最终回复不包含该句，自动追加到检查清单末尾，避免模型漏项。
- 该项不替代人工确认门禁，仅保证交付物中包含。
- 测试覆盖真实 LLM 缺失该句时的补齐逻辑（用 mock）。

### 4.5 Gate Service 拆分

```text
services/gate_service.py
├── validate_archive(project, stage, confirmations)
├── build_confirmation_record(stage, confirmations)
└── ensure_stage5_checklist(content)
```

路由/核心对话服务只调用 service，不写大段阶段 if-else。

---

## 5. 模块四：历史模板带入

### 5.1 模板来源

仅从 `record_type=delivery` 的项目中提取：

- 阶段一：`status=archived` 且 `archive.content` 非空。
- 阶段五：`status=archived` 且 `archive.content` 非空。
- 每个项目每个阶段最多一条当前模板（以最新归档版本为准）。

### 5.2 GET /api/templates

```json
{
  "ok": true,
  "templates": [
    {
      "template_id": "prj_old:1:1",
      "source_project_id": "prj_old",
      "source_project_name": "华东精密制造有限公司",
      "stage": 1,
      "stage_name": "现状调研与需求诊断",
      "archived_at": "2026-11-09T10:00:00+08:00",
      "version": 1,
      "preview": "排产约束优先级：交期优先，其次设备利用率……"
    }
  ]
}
```

- `template_id = {project_id}:{stage}:{archive.version}`。
- 不返回完整长文，避免下拉框加载大量数据；创建项目时由后端根据 ID 读取。
- 只返回阶段一/五。

### 5.3 新建项目复用

`POST /api/project` 增加可选字段：

```json
{
  "client_name": "新客户",
  "industry": "装备制造",
  "production_mode": "MTO",
  "pain_points": "插单",
  "template_id": "prj_old:1:1"
}
```

创建逻辑：

1. 校验 `template_id`。
2. 创建新项目。
3. 把来源归档内容写入新项目对应阶段的 `initial_context`：

```json
{
  "initial_context": {
    "content": "来源归档内容",
    "source_template_id": "prj_old:1:1",
    "source_project_id": "prj_old",
    "source_stage": 1,
    "injected_at": "ISO8601"
  }
}
```

4. 阶段状态保持 `not_started`，不复制归档状态、不复制对话历史。
5. 后续阶段的 Agent 提示词把 `initial_context` 作为背景，但明确标注“来自历史模板，需在新项目中重新确认”。
6. 用户可以在新项目里修改、重新生成、重新归档。

### 5.4 前端 UI

新建项目弹窗底部：

```text
复用历史模板（可选）
[不适用 ▼]
  ├─ 阶段一 · 华东精密制造有限公司 · 2026-11-09
  └─ 阶段五 · 悠桦林信息科技有限公司 · 2026-11-08
```

- 打开新建项目弹窗时请求 `GET /api/templates`。
- 没有模板时显示“暂无已归档历史模板”。
- 创建后进入工作台；对应阶段显示“历史模板带入”提示卡，内容可查看，但状态仍是未开始。
- 模板内容不自动归档，避免把历史结论直接当成新客户结论。

---

## 6. 接口汇总

| 接口 | 说明 | 状态 |
| --- | --- | --- |
| `POST /api/presales` | 创建售前评估 | 需求指定 |
| `GET /api/presales` | 售前历史列表 | 建议补充 |
| `GET /api/presales/{id}` | 售前记录详情 | 建议补充 |
| `POST /api/presales/{id}/export/word` | 售前初稿导出 Word | 建议补充 |
| `GET /api/issues/{project_id}` | 上线问题列表 | 需求指定 |
| `POST /api/issues/{project_id}` | 新增上线问题 | 需求指定 |
| `PATCH /api/issues/{project_id}/{issue_id}` | 修改问题状态/级别 | 建议补充 |
| `POST /api/chat/issues` | Agent 分析上线问题 | 需求指定 |
| `POST /api/export/issues` | 上线问题导出 Excel | 建议补充 |
| `GET /api/templates` | 历史模板列表 | 需求指定 |
| `POST /api/project` | 支持可选 `template_id` | 现有接口扩展 |
| `POST /api/archive` | 支持 `confirmations` | 现有接口扩展 |
| `GET /api/context/{id}` | 返回 `initial_context`、`gate_confirmations`、`post_launch` | 现有接口扩展 |

### 错误码

| code | HTTP | 说明 |
| --- | --- | --- |
| `PRESALES_VALIDATION_ERROR` | 400 | 售前字段缺失/过长 |
| `PRESALES_NOT_FOUND` | 404 | 售前记录不存在 |
| `POST_LAUNCH_LOCKED` | 409 | 阶段五未归档，不能新增/分析问题 |
| `ISSUE_VALIDATION_ERROR` | 400 | 问题字段非法 |
| `ISSUE_NOT_FOUND` | 404 | 问题不存在 |
| `GATE_CONFIRMATION_REQUIRED` | 400 | 阶段一/三未完成人工确认 |
| `TEMPLATE_NOT_FOUND` | 404 | 历史模板不存在 |
| `TEMPLATE_INVALID` | 400 | 模板来源不是阶段一/五已归档内容 |
| `EXPORT_FAILED` | 500 | 导出生成失败（解析错误会降级，不走到这里） |

---

## 7. Service 拆分

新增独立模块，避免把新业务写进五阶段核心：

```text
services/presales_service.py
  - create_assessment()
  - list_assessments()
  - get_assessment()
  - build_presales_prompt()

services/post_launch_service.py
  - is_unlocked()
  - list_issues()
  - add_issue()
  - update_issue()
  - build_issue_analysis_prompt()

services/gate_service.py
  - validate_archive()
  - build_confirmation_record()
  - ensure_stage5_checklist()

services/template_service.py
  - list_templates()
  - resolve_template()
  - inject_initial_context()

services/export_service.py
  - 现有 Word/Excel Markdown 导出
  - 增加 build_word_from_markdown() 供售前复用
  - 增加 export_issues_excel()
```

路由：

```text
routes/presales_routes.py
routes/post_launch_routes.py
```

项目创建/归档路由只调用 gate_service/template_service，不承载详细规则。

数据层：

- 售前轻量记录仍通过 `context_store` 原子写 projects.json。
- 上线问题通过 `context_store` 更新对应项目 `post_launch.issues`。
- 所有旧数据在 `_normalise_project` 中补默认字段。

---

## 8. 测试计划

- 售前：字段校验、LLM/本地兜底、JSON 持久化、历史列表、Word 导出。
- 上线跟踪：阶段五未归档锁定、归档后解锁、问题新增/状态更新、Agent 汇总、Excel 导出。
- 门禁：阶段一未勾选拒绝、阶段三空确认人拒绝、确认记录写入、手动归档同样受控、阶段五检查项补齐。
- 模板：只列出阶段一/五归档、创建项目注入 initial_context、状态保持未归档、不复制历史对话。
- 兼容：旧项目缺 `record_type/post_launch/gate_confirmations/initial_context` 不报错。
- 回归：首页、五阶段、RAG、来源、导出、删除、仪表盘。

---

## 9. 需要确认的关键决策

1. **售前记录存储方式**：接受“轻量 project 记录 + `record_type=presales`”的方案吗？这样能继续使用 projects.json 的原子写入，但需要在项目列表/统计/模板中过滤。
2. **售前后续查看**：接受补充 `GET /api/presales` 和 `GET /api/presales/{id}` 吗？
3. **售前导出**：接受新增 `POST /api/presales/{id}/export/word` 吗？
4. **问题状态修改**：接受新增 `PATCH /api/issues/{project_id}/{issue_id}` 吗？否则问题创建后无法变更状态。
5. **问题导出**：接受新增 `POST /api/export/issues` 吗？
6. **阶段三门禁**：用一个输入框记录“确认人及确认日期”字符串，还是要拆成“确认人”和“确认日期”两个字段？
7. **历史模板注入**：接受将内容写入 `stages[stage].initial_context`，状态保持未开始，并在工作台展示“历史模板带入”提示卡吗？
8. **售前聊天页**：v1 只展示单轮 AI 初稿，不做多轮追问，是否符合预期？

确认后我再按 Service / 路由 / 数据层 / 前端分层实现。
