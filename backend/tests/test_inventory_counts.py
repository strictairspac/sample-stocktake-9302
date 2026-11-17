"""Inventory-count behaviour: freeze snapshot, whole-batch rejection, idempotent posting, races."""
import threading
import uuid

import httpx
import pytest


def test_launch_after_post_is_refused(client, auth):
    sample = make_sample(client, auth, 16, 'QY-AFTERPOST')
    transfer = make_transfer(client, auth, sample['id'], 12)
    count = freeze(client, auth)
    enter(client, auth, count, {sample['code']: 4})
    assert post_json(client, f"/counts/{count['id']}/post", {}, auth).status_code == 200
    launch = post_json(client, '/trials', {'name': 'late-launch', 'transfer_id': transfer['id'], 'timeout_ms': 250}, auth)
    assert launch.status_code == 409
    assert '已失效' in launch.json()['detail']

@pytest.fixture(autouse=True)
def clean_trials(db):
    db.execute('DELETE FROM receipts')
    db.execute('DELETE FROM trial_balances')
    db.execute('DELETE FROM trials')
    yield


def post_json(client, path, payload, auth):
    return client.post(path, json=payload, headers=auth)


def make_sample(client, auth, quantity, prefix='QY-T'):
    code = f'{prefix}-{uuid.uuid4().hex[:8]}'
    response = post_json(client, '/samples', {'code': code, 'title': '测试留样 ' + code, 'quantity': quantity, 'location': '冷藏间 T-01'}, auth)
    assert response.status_code == 201, response.text
    return response.json()


def make_transfer(client, auth, sample_id, quantity):
    code = f'MOVE-{uuid.uuid4().hex[:8]}'
    response = post_json(client, '/transfers', {'code': code, 'sample_id': sample_id, 'destination': '理化检测室', 'quantity': quantity, 'note': '测试'}, auth)
    assert response.status_code == 201, response.text
    return response.json()


def freeze(client, auth):
    code = f'COUNT-{uuid.uuid4().hex[:8]}'
    response = post_json(client, '/counts', {'batch_code': code, 'note': '测试盘点'}, auth)
    assert response.status_code == 201, response.text
    return response.json()


