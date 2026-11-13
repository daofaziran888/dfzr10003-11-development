"""元数据目录业务规则：状态流转、字段校验与筛选口径都收在这里。

字段业务含义、统计信息与负责人的版本化发布由 ``field_version`` 服务负责；
本服务在读取时把当前生效版本快照合并进记录，保证元数据记录页展示的
含义、版本号与更新时间与血缘页、演进记录完全一致。
"""
from __future__ import annotations

from typing import Any

from app.store import store
from app.services.field_version import field_version_service

MODULE = "metadata"
REQUIRED_FIELDS = ["记录编号", "表名称", "字段名称"]
STATUS_ORDER = ["草稿", "待审批", "已生效", "已废弃"]
ACTION_RULES = {"提交审批": "待审批", "发布元数据": "已生效", "废弃元数据": "已废弃"}
NEGATIVE_ACTIONS = []


class MetadataService:
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
        page_rows = [field_version_service.enrich_metadata_record(row) for row in rows[start:start + size]]
        return page_rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        enriched = field_version_service.enrich_metadata_record(entry)
        enriched["版本时间线"] = field_version_service.timeline(entry_id)
        return enriched

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 登记时可顺带带上字段类型、含义等说明属性。
        for optional in ["字段类型", "业务含义", "统计信息", "更新频率", "负责人"]:
            if optional in values:
                entry[optional] = values.get(optional)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"元数据记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于元数据目录可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return field_version_service.enrich_metadata_record(entry), f"元数据记录已{action}"
