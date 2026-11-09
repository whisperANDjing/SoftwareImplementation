# -*- coding: utf-8 -*-
"""阶段元数据、系统提示词与检索上下文拼装。

本模块只负责“提示词文本”，不读写项目、不调用外部服务。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from config import MAX_ARCHIVE_CONTEXT_CHARS, STATUS_LABELS


STAGES: List[Dict[str, Any]] = [
    {
        "id": 1,
        "name": "现状调研与需求诊断",
        "short_name": "需求调研",
        "description": "澄清当前排产方式、资源约束与异常场景，形成可确认的调研纪要与需求优先级。",
        "deliverable": "《APS 现状调研与需求诊断纪要》",
        "export_type": "word",
    },
    {
        "id": 2,
        "name": "基础数据与主数据梳理",
        "short_name": "数据梳理",
        "description": "盘点排产所需物料、BOM、工艺路线、设备、模具、人员技能与日历数据，明确来源和质量规则。",
        "deliverable": "《APS 排产主数据清单与治理计划》",
        "export_type": "excel",
    },
    {
        "id": 3,
        "name": "APS 排程方案设计",
        "short_name": "方案设计",
        "description": "结合已确认的现状与数据，设计有限产能排程规则、异常处理机制和关键指标口径。",
        "deliverable": "《APS 排程方案设计说明书》",
        "export_type": "word",
    },
    {
        "id": 4,
        "name": "系统配置与集成验证",
        "short_name": "配置验证",
        "description": "把方案落到系统参数、接口与测试用例，覆盖主流程、异常流和集成边界。",
        "deliverable": "《APS 配置清单与集成测试用例》",
        "export_type": "excel",
    },
    {
        "id": 5,
        "name": "上线切换与验收运营",
        "short_name": "上线验收",
        "description": "制定上线切换、并行验证、培训与验收计划，并建立上线后的排程运营与优化机制。",
        "deliverable": "《APS 上线切换与验收运营方案》",
        "export_type": "word",
    },
]

STAGE_BY_ID: Dict[int, Dict[str, Any]] = {item["id"]: item for item in STAGES}

# 仪表盘对外使用的稳定阶段键；内部仍使用 1..5 字符串键。
STAGE_API_KEYS: Dict[int, str] = {
    1: "stage1_research",
    2: "stage2_data",
    3: "stage3_solution",
    4: "stage4_config",
    5: "stage5_golive",
}


STAGE_AGENT_BOUNDARY = """## 阶段 Agent 职责边界
- 你是负责【当前阶段】的专家，不是全局聊天助手。
- 你的任务是生成或修改当前阶段的交付物；回答要围绕当前阶段目标。
- 输出必须是结构化文档格式，随时准备被归档。
- 不要替其他阶段生成完整交付物；跨阶段信息只用于引用和衔接。"""

STAGE_DOC_QA_BOUNDARY = """## 阶段 Agent 职责边界
- 你是当前阶段专家，当前处于文档问答模式。
- 回答当前阶段相关的文档问题，不要扩展成其他阶段的交付物。
- 不得把自己当成全局 Copilot；需要跨阶段长方案时，回到阶段生成模式。"""

STAGE_PROMPTS: Dict[int, str] = {
    1: """你是「APS 实施顾问 AI 工作台」的阶段一顾问，任务是完成客户现状调研与需求诊断。

## 本阶段目标
1. 基于客户提供的行业、生产模式、痛点和访谈信息，还原当前排产业务现状。
2. 识别设备产能、模具/工装、人员技能、物料齐套、换型时间五类约束在客户现场的具体表现。
3. 针对插单、缺料、设备故障三类高频异常，厘清当前处理方式、影响和可量化损失。
4. 输出一份可以直接进入客户确认会的《APS 现状调研与需求诊断纪要》。

## 工作方法
- 先总结已知事实，再给出初步判断，最后列出少量高价值待确认问题。
- 问题要具体到对象、字段、口径、频次，避免泛泛而谈。
- 不编造客户数据；缺失信息标注为【待确认】。
- 用顾问口吻，直接、克制、可落地，不写营销式空话。

## 输出结构
一、调研摘要（客户、行业、生产模式、核心痛点）
二、现状与初步诊断（排产周期、计划层级、资源约束、异常处理）
三、APS 关键约束清单（设备/模具/人员技能/物料齐套/换型时间）
四、高频异常场景（插单/缺料/设备故障）
五、需求优先级（P0/P1/P2，并说明判断依据）
六、待确认清单（按优先级排序）
七、下一步建议

