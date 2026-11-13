"""字段含义版本发布的端到端校验脚本（仅用于本地验证，不随服务发布）。"""
from __future__ import annotations

import sys
import threading

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def post(path: str, values: dict | None = None, remark: str | None = None):
    payload = {"values": values or {}}
    if remark is not None:
        payload["remark"] = remark
    return client.post(path, json=payload)


print("== 1. 种子数据：META-0002 待审批示例与 LINEAGE-0002 引用 ==")
versions = client.get("/api/field-versions?record_code=META-0002").json()
check("META-0002 存在待审批版本", any(v["status"] == "待审批" for v in versions["items"]), str(versions))
lineage = client.get("/api/lineage?keyword=LINEAGE-0002").json()["items"][0]
check("LINEAGE-0002 引用版本编号", lineage.get("引用版本编号") == "FVER-0002-V01", str(lineage))
check("LINEAGE-0002 展示版本业务含义", "会员等级" in lineage.get("字段业务含义", ""), lineage.get("字段业务含义", ""))
check("LINEAGE-0002 带版本更新时间", lineage.get("版本更新时间") == "2026-09-15", str(lineage))

print("== 2. 三页字段含义/版本/更新时间一致 ==")
meta = client.get("/api/metadata?keyword=META-0002").json()["items"][0]
fv = client.get("/api/field-versions/3").json()
cata = [c for c in client.get("/api/catalog?size=100").json()["items"] if c.get("引用版本编号") == "FVER-0002-V01"][0]
check("元数据页版本号", meta["引用版本编号"] == "FVER-0002-V01", str(meta))
check("元数据页含义与版本快照一致", meta["业务含义"] == fv["业务含义"])
check("演进记录含义与版本快照一致", cata["字段业务含义"] == fv["业务含义"], cata.get("字段业务含义", ""))
check("演进记录更新时间与版本一致", cata["版本更新时间"] == fv["更新时间"])
check("血缘页含义与版本快照一致", lineage["字段业务含义"] == fv["业务含义"])

print("== 3. 修改含义形成新草稿，已生效版本不可覆盖 ==")
# META-0002 已有待审批版本，新草稿必须被拒绝
r = post("/api/field-versions/drafts", {"metadata_id": 2, "业务含义": "测试修改"})
check("开放版本唯一：待审批期间拒绝新草稿", r.json()["ok"] is False, r.text)
# 直接改已生效版本必须被拒绝
r = post("/api/field-versions/1/draft", {"业务含义": "试图覆盖已生效版本"})
check("已生效版本不可覆盖", r.json()["ok"] is False, r.text)
# 驳回待审批后释放占用，再建草稿
r = post("/api/field-versions/4/reject", {}, remark="口径需重审")
check("驳回待审批版本", r.json()["ok"] is True, r.text)
r = post("/api/field-versions/drafts", {
    "metadata_id": 2,
    "业务含义": "用户会员等级，按近 180 天行为分层（1-7 级）",
    "统计信息": "取值 1-7，日刷新 1.8 亿行",
    "负责人": "王澈",
}, remark="调整分层窗口")
check("释放后可创建新草稿", r.json()["ok"] is True, r.text)
draft = r.json()["entry"]
check("草稿编号递增", draft["版本编号"] == "FVER-0002-V03", draft["版本编号"])

print("== 4. 草稿 -> 待审批 -> 已生效 全链路 ==")
vid = draft["id"]
r = post(f"/api/field-versions/{vid}/submit")
check("提交审批", r.json()["ok"] and r.json()["entry"]["status"] == "待审批", r.text)
r = post(f"/api/field-versions/{vid}/publish", {"effective_date": "2026-11-13"})
check("发布生效", r.json()["ok"] and r.json()["entry"]["status"] == "已生效", r.text)
published = r.json()["entry"]
check("发布生成批次", published["发布批次"].startswith("REL-"), str(published))
# 发布后元数据记录被刷新且指向新版本
meta2 = client.get("/api/metadata?keyword=META-0002").json()["items"][0]
check("元数据记录指向新版本", meta2["引用版本编号"] == "FVER-0002-V03", str(meta2))
check("元数据记录含义已刷新", "180 天" in meta2["业务含义"], meta2["业务含义"])
# 发布自动写演进留痕
catalogs = client.get("/api/catalog?size=100").json()["items"]
new_cata = [c for c in catalogs if c.get("引用版本编号") == "FVER-0002-V03"]
check("发布自动生成演进记录并保留版本引用", len(new_cata) == 1, str(len(new_cata)))

print("== 5. 并发提交：同一字段只允许一个待办落库 ==")
# META-0003 有一个草稿（id=7），再制造一个草稿路径：先驳回场景不适用，
# 改为两个线程并发对同一记录创建草稿，只有一个成功（META-0003 当前有草稿）
results: list[bool] = []
def create_draft(metadata_id: int) -> None:
    resp = post("/api/field-versions/drafts", {"metadata_id": metadata_id, "业务含义": f"并发草稿 {metadata_id}"})
    results.append(resp.json()["ok"])
# META-0003 已有草稿 -> 任何新草稿都失败；先验证占用拦截
r = post("/api/field-versions/drafts", {"metadata_id": 3, "业务含义": "并发尝试"})
check("已有草稿时拒绝第二个开放版本", r.json()["ok"] is False, r.text)
# 并发提交同一个草稿两次，只有一次能进入待审批
d7 = client.get("/api/field-versions/7").json()
submit_results: list[bool] = []
def submit() -> None:
    resp = post("/api/field-versions/7/submit")
    submit_results.append(resp.json()["ok"])
