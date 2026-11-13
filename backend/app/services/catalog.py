"""表结构演进业务规则：状态流转、字段校验与筛选口径都收在这里。

每条演进记录可通过 ``field_version_id`` 保留当时采用的字段含义版本引用；
读取时由字段版本服务解析版本快照，因此历史批次回看时仍能看到当时说明，
且与元数据记录页、血缘页的版本号、含义、更新时间保持一致。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.field_version import field_version_service

MODULE = "catalog"
REQUIRED_FIELDS = ["记录编号", "表名称", "变更类型"]
STATUS_ORDER = ["已记录", "已复核", "已归档"]
ACTION_RULES = {"标记复核": "已记录", "归档记录": "已复核"}
NEGATIVE_ACTIONS = []


class CatalogService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = [field_version_service.enrich_catalog(row) for row in rows[start:start + size]]
        return page_rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return field_version_service.enrich_catalog(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for optional in ["变更字段", "变更前值", "变更后值", "变更批次", "变更时间"]:
            if optional in values:
                entry[optional] = values.get(optional)
        # 登记演进记录时可直接绑定字段含义版本，保留版本引用。
        if values.get("field_version_id"):
            try:
                version_id = int(values["field_version_id"])
            except (TypeError, ValueError):
                return None, ["field_version_id 必须是整数"]
            view = field_version_service.resolve_reference(version_id)
            if view is None:
                return None, [f"字段版本 {version_id} 不存在"]
            entry["field_version_id"] = version_id
            entry["引用版本编号"] = view["版本编号"]
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return field_version_service.enrich_catalog(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"演进记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于表结构演进可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return field_version_service.enrich_catalog(entry), f"演进记录已{action}"