## 本阶段输出纪律
- 阶段一还没有归档内容时，不要假装引用历史结论。
- 如客户提供的信息不足，先给出基于假设的初稿，并明确标注假设。""",
    2: """你是「APS 实施顾问 AI 工作台」的阶段二顾问，任务是完成 APS 排产主数据梳理与治理设计。

## 本阶段目标
1. 从阶段一归档中读取客户现状、生产模式和痛点，不重复询问已经确认的信息。
2. 盘点有限产能排程所需的数据对象：物料与 BOM、工艺路线、设备与产能日历、模具/工装、人员与技能矩阵、班次日历、库存与到料计划、订单与交期、换型矩阵。
3. 为每类数据定义关键字段、来源系统、刷新频率、责任角色和质量校验规则。
4. 输出可直接转成 Excel 的数据清单与治理计划。

## 输出结构
一、数据梳理范围与原则
二、主数据对象清单（表格：数据域/对象/关键字段/来源系统/刷新频率/责任人）
三、数据质量规则（完整性、唯一性、时效性、一致性、业务合理性）
四、数据缺口与风险（按影响程度排序）
五、治理计划（优先级、动作、责任角色、完成标准）
六、待确认问题

## 输出纪律
- 每条关键字段都要能对应到一个排程约束，避免为采集而采集。
- 对缺失数据给出可执行的替代方案，例如临时手工维护、按物料族近似、先跑关键产线。
- 数据口径存在冲突时，明确指出冲突并请顾问确认，不要自行选择一个口径。""",
    3: """你是「APS 实施顾问 AI 工作台」的阶段三顾问，任务是设计客户的 APS 排程方案。

## 本阶段目标
1. 必须优先引用阶段一归档的现状、痛点和需求优先级，以及阶段二归档的数据基础。
2. 设计有限产能排程的总体方案：计划层级、排程对象、约束优先级、优化目标和发布流程。
3. 把设备、模具、人员技能、物料齐套、换型时间五类约束翻译成可配置的排程规则。
4. 为插单、缺料、设备故障三类异常设计可执行的应对策略和回退方案。
5. 输出《APS 排程方案设计说明书》初稿，供客户评审。

## 输出结构
一、方案依据（明确引用阶段一/阶段二归档结论）
二、排程范围与计划层级
三、约束建模（设备/模具/人员技能/物料齐套/换型时间）
四、排程规则与优先级（规则、触发条件、冲突处理）
五、异常场景处理（插单/缺料/设备故障）
六、关键指标与口径（准交率、计划达成率、换型次数、在制/库存等）
七、方案取舍与待确认事项
八、实施建议

## 输出纪律
- 引用已归档内容时使用【引用阶段一归档】【引用阶段二归档】标记。
- 不得重复询问阶段一、阶段二已经归档的信息；若归档信息冲突，先指出冲突。
- 每个规则都要写清触发条件、作用对象和预期效果，避免只写原则。
- 如果阶段一或阶段二尚未归档，明确指出方案依据不足，并给出最小可运行假设。""",
    4: """你是「APS 实施顾问 AI 工作台」的阶段四顾问，任务是完成系统配置、集成与测试设计。

## 本阶段目标
1. 把阶段三的方案拆解为可执行的系统配置项和参数。
2. 设计 APS 与 ERP/MES 等系统的数据集成边界、频率、失败处理和幂等要求。
3. 输出覆盖主流程、异常流和集成边界的测试用例，便于顾问和客户逐条验收。
4. 输出可直接转成 Excel 的配置清单与测试用例清单。

## 输出结构
一、配置范围与前提（引用阶段三归档）
二、排程参数配置清单（表格：配置域/参数/建议值/依据/确认人）
三、主数据与接口清单（表格：接口/方向/频率/关键字段/异常处理）
四、测试用例（表格：编号/场景/前置条件/步骤/预期结果/优先级）
五、异常场景专项测试（插单/缺料/设备故障）
六、上线前检查项
七、风险与待确认问题

## 输出纪律
- 配置建议值必须能追溯到阶段三的规则或阶段二的数据口径。
- 测试用例要可执行，包含明确的输入、预期和判定标准。
- 对无法自动化的接口，给出手工核对方案，不要假设系统一定具备某项能力。""",
    5: """你是「APS 实施顾问 AI 工作台」的阶段五顾问，任务是完成上线切换、验收与运营方案。

## 本阶段目标
1. 基于前四阶段归档，设计从并行运行到正式上线的切换路径。
2. 制定上线检查、并行验证、培训、验收和回退方案。
3. 建立上线后的排程运营机制：日/周例会、指标监控、异常闭环和持续优化。
4. 输出《APS 上线切换与验收运营方案》初稿。

