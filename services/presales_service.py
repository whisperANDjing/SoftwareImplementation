# -*- coding: utf-8 -*-
"""售前快速评估：不进入五阶段，生成并保存 APS 价值点初稿。"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from data import context_store
from errors import ApiError, LlmCallError, LlmNotConfigured
from services import export_service, llm_service
from services.text_utils import summarize_text


PRESALES_PROMPT = """你是资深 APS 售前顾问。请根据客户行业、生产模式、核心痛点和预计规模，
输出一份中文 Markdown 的《APS 价值点初稿》，用于顾问拜访客户前快速准备。

必须包含：
1. 客户概况（仅使用用户提供的信息，不编造）
2. 核心痛点与 APS 能力映射（逐条对应）
3. 类似行业预期收益方向（明确是方向性判断，不是承诺数字）
4. 建议下一步（调研问题、演示重点、需要客户提供的数据）
5. 需进一步核实的信息

写作要求：
- 先给结论，再给依据；不要使用空泛的销售话术。
- 不得虚构客户名称、组织角色、具体收益数字或系统能力。
- 对不确定内容明确标注“待确认”。
"""


def _fallback(fields: Dict[str, Any]) -> str:
    industry = fields.get("industry") or "客户所在行业"
    mode = fields.get("production_mode") or "待确认"
    pain = fields.get("pain_points") or "待确认"
    scale = fields.get("scale") or "待确认"
    return f"""# APS 价值点初稿

## 客户概况

- 行业：{industry}
- 生产模式：{mode}
- 核心痛点：{pain}
- 预计规模：{scale}

## 核心痛点与 APS 能力映射

- **插单/订单优先级**：APS 可通过订单优先级、交期承诺和有限产能排程，把插单影响显性化。
- **缺料与齐套**：将物料约束纳入排程，提前暴露缺料与齐套风险。
- **设备与换型约束**：把设备能力、模具/换型时间纳入约束模型，减少不可执行计划。
- **计划冻结与滚动**：通过冻结期和滚动排程，平衡稳定性与响应速度。

> 以上为方向性判断，需要结合客户实际数据与流程进一步确认。

## 类似行业预期收益方向

- 计划编制周期缩短
- 插单响应更可解释
- 设备利用率与准交率的平衡改善
- 计划、物料、生产之间的协同增强

> 收益方向和幅度需在现状调研与数据验证后确认，不能作为售前承诺。

## 建议下一步

1. 访谈计划、生产、采购、销售负责人，确认痛点优先级。
2. 收集订单、BOM、工艺路线、设备、换型时间和库存数据样本。
3. 选择一条产线或产品族做小范围排程演示。
4. 对关键约束和收益口径形成书面确认。

## 需进一步核实的信息

- 当前排产周期与计划冻结期
- 订单来源与插单规则
- 关键设备/模具约束
- 物料齐套与替代规则
- 现有 ERP/MES 数据可用性
"""


def _validate(payload: Dict[str, Any]) -> Dict[str, str]:
    fields = {
        "industry": str(payload.get("industry", "")).strip(),
        "production_mode": str(payload.get("production_mode", "")).strip(),
        "pain_points": str(payload.get("pain_points", "")).strip(),
        "scale": str(payload.get("scale", "")).strip(),
    }
    if not fields["industry"]:
        raise ApiError("PRESALES_VALIDATION_ERROR", "请填写客户行业。", 400)
    if not fields["pain_points"]:
        raise ApiError("PRESALES_VALIDATION_ERROR", "请填写核心痛点。", 400)
    limits = {"industry": 80, "production_mode": 80, "pain_points": 2000, "scale": 200}
    for field, limit in limits.items():
        if len(fields[field]) > limit:
            raise ApiError(
                "PRESALES_VALIDATION_ERROR",
                f"{field} 不能超过 {limit} 字符。",
                400,
            )
    return fields


def _generate(fields: Dict[str, str]) -> Dict[str, str]:
    user_message = "\n".join(
        [
            f"行业：{fields['industry']}",
            f"生产模式：{fields['production_mode'] or '待确认'}",
            f"核心痛点：{fields['pain_points']}",
            f"预计规模：{fields['scale'] or '待确认'}",
        ]
    )
    try:
        content = llm_service.complete(
            system_prompt=PRESALES_PROMPT,
            history=[],
            user_message=user_message,
        )
        return {"content": content, "mode": "deepseek"}
    except LlmNotConfigured:
        return {"content": _fallback(fields), "mode": "demo"}
    except LlmCallError:
        return {"content": _fallback(fields), "mode": "demo-fallback"}
    except Exception:
        return {"content": _fallback(fields), "mode": "demo-fallback"}


def create_assessment(payload: Dict[str, Any]) -> Dict[str, Any]:
    fields = _validate(payload)
    assessment = _generate(fields)
    return context_store.create_presales(fields, assessment, assessment["mode"])


def get_assessment(presales_id: str) -> Dict[str, Any]:
    record = context_store.get_presales(presales_id)
    if not record:
        raise ApiError("PRESALES_NOT_FOUND", "售前评估记录不存在。", 404)
    return record


def list_assessments() -> List[Dict[str, Any]]:
    return [
        {
            "presales_id": record["id"],
            "industry": record.get("industry", ""),
            "production_mode": record.get("production_mode", ""),
            "created_at": record.get("created_at"),
            "preview": summarize_text(
                (record.get("assessment") or {}).get("content", ""), 120
            ),
        }
        for record in context_store.list_presales()
    ]


def _safe_filename(value: str) -> str:
    text = re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(value or ""))
    return text.strip(" ._")[:60] or "客户"


def export_assessment_word(presales_id: str) -> export_service.ExportFile:
    record = get_assessment(presales_id)
    content = str((record.get("assessment") or {}).get("content", "")).strip()
    if not content:
        raise ApiError("EXPORT_FAILED", "售前评估内容为空，无法导出。", 500)
    title = f"{record.get('industry', '客户')} - APS 价值点初稿"
    filename = f"{_safe_filename(record.get('industry', '客户'))}_售前评估_APS价值点初稿.docx"
    return export_service.build_word_from_markdown(content, title, filename)
