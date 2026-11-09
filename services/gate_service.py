# -*- coding: utf-8 -*-
"""关键节点人工确认门禁与阶段五检查项。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from data import context_store
from errors import ApiError


STAGE5_CHECKLIST_ITEM = "跨部门上线协调人已确认切换时间"


def validate_archive(
    project: Dict[str, Any],
    stage: int,
    confirmations: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """校验阶段一/三人工确认，返回可写入上下文的审计记录。"""
    confirmations = confirmations or {}
    if stage == 1:
        if confirmations.get("constraint_priority_confirmed") is not True:
            raise ApiError(
                "GATE_CONFIRMATION_REQUIRED",
                "请确认已与客户对齐排产约束的优先级。",
                400,
            )
        return {
            "gate": "constraint_priority",
            "confirmed_at": context_store.now_iso(),
            "payload": {"constraint_priority_confirmed": True},
        }
    if stage == 3:
        value = str(confirmations.get("confirmer_and_date", "")).strip()
        if not value:
            raise ApiError(
                "GATE_CONFIRMATION_REQUIRED",
                "请填写关键业务规则确认人及确认日期。",
                400,
            )
        if len(value) > 200:
            raise ApiError(
                "GATE_CONFIRMATION_REQUIRED",
                "确认人及确认日期不能超过 200 字符。",
                400,
            )
        return {
            "gate": "business_rule",
            "confirmed_at": context_store.now_iso(),
            "payload": {"confirmer_and_date": value},
        }
    return None


def ensure_stage5_checklist(content: str) -> str:
    """保证阶段五最终输出包含固定的跨部门切换确认项。"""
    if STAGE5_CHECKLIST_ITEM in (content or ""):
        return content
    suffix = f"\n\n- [ ] {STAGE5_CHECKLIST_ITEM}"
    return (content or "").rstrip() + suffix