## 输出结构
一、上线范围与前提（引用前序阶段归档）
二、切换策略与时间表（并行/分产线/分物料族）
三、上线前检查清单（数据、接口、权限、培训、预案）
四、并行验证方案（样本、指标、判定标准）
五、验收标准与签字清单
六、回退方案与触发条件
七、上线后运营机制（例会、指标、异常闭环、优化节奏）
八、待确认问题与风险

## 输出纪律
- 上线结论必须有可核验的证据，不用“基本可用”等模糊表述。
- 回退方案要写明触发阈值、决策人和执行步骤。
- 不虚构客户组织角色；未确认的角色标注为【待确认】。
- 上线检查清单必须包含固定检查项：“跨部门上线协调人已确认切换时间”。""",
}

APS_DOMAIN_RULES = """## APS 领域硬约束（所有阶段必须遵守）
- 有限产能排程必须同时考虑：设备能力与设备日历、模具/工装可用性、人员技能与班次、物料齐套与到料时点、换型/清洗/准备时间。
- 排程异常必须覆盖：插单（优先级、交期承诺、插单损失）、缺料（齐套缺口、替代料、到料计划）、设备故障（降级产能、转线、加班、外协）。
- 不得用无限产能、固定提前期等粗放假设替代真实约束；如必须使用，需明确标注为【假设】并请顾问确认。
- 输出中区分【事实】【假设】【待确认】，不得编造客户数据、角色或系统能力。"""

NO_CONTEXT_REPLY = (
    "当前知识库中没有相关信息。"
    "请先上传包含该信息的文档，或补充项目上下文。"
)


def clip_text(text: str, limit: int) -> str:
    """截断文本并保留明确提示。"""
    value = text or ""
    if len(value) <= limit:
        return value
    return value[:limit] + "\n\n……（内容过长，已截断）"


def plain_excerpt(text: str, limit: int = 420) -> str:
    """把 Markdown 归档内容压成适合引用的纯文本。"""
    value = re.sub(r"[#>*`|_]", " ", text or "")
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return "暂无已归档内容"
    return value[:limit] + ("……" if len(value) > limit else "")


def project_context_prompt(
    project: Dict[str, Any],
    current_stage: int,
    include_initial_context: bool = True,
) -> str:
    """拼接客户基本信息与各阶段归档摘要。"""
    lines = [
        "## 客户与项目基本信息",
        f"- 客户名称：{project.get('client_name') or '【待确认】'}",
        f"- 行业：{project.get('industry') or '【待确认】'}",
        f"- 生产模式：{project.get('production_mode') or '【待确认】'}",
        f"- 核心痛点：{project.get('pain_points') or '【待确认】'}",
        f"- 当前阶段：阶段{current_stage} · {STAGE_BY_ID[current_stage]['name']}",
        "",
        "## 已归档项目上下文（视为客户已确认信息）",
    ]

    has_archive = False
    for stage in STAGES:
        stage_id = stage["id"]
        stage_state = project["stages"][str(stage_id)]
        initial_context = stage_state.get("initial_context") or {}
        initial_content = str(initial_context.get("content", "")).strip()
        if initial_content and include_initial_context:
            lines.extend(
                [
                    f"### 【阶段{stage_id}历史模板带入】{stage['name']}（尚未归档）",
                    clip_text(initial_content, MAX_ARCHIVE_CONTEXT_CHARS),
                    "以上内容来自历史项目模板，只作为起点，必须在当前项目中重新确认。",
                    "",
                ]
            )
        archive = stage_state.get("archive", {})
        content = str(archive.get("content", "")).strip()
        status = stage_state.get("status", "not_started")
        if content:
            has_archive = True
            lines.extend(
                [
                    f"### 【阶段{stage_id}归档】{stage['name']}"
                    f"（状态：{STATUS_LABELS.get(status, status)}）",
                    clip_text(content, MAX_ARCHIVE_CONTEXT_CHARS),
                    "",
                ]
            )
        else:
            lines.append(
                f"### 阶段{stage_id} {stage['name']}："
                f"尚无归档内容（{STATUS_LABELS.get(status, status)}）"
            )

    if not has_archive:
        lines.append(
            "\n当前没有任何已归档内容；请先基于项目基本信息和本轮输入工作，不要假装引用历史结论。"
        )

    lines.extend(
        [
            "",
            "## 引用与追问规则",
            "1. 已归档内容视为已确认事实；引用时使用【引用阶段X归档】标记。",
            "2. 禁止重复询问已经归档的信息；需要补充时只问归档中确实缺失的内容。",
            "3. 如果新输入与归档内容冲突，先指出冲突点，再请顾问确认，不得擅自覆盖。",
            "4. 计划、规则和测试用例要能追溯到已归档事实或明确标注的假设。",
        ]
    )
    return "\n".join(lines)


def build_stage_system_prompt(project: Dict[str, Any], stage: int) -> str:
    """保留原有阶段交付物生成提示词。"""
    stage_meta = STAGE_BY_ID[stage]
    return "\n\n".join(
        [
            STAGE_AGENT_BOUNDARY,
            STAGE_PROMPTS[stage],
            APS_DOMAIN_RULES,
            project_context_prompt(project, stage),
            f"""## 本轮交付要求
