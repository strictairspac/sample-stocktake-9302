import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psycopg
import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
TEST_DB = os.environ.get('TEST_DB', 'locklab_test')
DB_HOST = os.environ.get('TEST_DB_HOST', '127.0.0.1')
DB_PORT = os.environ.get('TEST_DB_PORT', '17742')
DB_USER = os.environ.get('TEST_DB_USER', 'locklab')
DATABASE_URL = f'postgresql+psycopg://{DB_USER}@{DB_HOST}:{DB_PORT}/{TEST_DB}'
RAW_DSN = f'host={DB_HOST} port={DB_PORT} user={DB_USER} dbname={TEST_DB}'
REDIS_URL = os.environ.get('TEST_REDIS_URL', 'redis://127.0.0.1:6379/9')
PORT = int(os.environ.get('TEST_APP_PORT', '18081'))

os.environ['DATABASE_URL'] = DATABASE_URL
os.environ['REDIS_URL'] = REDIS_URL
os.environ['SEED_DEMO'] = 'false'
os.environ['JWT_SECRET'] = 'test-secret'

sys.path.insert(0, str(BASE_DIR))


def _reset_schema():
    with psycopg.connect(RAW_DSN, autocommit=True) as conn:
        conn.execute('DROP SCHEMA public CASCADE')
        conn.execute('CREATE SCHEMA public')
    from src.config.database import Base, engine
    from src.models import entities  # noqa: F401
    Base.metadata.create_all(engine)
    from src.config.database import ensure_schema
    ensure_schema()


def _seed_users():
    from src.config.database import Session
    from src.models.entities import User
    from src.utils.security import password_hash
    with Session() as db:
        for role in ('admin', 'operator', 'viewer'):
            db.add(User(email=role + '@qingyu.test', password=password_hash('Demo2026!'), role=role))
        db.commit()


@pytest.fixture(scope='session')
def server():
    _reset_schema()
    _seed_users()
    env = dict(os.environ)
    proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'src.main:app', '--host', '127.0.0.1', '--port', str(PORT)],
        cwd=BASE_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f'http://127.0.0.1:{PORT}'
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            if httpx.get(base + '/health', timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.25)
    else:
        proc.terminate()
        raise RuntimeError('server did not start')
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope='session')
def client(server):
    with httpx.Client(base_url=server, timeout=30) as c:
        yield c


@pytest.fixture(scope='session')
def admin_token(client):
    response = client.post('/auth/login', json={'email': 'admin@qingyu.test', 'password': 'Demo2026!'})
    assert response.status_code == 200, response.text
    return response.json()['token']


@pytest.fixture(scope='session')
def auth(admin_token):
    return {'Authorization': 'Bearer ' + admin_token}


@pytest.fixture()
def db():
    conn = psycopg.connect(RAW_DSN, autocommit=True)
    yield conn
    conn.close()
