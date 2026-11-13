"""字段业务含义版本发布业务规则。

围绕元数据记录的字段含义做版本化管理：

* 每次修改业务含义、统计信息或负责人都会形成一条新草稿，状态按
  「草稿 -> 待审批 -> 已生效」推进；已生效版本是不可变快照，任何修正都只能
  再发布新版本，不能覆盖旧版本。
* 同一元数据记录同一时刻只允许一个待审批版本（草稿同样唯一），并发提交时
  用进程锁 + 落库前复检保证只有一个待办真正入库。
* 发布时记录生效时间与发布批次，版本带上「生效区间」；晚到的说明修正以
  回填方式发布，会把修正版本插进对应生效区间，旧区间被收窄但旧版本本身
  不被改动，历史批次仍能回看当时采用的说明。
* 两个说明同时声称适用于同一字段时间段时，按生效时间、批次、版本完整度
  明确优先级；冲突判定以「能够覆盖目标日期且已生效」的版本为准。

血缘节点与表结构演进记录只保存版本引用，展示时统一通过本服务解析成
同一份版本快照，保证元数据记录页、血缘页、演进记录里的字段含义、版本号
与更新时间完全一致。
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

from app.store import store

MODULE = "field_version"
METADATA_MODULE = "metadata"
LINEAGE_MODULE = "lineage"
CATALOG_MODULE = "catalog"

DRAFT = "草稿"
PENDING = "待审批"
EFFECTIVE = "已生效"
REJECTED = "已驳回"
VERSION_STATUSES = [DRAFT, PENDING, EFFECTIVE, REJECTED]

# 版本承载的可变内容：只有这三类字段发生变化才需要形成新草稿。
SNAPSHOT_FIELDS = ["业务含义", "统计信息", "负责人"]
# 完整度评分依据：信息越全、在冲突排序中优先级越高。
COMPLETENESS_FIELDS = ["业务含义", "统计信息", "负责人", "字段类型", "更新频率"]

# 发布动作写回元数据记录 / 演进记录时使用的批次前缀。
BATCH_PREFIX = "REL"
BACKFILL_BATCH_PREFIX = "BF"

_lock = threading.RLock()


def _today() -> str:
    return date.today().isoformat()


class FieldVersionService:
    """字段含义版本的状态流转、区间管理与冲突解析。"""

    # ------------------------------------------------------------------
    # 基础读取
    # ------------------------------------------------------------------
    def list_versions(
        self,
        *,
        metadata_id: int | None = None,
        record_code: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._all_versions()
        if metadata_id is not None:
            rows = [row for row in rows if int(row.get("metadata_id", 0)) == metadata_id]
        if record_code:
            rows = [row for row in rows if row.get("记录编号") == record_code]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("版本编号", ""))
                or keyword in str(row.get("记录编号", ""))
                or keyword in str(row.get("字段名称", ""))
            ]
        # 默认按记录、版本序号排列，方便直接看到一条字段的演进脉络。
        rows = sorted(
            rows,
            key=lambda row: (str(row.get("记录编号", "")), int(row.get("version_no", 0))),
        )
        total = len(rows)
        start = max(page - 1, 0) * size
        return [dict(row) for row in rows[start:start + size]], total

    def get_version(self, version_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, version_id)
        return dict(row) if row else None

    def versions_of(self, metadata_id: int) -> list[dict[str, Any]]:
        """返回一条元数据记录的全部版本（按版本序号升序）。"""
        rows = [
            row
            for row in self._all_versions()
            if int(row.get("metadata_id", 0)) == metadata_id
        ]
        return [dict(row) for row in sorted(rows, key=lambda item: int(item.get("version_no", 0)))]

    # ------------------------------------------------------------------
    # 草稿：修改含义 / 统计信息 / 负责人时形成新草稿
    # ------------------------------------------------------------------
    def create_draft(
        self,
        metadata_id: int,
        values: dict[str, Any],
        *,
        change_note: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """基于元数据记录创建草稿；同记录存在待审批版本时拒绝新草稿。"""
        with _lock:
            record = store.find(METADATA_MODULE, metadata_id)
            if record is None:
                return None, f"元数据记录 {metadata_id} 不存在，无法创建字段含义草稿"

            open_version = self._open_version(metadata_id)
            if open_version is not None:
                state = open_version.get("status")
                return None, (
                    f"该字段已存在{state}版本（{open_version.get('版本编号')}），"
                    "同一字段只允许一个开放版本，请先完成审批或驳回后再修改"
                )

            snapshot = self._snapshot_from(record, values)
            if not str(snapshot.get("业务含义") or "").strip():
                return None, "业务含义为必填项，草稿缺少字段业务含义"

            rows = self._all_versions()
            next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
            version_no = self._next_version_no(metadata_id)
            version = {
                "id": next_id,
                "版本编号": self._build_version_code(record, version_no),
                "metadata_id": metadata_id,
                "version_no": version_no,
                "记录编号": record.get("记录编号"),
                "表名称": record.get("表名称"),
                "字段名称": record.get("字段名称"),
                "字段类型": record.get("字段类型"),
                "更新频率": record.get("更新频率"),
                "status": DRAFT,
                "pending": True,
                "abnormal": False,
                "业务含义": snapshot.get("业务含义", ""),
                "统计信息": snapshot.get("统计信息", ""),
                "负责人": snapshot.get("负责人", ""),
                "变更说明": change_note or values.get("变更说明") or "",
                "发布批次": "",
                "生效时间": "",
                "失效时间": "",
                "回填标记": False,
                "完整度": self._completeness({
                    **record,
                    "业务含义": snapshot.get("业务含义", ""),
                    "统计信息": snapshot.get("统计信息", ""),
                    "负责人": snapshot.get("负责人", ""),
                }),
                "创建时间": _today(),
                "更新时间": _today(),
                "发布时间": "",
            }
            rows.append(version)
            self._sync_record_status(record, DRAFT)
            return dict(version), "字段含义草稿已创建，可继续编辑后提交审批"

    def update_draft(
        self,
        version_id: int,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        """编辑草稿内容；已生效版本不可变，任何修改都会被拒绝。"""
        with _lock:
            version = store.find(MODULE, version_id)
            if version is None:
                return None, f"字段版本 {version_id} 不存在"
            if version.get("status") != DRAFT:
                return None, (
                    f"版本 {version.get('版本编号')} 已处于{version.get('status')}，"
                    "已生效或审批中的版本不可覆盖，请基于当前说明新建草稿"
                )
            for field in SNAPSHOT_FIELDS:
                if field in values and str(values.get(field) or "").strip():
                    version[field] = str(values.get(field)).strip()
            if values.get("变更说明") is not None:
                version["变更说明"] = str(values.get("变更说明") or "")
            if not str(version.get("业务含义") or "").strip():
                return None, "业务含义为必填项，不能清空"
            version["完整度"] = self._completeness(version)
            version["更新时间"] = _today()
            return dict(version), "草稿已更新，尚未影响已生效说明"

    # ------------------------------------------------------------------
    # 状态流转：草稿 -> 待审批 -> 已生效
    # ------------------------------------------------------------------
    def submit_for_approval(
        self,
        version_id: int,
        *,
        batch: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """提交审批：并发提交时同一字段只允许一个待办落库。"""
        with _lock:
            version = store.find(MODULE, version_id)
            if version is None:
                return None, f"字段版本 {version_id} 不存在"
            if version.get("status") != DRAFT:
                return None, f"只有草稿版本可提交审批，当前状态为{version.get('status')}"

            metadata_id = int(version["metadata_id"])
            # 落库前复检：即便两个请求同时进来，也只有一个待办能写入。
            duplicate = next(
                (
                    row
                    for row in self._all_versions()
                    if int(row.get("metadata_id", 0)) == metadata_id
                    and row.get("status") == PENDING
                    and int(row.get("id", 0)) != version_id
                ),
                None,
            )
            if duplicate is not None:
                return None, (
                    f"字段已存在待审批版本 {duplicate.get('版本编号')}，"
                    "同一字段版本并发提交时只允许一个待办落库"
                )

            version["status"] = PENDING
            version["pending"] = True
            version["发布批次"] = batch or version.get("发布批次") or ""
            version["更新时间"] = _today()
            record = store.find(METADATA_MODULE, metadata_id)
            if record is not None:
                self._sync_record_status(record, PENDING)
            return dict(version), f"版本 {version['版本编号']} 已提交审批"

    def publish(
        self,
        version_id: int,
        *,
        effective_date: str | None = None,
        batch: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """正常发布：审批通过后版本生效，收窄上一版本的生效区间。"""
        with _lock:
            version = store.find(MODULE, version_id)
            if version is None:
                return None, f"字段版本 {version_id} 不存在"
            if version.get("status") != PENDING:
                return None, f"只有待审批版本可发布，当前状态为{version.get('status')}"

            effective = (effective_date or _today()).strip()
            self._validate_date(effective)
            publish_batch = (batch or self._next_batch(BATCH_PREFIX)).strip()

            self._apply_effective(
                version,
                effective=effective,
                batch=publish_batch,
                backfill=False,
            )
            record = store.find(METADATA_MODULE, int(version["metadata_id"]))
            if record is not None:
                self._apply_snapshot_to_record(record, version)
                self._sync_record_status(record, EFFECTIVE)
                self._write_catalog_entry(record, version, backfill=False)
            return dict(version), f"版本 {version['版本编号']} 已生效，引用方将统一采用该版本"

    def reject(self, version_id: int, reason: str | None = None) -> tuple[dict[str, Any] | None, str]:
        """驳回到草稿态，释放该字段的待办占用。"""
        with _lock:
            version = store.find(MODULE, version_id)
            if version is None:
                return None, f"字段版本 {version_id} 不存在"
            if version.get("status") != PENDING:
                return None, f"只有待审批版本可驳回，当前状态为{version.get('status')}"
            version["status"] = REJECTED
            version["pending"] = False
            version["abnormal"] = True
            version["变更说明"] = (
                f"驳回原因：{reason}" if reason else "审批未通过，版本已驳回"
            )
            version["更新时间"] = _today()
            # 释放记录上的待办占用：有现行已生效版本则回到已生效，否则回到草稿。
            record = store.find(METADATA_MODULE, int(version["metadata_id"]))
            if record is not None:
                has_effective = any(
                    row.get("status") == EFFECTIVE
                    and int(row.get("metadata_id", 0)) == int(version["metadata_id"])
                    for row in self._all_versions()
                )
                record["status"] = EFFECTIVE if has_effective else DRAFT
                record["pending"] = record["status"] != EFFECTIVE
                record["abnormal"] = True
            return dict(version), f"版本 {version['版本编号']} 已驳回"

    # ------------------------------------------------------------------
    # 晚到修正：回填到对应生效区间，旧已生效版本不被覆盖
    # ------------------------------------------------------------------
    def publish_backfill(
        self,
        version_id: int,
        *,
        effective_date: str,
        batch: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """回填发布：修正版生效时间落在过去，插入历史区间并保留旧版本。

        与正常发布的区别：

        * 生效时间由调用方显式指定（晚到修正针对的是历史时间段）；
        * 标记 ``回填标记``，发布批次走回填批次序列；
        * 旧版本仍然是已生效快照，只是生效区间被收窄，历史批次引用不变。
        """
        with _lock:
            version = store.find(MODULE, version_id)
            if version is None:
                return None, f"字段版本 {version_id} 不存在"
            if version.get("status") != PENDING:
                return None, f"只有待审批版本可回填发布，当前状态为{version.get('status')}"
            target_date = (effective_date or "").strip()
            self._validate_date(target_date)
            if target_date > _today():
                return None, "回填发布用于晚到的历史修正，生效时间不能晚于今天"

            publish_batch = (batch or self._next_batch(BACKFILL_BATCH_PREFIX)).strip()
            self._apply_effective(
                version,
                effective=target_date,
                batch=publish_batch,
                backfill=True,
            )
            record = store.find(METADATA_MODULE, int(version["metadata_id"]))
            if record is not None:
                # 回填的是历史区间：记录当前展示仍由「覆盖今天」的版本决定，
                # 因此这里不直接用回填版覆盖记录当前值，只写演进留痕。
                self._write_catalog_entry(record, version, backfill=True)
            return (
                dict(version),
                f"回填版本 {version['版本编号']} 已插入 {target_date} 起的生效区间，"
                "历史说明已可追溯，原已生效版本保留不变",
            )

    # ------------------------------------------------------------------
    # 引用解析：血缘节点 / 演进记录 / 元数据记录共用同一份快照
    # ------------------------------------------------------------------
    def resolve_reference(self, version_id: int) -> dict[str, Any] | None:
        """把版本引用解析成统一快照，供血缘页与演进记录页展示。"""
        version = store.find(MODULE, version_id) if version_id else None
        if version is None:
            return None
        return self._reference_view(version)

    def resolve_for_date(
        self,
        metadata_id: int,
        target_date: str,
    ) -> dict[str, Any]:
        """解析某字段在目标日期实际采用的版本。

        优先级（同时适用于冲突排查）：

        1. 必须是已生效版本，且生效区间能够覆盖目标日期；
        2. 生效时间更晚的优先（后发布的修正优先于旧说明）；
        3. 生效时间相同时，发布批次靠后的优先；
        4. 批次也相同时，版本完整度更高的优先。
        """
        self._validate_date(target_date)
        candidates = [
            row
            for row in self._all_versions()
            if int(row.get("metadata_id", 0)) == metadata_id
            and row.get("status") == EFFECTIVE
            and self._covers(row, target_date)
        ]
        ordered = self._prioritize(candidates, target_date)
        chosen = ordered[0] if ordered else None
        if chosen is None:
            return {
                "metadata_id": metadata_id,
                "目标日期": target_date,
                "命中": False,
                "message": "目标日期没有可覆盖的已生效版本",
                "candidates": [],
            }
        view = self._reference_view(chosen)
        view["目标日期"] = target_date
        view["命中"] = True
        view["优先级说明"] = self._priority_reason(chosen, candidates)
        view["candidates"] = [
            {
                "版本编号": row.get("版本编号"),
                "生效时间": row.get("生效时间"),
                "失效时间": row.get("失效时间"),
                "发布批次": row.get("发布批次"),
                "完整度": row.get("完整度"),
            }
            for row in self._sort_candidates(candidates)
        ]
        return view

    def conflicts(self, metadata_id: int, target_date: str | None = None) -> list[dict[str, Any]]:
        """找出同一字段时间段上相互重叠的已生效版本，给出冲突优先级。"""
        effective_rows = [
            row
            for row in self._all_versions()
            if int(row.get("metadata_id", 0)) == metadata_id
            and row.get("status") == EFFECTIVE
        ]
        overlap_pairs: list[dict[str, Any]] = []
        for index, left in enumerate(effective_rows):
            for right in effective_rows[index + 1:]:
                if self._intervals_overlap(left, right):
                    probe_date = target_date or left.get("生效时间") or _today()
                    ordered = self._prioritize([left, right], probe_date)
                    winner = ordered[0] if ordered else None
                    loser = ordered[1] if ordered and len(ordered) > 1 else None
                    overlap_pairs.append({
                        "目标日期": probe_date,
                        "胜出版本": winner.get("版本编号") if winner else "",
                        "让位版本": loser.get("版本编号") if loser else "",
                        "判定依据": self._priority_reason(winner, [left, right]) if winner else "",
                        "版本A": self._reference_view(left),
                        "版本B": self._reference_view(right),
                    })
        return overlap_pairs

    def timeline(self, metadata_id: int) -> list[dict[str, Any]]:
        """返回一条字段的生效区间时间线，历史批次据此回看当时说明。"""
        rows = [
            row
            for row in self._all_versions()
            if int(row.get("metadata_id", 0)) == metadata_id
            and row.get("status") == EFFECTIVE
        ]
        rows.sort(key=lambda item: str(item.get("生效时间", "")))
        return [
            {
                "版本编号": row.get("版本编号"),
                "version_no": row.get("version_no"),
                "生效时间": row.get("生效时间"),
                "失效时间": row.get("失效时间") or "至今",
                "发布批次": row.get("发布批次"),
                "回填标记": row.get("回填标记", False),
                "业务含义": row.get("业务含义"),
                "统计信息": row.get("统计信息"),
                "负责人": row.get("负责人"),
                "完整度": row.get("完整度"),
                "更新时间": row.get("更新时间"),
            }
            for row in rows
        ]

    def enrich_lineage(self, node: dict[str, Any]) -> dict[str, Any]:
        """给血缘节点补字段映射引用的版本快照（同一字段说明版本）。"""
        version_id = node.get("field_version_id")
        view = self.resolve_reference(int(version_id)) if version_id else None
        enriched = dict(node)
        if view is not None:
            enriched["引用版本编号"] = view["版本编号"]
            enriched["字段业务含义"] = view["业务含义"]
            enriched["版本更新时间"] = view["更新时间"]
            enriched["发布批次"] = view["发布批次"]
            enriched["版本生效时间"] = view["生效时间"]
        else:
            enriched.setdefault("引用版本编号", "")
            enriched.setdefault("字段业务含义", "")
            enriched.setdefault("版本更新时间", "")
        return enriched

    def enrich_catalog(self, entry: dict[str, Any]) -> dict[str, Any]:
        """给演进记录补引用版本的含义快照，保留版本引用可回看历史。"""
        version_id = entry.get("field_version_id")
        view = self.resolve_reference(int(version_id)) if version_id else None
        enriched = dict(entry)
        if view is not None:
            enriched["引用版本编号"] = view["版本编号"]
            enriched["字段业务含义"] = view["业务含义"]
            enriched["版本更新时间"] = view["更新时间"]
        else:
            enriched.setdefault("引用版本编号", "")
            enriched.setdefault("字段业务含义", "")
            enriched.setdefault("版本更新时间", "")
        return enriched

    def enrich_metadata_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """给元数据记录补当前采用版本与开放版本信息。"""
        enriched = dict(record)
        metadata_id = int(record.get("id", 0))
        current = self.resolve_for_date(metadata_id, _today())
        if current.get("命中"):
            enriched["当前版本编号"] = current["版本编号"]
            enriched["业务含义"] = current["业务含义"]
            enriched["统计信息"] = current["统计信息"]
            enriched["负责人"] = current["负责人"]
            enriched["版本更新时间"] = current["更新时间"]
            enriched["版本生效时间"] = current["生效时间"]
        else:
            enriched.setdefault("当前版本编号", "")
            enriched.setdefault("版本更新时间", "")
        open_version = self._open_version(metadata_id)
        if open_version is not None:
            enriched["开放版本编号"] = open_version.get("版本编号")
            enriched["开放版本状态"] = open_version.get("status")
        else:
            enriched.setdefault("开放版本编号", "")
            enriched.setdefault("开放版本状态", "")
        enriched["版本总数"] = sum(
            1 for row in self._all_versions() if int(row.get("metadata_id", 0)) == metadata_id
        )
        return enriched

    # ------------------------------------------------------------------
    # 血缘节点绑定版本引用
    # ------------------------------------------------------------------
    def bind_lineage_version(
        self,
        lineage_id: int,
        version_id: int,
    ) -> tuple[dict[str, Any] | None, str]:
        """血缘节点字段映射绑定到某个已生效/待审批字段版本。"""
        with _lock:
            node = store.find(LINEAGE_MODULE, lineage_id)
            if node is None:
                return None, f"血缘节点 {lineage_id} 不存在"
            version = store.find(MODULE, version_id)
            if version is None:
                return None, f"字段版本 {version_id} 不存在"
            if version.get("status") not in (PENDING, EFFECTIVE):
                return None, (
                    f"版本 {version.get('版本编号')} 处于{version.get('status')}，"
                    "只能绑定待审批或已生效版本"
                )
            node["field_version_id"] = version_id
            node["引用版本编号"] = version.get("版本编号")
            node["字段映射"] = (
                f"{version.get('表名称')}.{version.get('字段名称')} "
                f"-> {version.get('版本编号')}"
            )
            return self.enrich_lineage(node), "血缘节点字段映射已绑定字段含义版本"

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _all_versions(self) -> list[dict[str, Any]]:
        return store.rows(MODULE)

    def _open_version(self, metadata_id: int) -> dict[str, Any] | None:
        """开放版本 = 草稿或待审批；同一字段最多一个。"""
        return next(
            (
                row
                for row in self._all_versions()
                if int(row.get("metadata_id", 0)) == metadata_id
                and row.get("status") in (DRAFT, PENDING)
            ),
            None,
        )

    def _next_version_no(self, metadata_id: int) -> int:
        return (
            max(
                (
                    int(row.get("version_no", 0))
                    for row in self._all_versions()
                    if int(row.get("metadata_id", 0)) == metadata_id
                ),
                default=0,
            )
            + 1
        )

    def _build_version_code(self, record: dict[str, Any], version_no: int) -> str:
        record_code = str(record.get("记录编号", "META-0000"))
        suffix = record_code.split("-")[-1]
        return f"FVER-{suffix}-V{version_no:02d}"

    def _next_batch(self, prefix: str) -> str:
        rows = self._all_versions()
        sequence = [
            int(str(row.get("发布批次", "")).split("-")[-1])
            for row in rows
            if str(row.get("发布批次", "")).startswith(prefix + "-")
            and str(row.get("发布批次", "")).split("-")[-1].isdigit()
        ]
        return f"{prefix}-{max(sequence, default=0) + 1:04d}"

    def _snapshot_from(
        self,
        record: dict[str, Any],
        values: dict[str, Any],
    ) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for field in SNAPSHOT_FIELDS:
            if field in values and str(values.get(field) or "").strip():
                snapshot[field] = str(values.get(field)).strip()
            else:
                snapshot[field] = str(record.get(field, "") or "")
        return snapshot

    def _completeness(self, version: dict[str, Any]) -> int:
        return sum(
            1
            for field in COMPLETENESS_FIELDS
            if str(version.get(field, "") or "").strip()
        )

    def _apply_effective(
        self,
        version: dict[str, Any],
        *,
        effective: str,
        batch: str,
        backfill: bool,
    ) -> None:
        version["status"] = EFFECTIVE
        version["pending"] = False
        version["abnormal"] = False
        version["生效时间"] = effective
        version["失效时间"] = ""
        version["发布批次"] = batch
        version["回填标记"] = backfill
        version["发布时间"] = _today()
        version["更新时间"] = _today()
        # 把新版本切进时间线，保持区间整洁：
        # 1) 覆盖新生效日期的更早版本，失效时间收窄到新生效日期；
        # 2) 新版本的失效时间取「更晚版本」的最早生效时间，没有则开放至今。
        # 同一生效时间的版本互不切分，作为同时声称同一时间段的真冲突，
        # 交给 resolve_for_date 按发布批次与完整度裁决。
        later_starts = [
            str(other.get("生效时间", ""))
            for other in self._all_versions()
            if int(other.get("metadata_id", 0)) == int(version["metadata_id"])
            and int(other.get("id", 0)) != int(version["id"])
            and other.get("status") == EFFECTIVE
            and str(other.get("生效时间", "")) > effective
        ]
        version["失效时间"] = min(later_starts) if later_starts else ""
        for other in self._all_versions():
            if (
                int(other.get("metadata_id", 0)) == int(version["metadata_id"])
                and int(other.get("id", 0)) != int(version["id"])
                and other.get("status") == EFFECTIVE
                and str(other.get("生效时间", "")) < effective
            ):
                end = str(other.get("失效时间") or "")
                if not end or end > effective:
                    other["失效时间"] = effective
                    other["更新时间"] = _today()

    def _apply_snapshot_to_record(
        self,
        record: dict[str, Any],
        version: dict[str, Any],
    ) -> None:
        for field in SNAPSHOT_FIELDS:
            record[field] = version.get(field)
        record["field_version_id"] = int(version["id"])
        record["引用版本编号"] = version.get("版本编号")
        record["版本更新时间"] = version.get("更新时间")

    def _sync_record_status(self, record: dict[str, Any], status: str) -> None:
        record["status"] = status
        record["pending"] = status in (DRAFT, PENDING)

    def _write_catalog_entry(
        self,
        record: dict[str, Any],
        version: dict[str, Any],
        *,
        backfill: bool,
    ) -> None:
        """发布即留痕：生成一条指向字段版本的演进记录。"""
        rows = store.rows(CATALOG_MODULE)
        next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        next_seq = max(
            (
                int(str(row.get("记录编号", "")).split("-")[-1])
                for row in rows
                if str(row.get("记录编号", "")).startswith("CATA-")
                and str(row.get("记录编号", "")).split("-")[-1].isdigit()
            ),
            default=0,
        ) + 1
        before = self._previous_meaning(int(version["metadata_id"]))
        entry = {
            "id": next_id,
            "status": "已记录",
            "pending": True,
            "abnormal": False,
            "记录编号": f"CATA-{next_seq:04d}",
            "表名称": record.get("表名称"),
            "变更类型": "字段含义回填修正" if backfill else "字段含义版本发布",
            "变更字段": record.get("字段名称"),
            "变更前值": before,
            "变更后值": version.get("业务含义"),
            "变更批次": version.get("发布批次"),
            "变更时间": version.get("生效时间"),
            "field_version_id": int(version["id"]),
            "引用版本编号": version.get("版本编号"),
        }
        rows.append(entry)

    def _previous_meaning(self, metadata_id: int) -> str:
        rows = [
            row
            for row in self._all_versions()
            if int(row.get("metadata_id", 0)) == metadata_id
            and row.get("status") == EFFECTIVE
        ]
        if not rows:
            return ""
        latest = sorted(rows, key=lambda item: str(item.get("生效时间", "")))[-1]
        return str(latest.get("业务含义", "") or "")

    def _reference_view(self, version: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(version["id"]),
            "版本编号": version.get("版本编号"),
            "version_no": version.get("version_no"),
            "记录编号": version.get("记录编号"),
            "表名称": version.get("表名称"),
            "字段名称": version.get("字段名称"),
            "业务含义": version.get("业务含义"),
            "统计信息": version.get("统计信息"),
            "负责人": version.get("负责人"),
            "status": version.get("status"),
            "发布批次": version.get("发布批次"),
            "生效时间": version.get("生效时间"),
            "失效时间": version.get("失效时间"),
            "回填标记": version.get("回填标记", False),
            "完整度": version.get("完整度"),
            "更新时间": version.get("更新时间"),
        }

    def _covers(self, version: dict[str, Any], target_date: str) -> bool:
        start = str(version.get("生效时间", "") or "")
        end = str(version.get("失效时间", "") or "")
        if not start:
            return False
        if start > target_date:
            return False
        if end and target_date >= end:
            return False
        return True

    def _intervals_overlap(self, left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_start = str(left.get("生效时间", "") or "")
        right_start = str(right.get("生效时间", "") or "")
        if not left_start or not right_start:
            return False
        earlier, later = (left, right) if left_start <= right_start else (right, left)
        earlier_end = str(earlier.get("失效时间", "") or "")
        # 早版本没有失效时间，或失效时间晚于晚版本生效时间 => 区间重叠。
        return not earlier_end or earlier_end > str(later.get("生效时间", ""))

    def _sort_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            candidates,
            key=lambda row: (
                str(row.get("生效时间", "")),
                str(row.get("发布批次", "")),
                int(row.get("完整度", 0)),
                int(row.get("version_no", 0)),
            ),
            reverse=True,
        )

    def _prioritize(
        self,
        candidates: list[dict[str, Any]],
        target_date: str,
    ) -> list[dict[str, Any]]:
        covering = [row for row in candidates if self._covers(row, target_date)]
        return self._sort_candidates(covering)

    def _priority_reason(
        self,
        winner: dict[str, Any] | None,
        candidates: list[dict[str, Any]],
    ) -> str:
        if winner is None:
            return "没有能够覆盖目标日期的已生效版本"
        others = [row for row in candidates if int(row.get("id", 0)) != int(winner.get("id", 0))]
        if not others:
            return "唯一覆盖目标日期的已生效版本"
        for other in others:
            if str(winner.get("生效时间", "")) != str(other.get("生效时间", "")):
                return "生效时间更晚，按生效时间优先"
            if str(winner.get("发布批次", "")) != str(other.get("发布批次", "")):
                return "生效时间相同，发布批次靠后优先"
        return "生效时间与批次相同，版本完整度更高优先"

    def _validate_date(self, value: str) -> None:
        if not value:
            raise ValueError("日期不能为空")
        date.fromisoformat(value)


field_version_service = FieldVersionService()
