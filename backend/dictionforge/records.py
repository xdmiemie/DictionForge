import json
import uuid
from pathlib import Path
from .audio import digest

def put(jobs, kind, key, value, db=None):
    if db is None:
        with jobs.connect(write=True) as connection:
            return put(jobs, kind, key, value, connection)
    db.execute("INSERT OR REPLACE INTO records VALUES(?,?,?)", (kind, key, json.dumps(value, ensure_ascii=False, allow_nan=False)))
    return value

def get(jobs, kind, key, db=None):
    if db is None:
        with jobs.connect() as connection:
            return get(jobs, kind, key, connection)
    row = db.execute("SELECT metadata FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
    if row is None:
        raise KeyError(key)
    return json.loads(row[0])

def list_records(jobs, kind):
    with jobs.connect() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT metadata FROM records WHERE kind=? ORDER BY rowid DESC", (kind,))]

def media(jobs, path, *, external=False, owner=None, db=None):
    path = Path(path).resolve()
    if not external and not path.is_relative_to(jobs.root.resolve()):
        raise ValueError("音频路径不在管理目录")
    key = uuid.uuid4().hex
    put(jobs, "media", key, {"id": key, "path": str(path), "external": external, "owner": owner,
                              "sha256": digest(path), "state": "AVAILABLE"}, db)
    return key