t1 = threading.Thread(target=submit)
t2 = threading.Thread(target=submit)
t1.start(); t2.start(); t1.join(); t2.join()
check("并发提交仅一个待办落库", submit_results.count(True) == 1, str(submit_results))
check("草稿状态已变为待审批", client.get("/api/field-versions/7").json()["status"] == "待审批")

print("== 6. 晚到修正回填生效区间，旧版本保留 ==")
# 为 META-0003 再造一个修正版并回填到 2026-09-10（V01 区间内）
r = post("/api/field-versions/drafts", {
    "metadata_id": 3,
    "业务含义": "当日支付金额（元），统计支付成功订单，补记渠道费抵扣前口径",
    "统计信息": "日均约 300 万笔，金额单位元",
    "负责人": "陈航",
}, remark="晚到的历史口径修正")
# 当前有待审批版本占用，先驳回再建
post("/api/field-versions/7/reject", {}, remark="让位历史修正")
r = post("/api/field-versions/drafts", {
    "metadata_id": 3,
    "业务含义": "当日支付金额（元），统计支付成功订单，补记渠道费抵扣前口径",
    "统计信息": "日均约 300 万笔，金额单位元",
    "负责人": "陈航",
}, remark="晚到的历史口径修正")
check("创建回填草稿", r.json()["ok"], r.text)
bf_draft = r.json()["entry"]
post(f"/api/field-versions/{bf_draft['id']}/submit")
r = post(f"/api/field-versions/{bf_draft['id']}/backfill", {"effective_date": "2026-09-10"})
check("回填发布成功", r.json()["ok"], r.text)
bf = r.json()["entry"]
check("回填版本带回填标记与 BF 批次", bf["回填标记"] is True and bf["发布批次"].startswith("BF-"), str(bf))
# 旧版本 V01 仍在且不可变
v01 = client.get("/api/field-versions/5").json()
check("旧已生效版本保留", v01["status"] == "已生效" and "渠道费" not in v01["业务含义"])
# 区间：2026-09-10 应命中回填版
hit = client.get("/api/field-versions/records/3/resolve?target_date=2026-09-10").json()
check("回填区间命中修正版", hit["命中"] and hit["版本编号"] == bf["版本编号"], str(hit))
# 2026-09-05 仍命中旧 V01
hit2 = client.get("/api/field-versions/records/3/resolve?target_date=2026-09-05").json()
check("更早日期仍命中旧版本", hit2["命中"] and hit2["版本编号"] == "FVER-0003-V01", str(hit2))
# 今天仍命中 V02
hit3 = client.get("/api/field-versions/records/3/resolve?target_date=2026-11-13").json()
check("当前日期命中现行 V02", hit3["命中"] and hit3["版本编号"] == "FVER-0003-V02", str(hit3))

print("== 7. 冲突优先级：生效时间 -> 批次 -> 完整度 ==")
# 再发布一个同生效日期的回填版本：同日期版本互不切分，构成真冲突
r = post("/api/field-versions/drafts", {
    "metadata_id": 3,
    "业务含义": "当日支付金额（元），统计支付成功订单，渠道费抵扣前口径（二次修正）",
    "统计信息": "日均约 300 万笔，金额单位元",
    "负责人": "陈航",
}, remark="同一历史日期的二次修正")
check("同日期冲突版草稿可创建", r.json()["ok"], r.text)
conflict_draft = r.json()["entry"]
post(f"/api/field-versions/{conflict_draft['id']}/submit")
r = post(f"/api/field-versions/{conflict_draft['id']}/backfill",
         {"effective_date": "2026-09-10", "batch": "BF-0099"})
check("同日期第二版回填发布", r.json()["ok"], r.text)
conf = client.get("/api/field-versions/records/3/conflicts?target_date=2026-09-10").json()
check("检测到同时间段重叠冲突", len(conf["items"]) >= 1, str(conf))
if conf["items"]:
    pair = conf["items"][0]
    check("冲突以覆盖目标日期的已生效版为准", pair["胜出版本"] == "FVER-0003-V05", str(pair))
    check("同生效时间按发布批次靠后优先", "批次" in pair["判定依据"], str(pair))
# 解析同样按批次裁决到第二版
hit_same_day = client.get("/api/field-versions/records/3/resolve?target_date=2026-09-10").json()
check("目标日期解析到批次靠后的版本", hit_same_day["版本编号"] == "FVER-0003-V05", str(hit_same_day))
# 时间线可回看历史批次
tl = client.get("/api/field-versions/records/1/timeline").json()["items"]
check("META-0001 时间线含两个区间", len(tl) == 2, str(tl))
check("时间线保留回填批次", any(t["发布批次"] == "BF-0001" for t in tl), str(tl))
# 区间整洁：不同生效时间的版本区间互不重叠
tl3 = client.get("/api/field-versions/records/3/timeline").json()["items"]
starts_ends = [(t["生效时间"], t["失效时间"]) for t in tl3 if t["版本编号"] != "FVER-0003-V05"]
check("时间线区间首尾相接无重叠", all(
    starts_ends[i][1] == starts_ends[i + 1][0] for i in range(len(starts_ends) - 1)
), str(starts_ends))

print("== 8. 血缘绑定版本引用 ==")
r = post("/api/lineage/1/bind-version", {"field_version_id": 6})
check("血缘节点绑定已生效版本", r.json()["ok"], r.text)
node = client.get("/api/lineage?keyword=LINEAGE-0001").json()["items"][0]
check("绑定后字段映射带版本号", "FVER-0003-V02" in node["字段映射"], str(node))
check("绑定后展示同一版本含义", node["字段业务含义"] == client.get("/api/field-versions/6").json()["业务含义"])
r = post("/api/lineage/1/bind-version", {"field_version_id": 999999})
check("绑定不存在版本被拦截", r.json()["ok"] is False, r.text)

print()
print(f"结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
