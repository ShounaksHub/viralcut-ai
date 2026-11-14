import json
import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager
from app.config import DB_PATH

ACTIVE_STATES = ('queued', 'downloading', 'extracting_audio', 'transcribing', 'analyzing', 'selecting_clips', 'rendering')
RETRYABLE_STATES = ('failed', 'interrupted', 'cancelled')

def utc_now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=30000')
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_url TEXT,
        status TEXT NOT NULL, stage TEXT NOT NULL, progress REAL,
        filename TEXT, metadata TEXT, transcript_path TEXT,
        error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        worker_token TEXT, heartbeat_at TEXT,
        processing_mode TEXT NOT NULL DEFAULT 'BALANCED')''')
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(jobs)')}
        for name, definition in {
        'cancel_requested': 'INTEGER NOT NULL DEFAULT 0',
        'worker_token': 'TEXT',
        'heartbeat_at': 'TEXT',
        'processing_mode': "TEXT NOT NULL DEFAULT 'BALANCED'",
        }.items():
            if name not in columns:
                conn.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_status_updated ON jobs(status, updated_at)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source_type, source_url)')
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def insert(job_id, kind, source_url=None, filename=None, processing_mode='BALANCED'):
    now = utc_now()
    with connection() as conn:
        conn.execute('INSERT INTO jobs (id, source_type, source_url, status, stage, progress, filename, created_at, updated_at, heartbeat_at, processing_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (job_id, kind, source_url, 'queued', 'Queued', None, filename, now, now, now, processing_mode.upper()))

def update(job_id, **values):
    if not values:
        return
    values['updated_at'] = utc_now()
    for col in ('metadata',):
        if col in values and values[col] is not None:
            values[col] = json.dumps(values[col])
    with connection() as conn:
        conn.execute('UPDATE jobs SET ' + ', '.join(f'{k}=?' for k in values) + ' WHERE id=?', [*values.values(), job_id])

def set_stage(job_id, status, stage, progress=None, **extra):
    update(job_id, status=status, stage=stage, progress=progress, heartbeat_at=utc_now(), **extra)

def heartbeat(job_id, **values):
    update(job_id, heartbeat_at=utc_now(), **values)

def request_cancel(job_id):
    with connection() as conn:
        changed = conn.execute(
            "UPDATE jobs SET cancel_requested=1, updated_at=?, heartbeat_at=? WHERE id=? AND status IN (" + ','.join('?' * len(ACTIVE_STATES)) + ')',
            [utc_now(), utc_now(), job_id, *ACTIVE_STATES],
        ).rowcount
    return changed == 1

def clear_cancel(job_id):
    update(job_id, cancel_requested=0)

def recover_active_jobs():
    """Recover jobs from a previous process; this process has no durable workers."""
    now = utc_now()
    with connection() as conn:
        rows = conn.execute(
            'SELECT id FROM jobs WHERE status IN (' + ','.join('?' * len(ACTIVE_STATES)) + ')', ACTIVE_STATES
        ).fetchall()
        if rows:
            conn.execute(
                'UPDATE jobs SET status=?, stage=?, error=?, progress=NULL, worker_token=NULL, heartbeat_at=?, updated_at=? WHERE status IN (' + ','.join('?' * len(ACTIVE_STATES)) + ')',
                ['interrupted', 'Interrupted after the server stopped before the job completed.', 'The previous worker did not finish cleanly.', now, now, *ACTIVE_STATES],
            )
    return [row['id'] for row in rows]

def get(job_id):
    with connection() as conn:
        row = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    if data['metadata']:
        data['metadata'] = json.loads(data['metadata'])
    return data

def list_jobs():
    with connection() as conn:
        ids = [r['id'] for r in conn.execute('SELECT id FROM jobs ORDER BY created_at DESC LIMIT 30')]
    return [get(i) for i in ids]
