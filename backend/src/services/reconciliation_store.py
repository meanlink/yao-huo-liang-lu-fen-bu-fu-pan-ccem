import time

from fastapi import HTTPException
from sqlalchemy import select, update

from src.models import Reconciliation, ReconciliationItem, ReconciliationApproval
from src.services.reconciliation import InputError, compute


def _items_of(db, rec_id: int):
    return list(
        db.scalars(
            select(ReconciliationItem)
            .where(ReconciliationItem.reconciliation_id == rec_id)
            .order_by(ReconciliationItem.position, ReconciliationItem.id)
        )
    )


def _raw_item(item: ReconciliationItem) -> dict:
    return {
        "batch": item.batch,
        "line": item.line,
        "fired": item.fired,
        "reported_yield": item.reported_yield,
        "supplied_passed": item.supplied_passed,
    }


def _snapshot_item(item: ReconciliationItem) -> dict:
    return {
        **_raw_item(item),
        "supplied_at": item.supplied_at,
        "supplied_by": item.supplied_by,
    }


def _lock(db, rec_id: int) -> Reconciliation:
    rec = db.scalar(
        select(Reconciliation)
        .where(Reconciliation.id == rec_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if rec is None:
        raise HTTPException(404, "对账复盘不存在")
    return rec


def _check_revision(rec: Reconciliation, expected: int | None, action: str):
    if expected is not None and expected != rec.revision:
        raise HTTPException(
            409,
            f"数据已更新到第 {rec.revision} 版（提交依据为第 {expected} 版），"
            f"请刷新后重新核对，{action}",
        )


def _payload(db, rec: Reconciliation) -> dict:
    items = _items_of(db, rec.id)
    try:
        result = compute(
            [_raw_item(i) for i in items], ledger_total=rec.ledger_total, target=rec.target
        )
    except InputError as error:
        raise HTTPException(422, str(error))
    approvals = list(
        db.scalars(
            select(ReconciliationApproval)
            .where(ReconciliationApproval.reconciliation_id == rec.id)
            .order_by(ReconciliationApproval.id.desc())
        )
    )
    current_approval = next((a for a in approvals if a.revision == rec.revision), None)
    latest = approvals[0] if approvals else None
    return {
        "id": rec.id,
        "name": rec.name,
        "revision": rec.revision,
        "target": rec.target,
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
        "approved": current_approval is not None,
        "approved_at": current_approval.approved_at if current_approval else None,
        "approved_revision": latest.revision if latest else None,
        "stale_approval": bool(latest and current_approval is None),
        "approval_history": [
            {
                "id": a.id,
                "revision": a.revision,
                "approved_at": a.approved_at,
                "snapshot": a.snapshot,
            }
            for a in approvals
        ],
        **result,
    }


def create(db, data, user):
    rows = []
    for raw in data.items:
        rows.append(
            {
                "batch": raw.get("batch"),
                "line": raw.get("line"),
                "fired": raw.get("fired"),
                "reported_yield": raw.get("reported_yield"),
                "supplied_passed": raw.get("supplied_passed"),
            }
        )
    try:
        computed = compute(rows, ledger_total=None, target=data.target)
    except InputError as error:
        raise HTTPException(422, str(error))
    now = time.time()
    rec = Reconciliation(
        name=data.name.strip(),
        target=float(data.target),
        ledger_total=None,
        revision=1,
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(rec)
    db.flush()
    for position, item in enumerate(computed["items"]):
        supplied = item["supplied_passed"]
        db.add(
            ReconciliationItem(
                reconciliation_id=rec.id,
                position=position,
                batch=item["batch"],
                line=item["line"],
                fired=item["fired"],
                reported_yield=item["reported_yield"],
                supplied_passed=supplied,
                supplied_at=now if supplied is not None else None,
                supplied_by=user.email if supplied is not None else None,
            )
        )
    db.flush()
    return rec


def set_ledger(db, rec_id: int, ledger_total: int, expected_revision: int | None):
    rec = _lock(db, rec_id)
    _check_revision(rec, expected_revision, "不能覆盖过期结果，请确认最新总账后重新提交")
    if rec.ledger_total == ledger_total:
        return rec
    rec.ledger_total = ledger_total
    rec.revision += 1
    rec.updated_at = time.time()
    db.flush()
    return rec


def supply(db, rec_id: int, batch: str, passed: int, expected_revision: int | None, actor: str):
    rec = _lock(db, rec_id)
    _check_revision(rec, expected_revision, "不能覆盖过期结果，请刷新后再补报")
    item = db.scalar(
        select(ReconciliationItem).where(
            ReconciliationItem.reconciliation_id == rec.id,
            ReconciliationItem.batch == batch,
        )
    )
    if item is None:
        raise HTTPException(404, f"窑次 {batch} 不在本次对账中")
    if passed > item.fired:
        raise HTTPException(422, f"补报合格件数不能超过烧成件数 {item.fired}")
    if item.supplied_passed == passed:
        return rec, item
    item.supplied_passed = passed
    item.supplied_at = time.time()
    item.supplied_by = actor
    rec.revision += 1
    rec.updated_at = time.time()
    db.flush()
    return rec, item


def approve(db, rec_id: int, expected_revision: int, user):
    rec = _lock(db, rec_id)
    _check_revision(rec, expected_revision, "不能批准过期结果")
    payload = _payload(db, rec)
    if payload["approved"]:
        raise HTTPException(409, "当前版本已经批准，无需重复批准")
    if not payload["can_approve"]:
        reason = payload["blocking_reasons"][0] if payload["blocking_reasons"] else "复盘尚未满足批准条件"
        raise HTTPException(409, f"当前结果不能批准：{reason}")
    snapshot = {
        "revision": rec.revision,
        "name": rec.name,
        "target": payload["target"],
        "ledger_total": payload["ledger_total"],
        "computed_at": time.time(),
        "items": [
            {
                "batch": item["batch"],
                "line": item["line"],
                "fired": item["fired"],
                "reported_yield": item["reported_yield"],
                "reported_min": item["reported_min"],
                "reported_max": item["reported_max"],
                "supplied_passed": item["supplied_passed"],
                "required_passed": item["required_passed"],
                "constrained_min": item["constrained_min"],
                "constrained_max": item["constrained_max"],
                "verdict": item["verdict"],
                "reason": item["reason"],
            }
            for item in payload["items"]
        ],
        "total": payload["total"],
        "summary": payload["summary"],
        "raw_items": [_snapshot_item(i) for i in _items_of(db, rec.id)],
    }
    approval = ReconciliationApproval(
        reconciliation_id=rec.id,
        revision=rec.revision,
        approved_by=user.id,
        approved_at=time.time(),
        snapshot=snapshot,
    )
    db.add(approval)
    rec.approved_at = approval.approved_at
    rec.approved_by = user.id
    rec.approved_revision = rec.revision
    rec.approved_snapshot = snapshot
    db.flush()
    return rec, approval


def list_all(db):
    return list(db.scalars(select(Reconciliation).order_by(Reconciliation.id.desc())))


def summaries(db):
    """列表用的轻量视图：不携带完整快照，只给出阶段、待核数与批准状态。"""
    approved_revisions = {}
    for rec_id, revision in db.execute(
        select(ReconciliationApproval.reconciliation_id, ReconciliationApproval.revision)
    ):
        approved_revisions.setdefault(rec_id, set()).add(revision)
    rows = []
    for rec in list_all(db):
        try:
            result = compute(
                [_raw_item(i) for i in _items_of(db, rec.id)],
                ledger_total=rec.ledger_total,
                target=rec.target,
            )
            phase, summary, note = result["phase"], result["summary"], result["total"]["note"]
        except InputError:
            phase, summary, note = "needs_input", None, "上报数据无法解析，请核对"
        rows.append(
            {
                "id": rec.id,
                "name": rec.name,
                "target": rec.target,
                "ledger_total": rec.ledger_total,
                "revision": rec.revision,
                "phase": phase,
                "summary": summary,
                "note": note,
                "approved": rec.revision in approved_revisions.get(rec.id, set()),
                "approved_revision": rec.approved_revision,
                "created_at": rec.created_at,
                "updated_at": rec.updated_at,
            }
        )
    return rows


def get(db, rec_id: int):
    rec = db.get(Reconciliation, rec_id)
    if rec is None:
        raise HTTPException(404, "对账复盘不存在")
    return rec


def detail(db, rec_id: int):
    return _payload(db, get(db, rec_id))
