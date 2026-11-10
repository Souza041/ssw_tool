"""Metadados operacionais de jobs locais; sem credenciais, logs ou payloads."""
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(os.getenv("NEXUS_METRICS_DB", "data/nexus_automation_metrics.sqlite3"))

def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB), timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS automation_runs (
        id TEXT PRIMARY KEY, module TEXT NOT NULL, started_at TEXT NOT NULL,
        finished_at TEXT, status TEXT NOT NULL CHECK(status IN ('running','done','error'))
    )""")
    return conn

def started(job_id, module):
    with connect() as db:
        db.execute("INSERT INTO automation_runs (id,module,started_at,status) VALUES (?,?,?,?)",
                   (job_id, module, datetime.now(timezone.utc).isoformat(), "running"))

def finished(job_id, status):
    with connect() as db:
        db.execute("UPDATE automation_runs SET status=?,finished_at=? WHERE id=? AND status='running'",
                   (status, datetime.now(timezone.utc).isoformat(), job_id))

def snapshot(days=7, module="", status=""):
    now = datetime.now(timezone.utc)
    since = (now-timedelta(days=days-1)).date().isoformat()
    clauses = ["substr(started_at,1,10)>=?"]
    params = [since]
    if module:
        clauses.append("module LIKE ? ESCAPE '\\'")
        escaped = module.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append("%" + escaped + "%")
    if status in ("done", "error", "running"):
        clauses.append("status=?")
        params.append(status)
    with connect() as db:
        rows = db.execute("SELECT module,started_at,finished_at,status FROM automation_runs WHERE " + " AND ".join(clauses) + " ORDER BY started_at DESC", params).fetchall()
    daily = {(now-timedelta(days=i)).date().isoformat(): {"done":0,"error":0,"running":0} for i in range(days)}
    for module, started_at, finished_at, status in rows:
        date = started_at[:10]
        if date in daily:
            daily[date][status] += 1
    completed = sum(r[3] in ('done','error') for r in rows)
    success = sum(r[3]=='done' for r in rows)
    durations = []
    for module, start, end, status in rows:
        if end and status in ('done','error'):
            try: durations.append((datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds())
            except ValueError: pass
    return {"total":len(rows),"success":success,"errors":sum(r[3]=='error' for r in rows),
            "running":sum(r[3]=='running' for r in rows),"rate":round(100*success/completed,1) if completed else None,
            "avg_seconds":round(sum(durations)/len(durations),1) if durations else None,
            "daily":[{"date":k,**v} for k,v in sorted(daily.items())],
            "recent":[{"module":r[0],"started":r[1][:16].replace('T',' ')+" UTC","status":r[3]} for r in rows[:8]],
            "scope":f"Jobs locais instrumentados desde a Sprint 03; últimos {days} dias (UTC). Não inclui LG POD/OC73 externos."}
