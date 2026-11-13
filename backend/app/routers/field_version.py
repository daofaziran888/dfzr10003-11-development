"""字段业务含义版本接口。

提供版本草稿、审批、发布、回填以及引用解析能力；元数据记录页、血缘页与
演进记录页都通过这里的解析结果展示同一份字段说明版本。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.field_version import (
    DRAFT,
    EFFECTIVE,
    PENDING,
    field_version_service as service,
)

router = APIRouter(prefix="/api/field-versions", tags=["字段含义版本"])

STATUSES = [DRAFT, PENDING, EFFECTIVE, "已驳回"]


@router.get("", response_model=PageResult[dict])
def list_versions(
    metadata_id: int | None = Query(default=None, description="按元数据记录 id 过滤"),
    record_code: str | None = Query(default=None, description="按记录编号（如 META-0002）过滤"),
    status: str | None = Query(default=None, description="草稿、待审批、已生效、已驳回"),
    keyword: str | None = Query(default=None, description="按版本编号/记录编号/字段名称检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """列出字段含义版本，可按元数据记录与状态过滤。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_versions(
        metadata_id=metadata_id,
        record_code=record_code,
        status=status,
        keyword=keyword,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{version_id}", response_model=dict)
def get_version(version_id: int) -> dict:
    """读取单个字段版本明细。"""
    version = service.get_version(version_id)
    if version is None:
        raise HTTPException(status_code=404, detail=f"字段版本 {version_id} 不存在")
    return version


@router.post("/drafts", response_model=ActionResult)
def create_draft(payload: EntryPayload) -> ActionResult:
    """修改业务含义、统计信息或负责人时形成新草稿。

    同一元数据记录存在草稿/待审批版本时会被拒绝，保证开放版本唯一。
    """
    values = payload.values
    metadata_id = values.get("metadata_id")
    if not str(metadata_id or "").strip():
        return ActionResult(ok=False, message="缺少必填字段：metadata_id")
    try:
        metadata_id = int(metadata_id)
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="metadata_id 必须是整数")
    version, message = service.create_draft(metadata_id, values, change_note=payload.remark)
    if version is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=version)


@router.post("/{version_id}/draft", response_model=ActionResult)
def update_draft(version_id: int, payload: EntryPayload) -> ActionResult:
    """继续编辑草稿；已生效版本不可覆盖。"""
    version, message = service.update_draft(version_id, payload.values)
    if version is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=version)


@router.post("/{version_id}/submit", response_model=ActionResult)
def submit_for_approval(version_id: int, payload: EntryPayload | None = None) -> ActionResult:
    """草稿提交审批；并发提交时同一字段只允许一个待办落库。"""
    batch = None
    if payload is not None:
        batch = payload.values.get("batch")
    version, message = service.submit_for_approval(version_id, batch=batch)
    if version is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=version)


@router.post("/{version_id}/publish", response_model=ActionResult)
def publish(version_id: int, payload: EntryPayload | None = None) -> ActionResult:
    """待审批版本发布生效，自动收窄上一版本生效区间并写演进留痕。"""
    effective_date = None
    batch = None
    if payload is not None:
        effective_date = payload.values.get("effective_date")
        batch = payload.values.get("batch")
    try:
        version, message = service.publish(
            version_id, effective_date=effective_date, batch=batch
        )
    except ValueError as exc:
        return ActionResult(ok=False, message=f"生效时间格式不正确：{exc}")
    if version is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=version)


@router.post("/{version_id}/backfill", response_model=ActionResult)
def publish_backfill(version_id: int, payload: EntryPayload) -> ActionResult:
    """晚到的说明修正回填发布到历史生效区间，旧已生效版本保留不覆盖。"""
    effective_date = str(payload.values.get("effective_date") or "").strip()
    if not effective_date:
        return ActionResult(ok=False, message="回填发布必须指定 effective_date（修正生效日期）")
    batch = payload.values.get("batch")
    try:
        version, message = service.publish_backfill(
            version_id, effective_date=effective_date, batch=batch
        )
    except ValueError as exc:
        return ActionResult(ok=False, message=f"生效时间格式不正确：{exc}")
    if version is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=version)


@router.post("/{version_id}/reject", response_model=ActionResult)
def reject(version_id: int, payload: EntryPayload | None = None) -> ActionResult:
    """驳回待审批版本，释放该字段的待办占用。"""
    reason = payload.remark if payload is not None else None
    version, message = service.reject(version_id, reason)
    if version is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=version)


@router.get("/records/{metadata_id}/timeline", response_model=dict)
def timeline(metadata_id: int) -> dict[str, object]:
    """一条字段的生效区间时间线，历史批次据此回看当时说明。"""
    return {
        "metadata_id": metadata_id,
        "items": service.timeline(metadata_id),
    }


@router.get("/records/{metadata_id}/resolve", response_model=dict)
def resolve_for_date(
    metadata_id: int,
    target_date: str = Query(..., description="要解析的目标日期，如 2026-10-01"),
) -> dict[str, Any]:
    """解析某字段在目标日期实际采用的版本（生效时间/批次/完整度优先级）。"""
    try:
        return service.resolve_for_date(metadata_id, target_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"目标日期格式不正确：{exc}")


@router.get("/records/{metadata_id}/conflicts", response_model=dict)
def conflicts(
    metadata_id: int,
    target_date: str | None = Query(default=None, description="可选，冲突判定的目标日期"),
) -> dict[str, object]:
    """排查同一字段时间段上重叠的已生效版本，并给出冲突优先级结论。"""
    return {
        "metadata_id": metadata_id,
        "items": service.conflicts(metadata_id, target_date),
    }
