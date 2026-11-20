from fastapi import APIRouter, Depends, HTTPException

from src.config.database import get_db
from src.middlewares.auth import current_user, can_write, admin_only
from src.schemas.reconciliation import (
    ApproveInput,
    LedgerInput,
    ReconciliationInput,
    SupplyInput,
)
from src.services import reconciliation_store as store
from src.services.audit import record
from src.services.reconciliation import InputError

router = APIRouter(prefix='/reconciliations')


@router.get('')
def listing(db=Depends(get_db), u=Depends(current_user)):
    return store.summaries(db)


@router.post('', status_code=201)
def create(data: ReconciliationInput, db=Depends(get_db), u=Depends(can_write)):
    try:
        rec = store.create(db, data, u)
    except InputError as error:
        raise HTTPException(422, str(error))
    record(db, u.email, 'reconciliation.create', {'id': rec.id, 'items': len(data.items)})
    db.commit()
    return store.detail(db, rec.id)


@router.get('/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    return store.detail(db, id)


@router.put('/{id}/ledger')
def ledger(id: int, data: LedgerInput, db=Depends(get_db), u=Depends(can_write)):
    rec = store.set_ledger(db, id, data.ledger_total, data.expected_revision)
    record(
        db,
        u.email,
        'reconciliation.ledger',
        {'id': id, 'ledger_total': data.ledger_total, 'revision': rec.revision},
    )
    db.commit()
    return store.detail(db, id)


@router.put('/{id}/supply')
def supply(id: int, data: SupplyInput, db=Depends(get_db), u=Depends(can_write)):
    rec, item = store.supply(
        db, id, data.batch.strip(), data.supplied_passed, data.expected_revision, u.email
    )
    record(
        db,
        u.email,
        'reconciliation.supply',
        {'id': id, 'batch': item.batch, 'passed': item.supplied_passed, 'revision': rec.revision},
    )
    db.commit()
    return store.detail(db, id)


@router.post('/{id}/approve')
def approve(id: int, data: ApproveInput, db=Depends(get_db), u=Depends(admin_only)):
    rec, approval = store.approve(db, id, data.expected_revision, u)
    record(
        db,
        u.email,
        'reconciliation.approve',
        {'id': id, 'revision': rec.revision, 'approval': approval.id},
    )
    db.commit()
    return store.detail(db, id)