def enter(client, auth, count, quantities):
    lines = [{'line_id': line['id'], 'physical_quantity': quantities.get(line['sample_code'], line['frozen_quantity'])} for line in count['lines']]
    response = client.put(f"/counts/{count['id']}/lines", json={'lines': lines}, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def sample_by_code(db, code):
    return db.execute('SELECT id, quantity, quantity_version FROM samples WHERE code=%s', (code,)).fetchone()


def audit_count(db, batch_code, prefix):
    return db.execute("SELECT count(*) FROM audit_logs WHERE action LIKE %s", (prefix + batch_code + '%',)).fetchone()[0]


def line_for(count, code):
    return next(line for line in count['lines'] if line['sample_code'] == code)


def test_freeze_captures_whole_library_and_excludes_later_samples(client, auth):
    first = make_sample(client, auth, 50, 'QY-FREEZE')
    count = freeze(client, auth)
    second = make_sample(client, auth, 70, 'QY-LATER')
    detail = client.get(f"/counts/{count['id']}", headers=auth).json()
    codes = {line['sample_code'] for line in detail['lines']}
    assert first['code'] in codes
    assert second['code'] not in codes
    assert line_for(detail, first['code'])['frozen_quantity'] == 50
    assert detail['status'] == 'pending'
    assert detail['summary']['pending'] == detail['summary']['total']


def test_daily_edits_and_registrations_continue_after_freeze(client, auth):
    sample = make_sample(client, auth, 40, 'QY-DAILY')
    freeze(client, auth)
    updated = client.put(f"/samples/{sample['id']}", json={'code': sample['code'], 'title': sample['title'], 'quantity': 45, 'location': sample['location']}, headers=auth)
    assert updated.status_code == 200, updated.text
    assert updated.json()['quantity'] == 45
    make_sample(client, auth, 10, 'QY-DAILY-NEW')


def test_post_applies_physical_counts_and_invalidates_oversized_transfer(client, auth):
    sample = make_sample(client, auth, 30, 'QY-POST')
    transfer = make_transfer(client, auth, sample['id'], 25)
    count = freeze(client, auth)
    entered = enter(client, auth, count, {sample['code']: 20})
    posted = post_json(client, f"/counts/{count['id']}/post", {}, auth)
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body['status'] == 'posted'
    assert line_for(body, sample['code'])['result'] == 'adjusted'
    listed = {x['code']: x for x in client.get('/samples', headers=auth).json()}
    assert listed[sample['code']]['quantity'] == 20
    transfer_row = next(x for x in client.get('/transfers', headers=auth).json() if x['id'] == transfer['id'])
    assert transfer_row['invalidated'] is True
    assert '低于移交量' in transfer_row['invalid_reason']
    launch = post_json(client, '/trials', {'name': 'invalid-transfer', 'transfer_id': transfer['id'], 'timeout_ms': 250}, auth)
    assert launch.status_code == 409
    assert '已失效' in launch.json()['detail']


def test_quantity_change_after_freeze_rejects_whole_batch_and_keeps_stock(client, auth, db):
    changed = make_sample(client, auth, 60, 'QY-CHG')
    untouched = make_sample(client, auth, 15, 'QY-OK')
    count = freeze(client, auth)
    client.put(f"/samples/{changed['id']}", json={'code': changed['code'], 'title': changed['title'], 'quantity': 55, 'location': changed['location']}, headers=auth)
    enter(client, auth, count, {changed['code']: 10, untouched['code']: 99})
    response = post_json(client, f"/counts/{count['id']}/post", {}, auth)
    assert response.status_code == 409
    assert '整批拒绝' in response.json()['detail']
    assert '冻结后可用量已变更' in response.json()['detail']
    # Original stock is untouched for every sample, including the ones that matched.
    assert sample_by_code(db, changed['code'])[1] == 55
    assert sample_by_code(db, untouched['code'])[1] == 15
    detail = client.get(f"/counts/{count['id']}", headers=auth).json()
    assert detail['status'] == 'conflict'
    assert all(line['result'] == 'conflict' for line in detail['lines'])
    assert '冻结后可用量已变更' in detail['failed_reason']
    # Retrying the rejected batch is refused and does not duplicate the rejection audit.
    replay = post_json(client, f"/counts/{count['id']}/post", {}, auth)
    assert replay.status_code == 409
    assert audit_count(db, detail['batch_code'], '盘点整批拒绝 ') == 1


def test_changed_away_and_back_still_conflicts(client, auth):
    sample = make_sample(client, auth, 42, 'QY-ROUNDTRIP')
    count = freeze(client, auth)
    for value in (41, 42):
        response = client.put(f"/samples/{sample['id']}", json={'code': sample['code'], 'title': sample['title'], 'quantity': value, 'location': sample['location']}, headers=auth)
        assert response.status_code == 200
    enter(client, auth, count, {})
    response = post_json(client, f"/counts/{count['id']}/post", {}, auth)
    assert response.status_code == 409
    assert '冻结后可用量已变更' in response.json()['detail']


def test_retry_after_success_does_not_adjust_or_audit_twice(client, auth, db):
    sample = make_sample(client, auth, 33, 'QY-RETRY')
    count = freeze(client, auth)
    enter(client, auth, count, {sample['code']: 31})
    first = post_json(client, f"/counts/{count['id']}/post", {}, auth)
    assert first.status_code == 200, first.text
    version = sample_by_code(db, sample['code'])[2]
    second = post_json(client, f"/counts/{count['id']}/post", {}, auth)
    assert second.status_code == 200
    assert second.json()['status'] == 'posted'
    assert sample_by_code(db, sample['code'])[1] == 31
    assert sample_by_code(db, sample['code'])[2] == version
    assert audit_count(db, first.json()['batch_code'], '盘点落账 ') == 1


def test_interrupted_post_leaves_no_partial_state_and_can_retry(client, auth, db):
    sample = make_sample(client, auth, 24, 'QY-CRASH')
    count = freeze(client, auth)
    enter(client, auth, count, {sample['code']: 18})
    db.execute("""
        CREATE OR REPLACE FUNCTION fail_post() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'injected interruption'; END; $$ LANGUAGE plpgsql""")
    db.execute("CREATE TRIGGER fail_post BEFORE UPDATE ON samples FOR EACH ROW EXECUTE FUNCTION fail_post()")
    try:
        broken = post_json(client, f"/counts/{count['id']}/post", {}, auth)
        assert broken.status_code == 500
    finally:
        db.execute('DROP TRIGGER fail_post ON samples')
        db.execute('DROP FUNCTION fail_post')
    detail = client.get(f"/counts/{count['id']}", headers=auth).json()
    assert detail['status'] == 'pending'
    assert sample_by_code(db, sample['code'])[1] == 24
    assert audit_count(db, detail['batch_code'], '盘点落账 ') == 0
    retried = post_json(client, f"/counts/{count['id']}/post", {}, auth)
    assert retried.status_code == 200, retried.text
    assert sample_by_code(db, sample['code'])[1] == 18
    assert sample_by_code(db, sample['code'])[2] == 1  # adjusted exactly once
    assert audit_count(db, detail['batch_code'], '盘点落账 ') == 1


def test_post_rejects_batch_when_a_sample_has_a_running_trial(client, auth, db):
    sample = make_sample(client, auth, 50, 'QY-TRIAL')
    transfer = make_transfer(client, auth, sample['id'], 20)
    count = freeze(client, auth)
    enter(client, auth, count, {sample['code']: 40})
    trial = post_json(client, '/trials', {'name': 'running-trial', 'transfer_id': transfer['id'], 'timeout_ms': 250}, auth)
    assert trial.status_code == 201, trial.text
    response = post_json(client, f"/counts/{count['id']}/post", {}, auth)
    assert response.status_code == 409
    assert '已发起演练' in response.json()['detail']
    assert sample_by_code(db, sample['code'])[1] == 50


def test_post_and_trial_launch_are_mutually_exclusive(client, auth, db):
    """Concurrent post/launch: exactly one wins; the loser observes the winner's committed state."""
    sample = make_sample(client, auth, 12, 'QY-RACE')
    transfer = make_transfer(client, auth, sample['id'], 10)
    count = freeze(client, auth)
    enter(client, auth, count, {sample['code']: 5})
    barrier = threading.Barrier(2)
    results = {}

    def post_worker():
        with httpx.Client(base_url=str(client.base_url), timeout=30) as c:
            barrier.wait()
            results['post'] = c.post(f"/counts/{count['id']}/post", json={}, headers=auth)

    def trial_worker():
        with httpx.Client(base_url=str(client.base_url), timeout=30) as c:
            barrier.wait()
            results['trial'] = c.post('/trials', json={'name': 'race-trial', 'transfer_id': transfer['id'], 'timeout_ms': 250}, headers=auth)

    threads = [threading.Thread(target=post_worker), threading.Thread(target=trial_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert results['post'].status_code in (200, 409)
    assert results['trial'].status_code in (201, 409)
    detail = client.get(f"/counts/{count['id']}", headers=auth).json()
    transfer_row = next(x for x in client.get('/transfers', headers=auth).json() if x['id'] == transfer['id'])
    stock = sample_by_code(db, sample['code'])[1]
    if results['post'].status_code == 200:
        # Posting won: stock adjusted, oversize transfer invalidated, trial launch refused.
        assert results['trial'].status_code == 409
        assert stock == 5
        assert transfer_row['invalidated'] is True
        assert detail['status'] == 'posted'
    else:
        # Trial launch won: batch rejected whole, stock untouched, trial accepted.
        assert results['trial'].status_code == 201
        assert stock == 12
        assert transfer_row['invalidated'] is False
        assert detail['status'] == 'conflict'
        assert '已发起演练' in detail['failed_reason']


def test_quantity_edit_racing_post_is_serialised(client, auth, db):
    sample = make_sample(client, auth, 20, 'QY-EDITRACE')
    count = freeze(client, auth)
    enter(client, auth, count, {sample['code']: 30})
    barrier = threading.Barrier(2)
    results = {}

    def post_worker():
        with httpx.Client(base_url=str(client.base_url), timeout=30) as c:
            barrier.wait()
            results['post'] = c.post(f"/counts/{count['id']}/post", json={}, headers=auth)

    def edit_worker():
        with httpx.Client(base_url=str(client.base_url), timeout=30) as c:
            barrier.wait()
            results['edit'] = c.put(f"/samples/{sample['id']}", json={'code': sample['code'], 'title': sample['title'], 'quantity': 25, 'location': sample['location']}, headers=auth)

    threads = [threading.Thread(target=post_worker), threading.Thread(target=edit_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert results['edit'].status_code == 200
    detail = client.get(f"/counts/{count['id']}", headers=auth).json()
    if results['post'].status_code == 200:
        # Post committed first; the edit then applies on top of the posted stock.
        assert detail['status'] == 'posted'
        assert sample_by_code(db, sample['code'])[1] == 25
    else:
        assert results['post'].status_code == 409
        assert detail['status'] == 'conflict'
        assert sample_by_code(db, sample['code'])[1] == 25


def _hold_lock_then_post(client, auth, count_id, sample_id, mutate_sql):
    """Deterministic race: hold the sample lock, let the post block, mutate, release first."""
    import time
    import psycopg
    from conftest import RAW_DSN
    holder = psycopg.connect(RAW_DSN)
    holder.execute('BEGIN')
    holder.execute('SELECT id FROM samples WHERE id=%s FOR UPDATE', (sample_id,))
    outcome = {}

    def run():
        with httpx.Client(base_url=str(client.base_url), timeout=60) as c:
            outcome['response'] = c.post(f'/counts/{count_id}/post', json={}, headers=auth)

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.6)  # let the request reach the row lock
    holder.execute(mutate_sql, (sample_id,))
    holder.commit()
    holder.close()
    thread.join(30)
    return outcome['response']


def test_blocked_post_observes_committed_quantity_change(client, auth, db):
    sample = make_sample(client, auth, 18, 'QY-LOCKCHG')
    count = freeze(client, auth)
    enter(client, auth, count, {sample['code']: 8})
    response = _hold_lock_then_post(
        client, auth, count['id'], sample['id'],
        'UPDATE samples SET quantity=17, quantity_version=quantity_version+1 WHERE id=%s')
    assert response.status_code == 409
    assert '冻结后可用量已变更' in response.json()['detail']
    assert sample_by_code(db, sample['code'])[1] == 17


def test_viewer_cannot_write_counts(client, auth):
    viewer = client.post('/auth/login', json={'email': 'viewer@qingyu.test', 'password': 'Demo2026!'}).json()['token']
    viewer_auth = {'Authorization': 'Bearer ' + viewer}
    assert post_json(client, '/counts', {'batch_code': 'COUNT-VIEWER'}, viewer_auth).status_code == 403
    listed = client.get('/counts', headers=viewer_auth)
    assert listed.status_code == 200
