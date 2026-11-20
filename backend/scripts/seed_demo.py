import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, Reconciliation
from src.services.business import metadata, seed_records, analyze
from src.services import reconciliation_store
from src.schemas.reconciliation import ReconciliationInput
from src.services.reports import deliver
from src.utils.security import hash_password
from src.utils.serialization import checksum

OUTSOURCED_KILNS = [
    {'batch': 'WX-01', 'line': '外协东窑', 'fired': 2000, 'reported_yield': 93.0},
    {'batch': 'WX-02', 'line': '外协东窑', 'fired': 1800, 'reported_yield': 91.5},
    {'batch': 'WX-03', 'line': '外协中窑', 'fired': 1600, 'reported_yield': 96.2},
    {'batch': 'WX-04', 'line': '外协西窑', 'fired': 2200, 'reported_yield': 94.5},
]
OUTSOURCED_LEDGER = 7126


def seed_reconciliations(db, admin):
    if db.scalar(select(Reconciliation).where(Reconciliation.name == '外协窑 · 10 月对账（已批准）')):
        return
    done = reconciliation_store.create(
        db,
        ReconciliationInput(name='外协窑 · 10 月对账（已批准）', target=93, items=OUTSOURCED_KILNS),
        admin,
    )
    reconciliation_store.set_ledger(db, done.id, OUTSOURCED_LEDGER, expected_revision=None)
    reconciliation_store.approve(db, done.id, expected_revision=done.revision, user=admin)

    trial_items = [dict(row) for row in OUTSOURCED_KILNS[:3]]
    trial_items.append({'batch': 'WX-09', 'line': '外协试烧', 'fired': 900, 'reported_yield': 92.8})
    reconciliation_store.create(
        db,
        ReconciliationInput(name='外协窑 · 11 月对账（试算中）', target=93, items=trial_items),
        admin,
    )


def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        admin=db.scalar(select(User).where(User.role=='admin'))
        seed_reconciliations(db, admin)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,Reconciliation]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
