import os
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='kiln-rec-tests-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP, 'test.db')
os.environ['ARTIFACT_DIR'] = os.path.join(_TMP, 'artifacts')
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6399/0'
os.environ['SEED_DEMO'] = 'false'
os.environ['JWT_SECRET'] = 'test-secret'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from src.config.database import Base, Session, engine  # noqa: E402
from src.main import app  # noqa: E402
from src.models import User  # noqa: E402
from src.utils.security import hash_password  # noqa: E402


@pytest.fixture(scope='session', autouse=True)
def seeded():
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ('admin', 'analyst', 'viewer'):
            if db.query(User).filter(User.email == role + '@demo.local').count() == 0:
                db.add(
                    User(
                        email=role + '@demo.local',
                        name=role,
                        role=role,
                        password=hash_password('Demo123!'),
                    )
                )
        db.commit()
    yield


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def token(client, role='admin'):
    response = client.post(
        '/auth/login', json={'email': role + '@demo.local', 'password': 'Demo123!'}
    )
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['access_token']}


ITEMS = [
    {'batch': 'W-1', 'line': '东窑', 'fired': 2000, 'reported_yield': 93.0},
    {'batch': 'W-2', 'line': '东窑', 'fired': 2000, 'reported_yield': 92.0},
    {'batch': 'W-3', 'line': '西窑', 'fired': 2000, 'reported_yield': 95.0},
]