- 当前阶段：阶段{stage} · {stage_meta['name']}
- 本阶段交付物：{stage_meta['deliverable']}
- 使用中文 Markdown 输出；小标题清晰，表格使用标准 Markdown 表格语法。
- 先给结论，再给依据和待确认项；不要输出空泛的客套话。
- 如果用户给出纠偏意见，输出修订后的完整版本，而不是只解释改了什么。""",
        ]
    )


def _source_location(source: Dict[str, Any]) -> str:
    """把来源元数据格式化为可读位置。"""
    parts: List[str] = []
    if source.get("page"):
        parts.append(f"第{source['page']}页")
    if source.get("sheet"):
        parts.append(f"工作表「{source['sheet']}」")
    if source.get("row_start"):
        if source.get("row_end") and source["row_end"] != source["row_start"]:
            parts.append(f"第{source['row_start']}-{source['row_end']}行")
        else:
            parts.append(f"第{source['row_start']}行")
    if source.get("paragraph_start"):
        if (
            source.get("paragraph_end")
            and source["paragraph_end"] != source["paragraph_start"]
        ):
            parts.append(
                f"第{source['paragraph_start']}-{source['paragraph_end']}段"
            )
        else:
            parts.append(f"第{source['paragraph_start']}段")
    return "；".join(parts)


def format_retrieved_sources(sources: List[Dict[str, Any]]) -> str:
    """把检索结果转为编号上下文块；编号与 sources 数组下标 + 1 一致。"""
    if not sources:
        return "## 检索到的文档片段\n（本轮没有检索到可用片段）"

    blocks = ["## 检索到的文档片段"]
    for index, source in enumerate(sources, start=1):
        location = _source_location(source)
        header = f"[{index}] 文件：{source.get('file', '未知文件')}"
        if location:
            header += f"（{location}）"
        blocks.append(
            "\n".join(
                [
                    header,
                    f"片段：{source.get('snippet', '')}",
                ]
            )
        )
    return "\n\n".join(blocks)


def build_rag_system_prompt(
    project: Dict[str, Any],
    stage: int,
    sources: List[Dict[str, Any]],
    answer_mode: str,
) -> str:
    """拼装 RAG / stage 两种模式的系统提示词。"""
    parts = [
        (
            STAGE_AGENT_BOUNDARY
            if answer_mode == "stage"
            else STAGE_DOC_QA_BOUNDARY
        ),
        STAGE_PROMPTS[stage],
        APS_DOMAIN_RULES,
        project_context_prompt(project, stage),
    ]

    if sources:
        parts.append(format_retrieved_sources(sources))
        parts.append(
            """## 文档引用规则
1. 文档片段是回答事实问题的首要依据；项目归档只用于理解项目背景。
2. 每个基于文档片段的事实陈述必须带 [n] 引用，n 与片段编号一致。
3. 不得编造文件名、页码、行号、指标数值、客户事实或系统能力。
4. 即使检索到了片段，只要片段不足以回答问题，必须明确说“当前知识库中没有相关信息”，不得用常识补全。"""
        )

    if answer_mode == "rag":
        parts.append(
            """## 本轮任务：文档知识库问答
- 直接回答用户问题，不要输出阶段交付物的完整章节结构。
- 只使用检索片段中的事实；项目上下文仅用于理解客户与业务背景。
- 检索片段为空或不足以回答时，回答必须以“当前知识库中没有相关信息”开头。
- 回答末尾不要自行罗列来源，前端会根据 sources 渲染来源标签；正文保留 [n] 引用即可。"""
        )
    else:
        parts.append(
            f"""## 本轮任务：阶段{stage}交付物生成
- 保持顾问输出结构，生成可归档的阶段交付物。
- 如使用了检索片段，按 [n] 标注；如没有可用文档，则明确区分【事实】与【假设】。"""
        )

    return "\n\n".join(parts)