def make(client, headers, items=None, name='11 月外协窑对账', target=93):
    response = client.post(
        '/reconciliations',
        json={'name': name, 'target': target, 'items': items or ITEMS},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_trial_then_ledger_then_approve(client):
    headers = token(client)
    rec = make(client, headers)
    assert rec['phase'] == 'trial'
    assert rec['revision'] == 1
    assert rec['can_approve'] is False
    assert rec['total']['ledger'] is None
    assert all(row['constrained_min'] <= row['constrained_max'] for row in rec['items'])

    response = client.put(
        f"/reconciliations/{rec['id']}/ledger",
        json={'ledger_total': 5600, 'expected_revision': 1},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    rec = response.json()
    assert rec['revision'] == 2
    assert rec['feasible'] is True
    assert rec['can_approve'] is True
    verdicts = {r['batch']: (r['constrained_min'], r['constrained_max'], r['verdict']) for r in rec['items']}
    assert verdicts['W-1'] == (1860, 1860, '达标')
    assert verdicts['W-2'] == (1840, 1840, '未达标')
    assert verdicts['W-3'] == (1900, 1900, '达标')

    response = client.post(
        f"/reconciliations/{rec['id']}/approve",
        json={'expected_revision': 2},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    approved = response.json()
    assert approved['approved'] is True
    assert approved['approved_revision'] == 2
    assert len(approved['approval_history']) == 1
    snapshot = approved['approval_history'][0]['snapshot']
    assert snapshot['ledger_total'] == 5600
    assert len(snapshot['items']) == 3


def test_infeasible_ledger_blocks_approval_with_reason(client):
    headers = token(client)
    rec = make(client, headers, name='无解示例 ' + str(id(client)))
    response = client.put(
        f"/reconciliations/{rec['id']}/ledger",
        json={'ledger_total': 5000, 'expected_revision': 1},
        headers=headers,
    )
    assert response.status_code == 200
    rec = response.json()
    assert rec['feasible'] is False
    assert rec['phase'] == 'infeasible'
    assert rec['can_approve'] is False
    assert '5597' in rec['total']['note'] or '5597' in ' '.join(rec['blocking_reasons'])
    assert all(row['verdict'] == '待核' for row in rec['items'])

    response = client.post(
        f"/reconciliations/{rec['id']}/approve",
        json={'expected_revision': 2},
        headers=headers,
    )
    assert response.status_code == 409
    assert '不能批准' in response.json()['detail']


def test_stale_revision_cannot_approve_after_concurrent_supply(client):
    headers = token(client)
    rec = make(client, headers, name='并发示例 ' + str(id(client)))
    rec = client.put(
        f"/reconciliations/{rec['id']}/ledger",
        json={'ledger_total': 5600, 'expected_revision': 1},
        headers=headers,
    ).json()
    assert rec['can_approve'] is True
    revision_seen = rec['revision']

    supplied = client.put(
        f"/reconciliations/{rec['id']}/supply",
        json={'batch': 'W-2', 'supplied_passed': 1840, 'expected_revision': revision_seen},
        headers=headers,
    )
    assert supplied.status_code == 200
    assert supplied.json()['revision'] == revision_seen + 1

    stale = client.post(
        f"/reconciliations/{rec['id']}/approve",
        json={'expected_revision': revision_seen},
        headers=headers,
    )
    assert stale.status_code == 409
    assert '第' in stale.json()['detail']

    current = client.post(
        f"/reconciliations/{rec['id']}/approve",
        json={'expected_revision': revision_seen + 1},
        headers=headers,
    )
    assert current.status_code == 200


def test_supply_after_approval_keeps_snapshot_and_marks_stale(client):
    headers = token(client)
    rec = make(client, headers, name='批准留档 ' + str(id(client)))
    rec = client.put(
        f"/reconciliations/{rec['id']}/ledger",
        json={'ledger_total': 5600, 'expected_revision': 1},
        headers=headers,
    ).json()
    rec = client.post(
        f"/reconciliations/{rec['id']}/approve",
        json={'expected_revision': rec['revision']},
        headers=headers,
    ).json()
    assert rec['approved'] is True
    approved_revision = rec['revision']

    rec = client.put(
        f"/reconciliations/{rec['id']}/supply",
        json={'batch': 'W-2', 'supplied_passed': 1839, 'expected_revision': approved_revision},
        headers=headers,
    ).json()
    assert rec['approved'] is False
    assert rec['stale_approval'] is True
    assert rec['approved_revision'] == approved_revision
    assert len(rec['approval_history']) == 1
    assert rec['approval_history'][0]['revision'] == approved_revision

    # 已批准的旧依据仍在历史里，且数值不随新的补报改变
    old = rec['approval_history'][0]['snapshot']
    old_row = next(r for r in old['items'] if r['batch'] == 'W-2')
    new_row = next(r for r in rec['items'] if r['batch'] == 'W-2')
    assert old_row['constrained_min'] == 1840 and old_row['constrained_max'] == 1840
    assert old_row['supplied_passed'] is None
    assert new_row['supplied_passed'] == 1839
    assert new_row['constrained_min'] == 1839 and new_row['constrained_max'] == 1839


def test_supply_rejects_out_of_band_value(client):
    headers = token(client)
    rec = make(client, headers, name='补报校验 ' + str(id(client)))
    response = client.put(
        f"/reconciliations/{rec['id']}/supply",
        json={'batch': 'W-2', 'supplied_passed': 1700, 'expected_revision': 1},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body['can_approve'] is False
    row = next(r for r in body['items'] if r['batch'] == 'W-2')
    assert row['verdict'] == '待核'
    assert '不一致' in row['reason']


def test_supply_unknown_batch_is_404(client):
    headers = token(client)
    rec = make(client, headers, name='未知窑次 ' + str(id(client)))
    response = client.put(
        f"/reconciliations/{rec['id']}/supply",
        json={'batch': 'NOPE', 'supplied_passed': 100, 'expected_revision': 1},
        headers=headers,
    )
    assert response.status_code == 404


def test_invalid_inputs_rejected(client):
    headers = token(client)
    for bad in (
        {'name': '坏数据', 'target': 93, 'items': [{'batch': 'W-1', 'fired': 100, 'reported_yield': 92.35}]},
        {'name': '坏数据', 'target': 93, 'items': [{'batch': 'W-1', 'fired': 0, 'reported_yield': 90}]},
        {'name': '坏数据', 'target': 93, 'items': [{'batch': 'W-1', 'fired': 100, 'reported_yield': 120}]},
        {'name': '坏数据', 'target': 93, 'items': [
            {'batch': 'W-1', 'fired': 100, 'reported_yield': 90},
            {'batch': 'W-1', 'fired': 100, 'reported_yield': 91},
        ]},
        {'name': '坏数据', 'target': 93, 'items': []},
    ):
        response = client.post('/reconciliations', json=bad, headers=headers)
        assert response.status_code == 422, (bad, response.text)


def test_only_admin_can_approve_and_viewer_is_read_only(client):
    analyst = token(client, 'analyst')
    viewer = token(client, 'viewer')
    admin = token(client)
    rec = make(client, admin, name='权限 ' + str(id(client)))
    rec = client.put(
        f"/reconciliations/{rec['id']}/ledger",
        json={'ledger_total': 5600, 'expected_revision': 1},
        headers=admin,
    ).json()
    denied = client.post(
        f"/reconciliations/{rec['id']}/approve",
        json={'expected_revision': rec['revision']},
        headers=analyst,
    )
    assert denied.status_code == 403
    detail = client.get(f"/reconciliations/{rec['id']}", headers=viewer)
    assert detail.status_code == 200
    assert detail.json()['can_approve'] is True
    blocked = client.post(
        '/reconciliations',
        json={'name': '观察员写入', 'target': 93, 'items': ITEMS},
        headers=viewer,
    )
    assert blocked.status_code == 403
