"""
Database models for Job Hunter.
Uses raw sqlite3 — no ORM, no dependencies.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.environ.get("JOBHUNTER_DB", os.path.join(os.path.dirname(__file__), "jobhunter.db"))


def get_db():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_session():
    """Context manager for database transactions."""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with db_session() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT,
                location TEXT,
                job_url TEXT UNIQUE,
                date_posted TEXT,
                site TEXT,
                is_remote INTEGER DEFAULT 0,
                salary_min REAL,
                salary_max REAL,
                description TEXT,
                relevance_score INTEGER DEFAULT 0,
                status TEXT DEFAULT 'new',
                notes TEXT DEFAULT '',
                search_profile TEXT,
                search_term TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                searches_json TEXT NOT NULL,
                sites_json TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS scrape_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                profile TEXT,
                jobs_found INTEGER DEFAULT 0,
                jobs_new INTEGER DEFAULT 0,
                duration_seconds REAL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_relevance ON jobs(relevance_score DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_url ON jobs(job_url);
        """)


def seed_default_profiles():
    """Insert default search profiles if none exist."""
    with db_session() as conn:
        count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
        if count > 0:
            return

        defaults = [
            {
                "name": "devops",
                "searches": [
                    {"term": "DevOps junior", "location": "Curitiba, PR, Brazil", "distance": 50, "remote": False},
                    {"term": "DevOps estagio", "location": "Curitiba, PR, Brazil", "distance": 50, "remote": False},
                    {"term": "SRE junior", "location": "Brazil", "distance": None, "remote": True},
                    {"term": "DevOps engineer junior", "location": "Brazil", "distance": None, "remote": True},
                ],
                "sites": ["indeed", "linkedin", "glassdoor"],
            },
            {
                "name": "sysadmin",
                "searches": [
                    {"term": "administrador de sistemas junior", "location": "Curitiba, PR, Brazil", "distance": 50, "remote": False},
                    {"term": "analista infraestrutura junior", "location": "Curitiba, PR, Brazil", "distance": 50, "remote": False},
                    {"term": "analista TI junior", "location": "Curitiba, PR, Brazil", "distance": 50, "remote": False},
                    {"term": "infrastructure engineer junior", "location": "Brazil", "distance": None, "remote": True},
                ],
                "sites": ["indeed", "linkedin", "glassdoor"],
            },
            {
                "name": "estagio",
                "searches": [
                    {"term": "estagio TI infraestrutura", "location": "Curitiba, PR, Brazil", "distance": 30, "remote": False},
                    {"term": "estagio DevOps", "location": "Curitiba, PR, Brazil", "distance": 50, "remote": False},
                    {"term": "estagio cloud", "location": "Curitiba, PR, Brazil", "distance": 50, "remote": False},
                    {"term": "estagio Linux Docker", "location": "Curitiba, PR, Brazil", "distance": 50, "remote": False},
                    {"term": "estagio redes", "location": "Curitiba, PR, Brazil", "distance": 50, "remote": False},
                ],
                "sites": ["indeed", "linkedin", "glassdoor"],
            },
            {
                "name": "remote-intl",
                "searches": [
                    {"term": "junior DevOps engineer", "location": "Brazil", "distance": None, "remote": True},
                    {"term": "junior site reliability engineer", "location": "Brazil", "distance": None, "remote": True},
                    {"term": "junior infrastructure engineer", "location": "Brazil", "distance": None, "remote": True},
                    {"term": "junior systems administrator remote", "location": "", "distance": None, "remote": True},
                    {"term": "DevOps intern remote", "location": "", "distance": None, "remote": True},
                ],
                "sites": ["indeed", "linkedin", "glassdoor", "zip_recruiter"],
            },
        ]

        for p in defaults:
            conn.execute(
                "INSERT INTO profiles (name, searches_json, sites_json, is_active) VALUES (?, ?, ?, 1)",
                (p["name"], json.dumps(p["searches"]), json.dumps(p["sites"])),
            )


# --- Job CRUD ---

def upsert_job(job: dict) -> bool:
    """Insert or update a job. Returns True if the job is new."""
    now = datetime.now().isoformat()
    with db_session() as conn:
        existing = conn.execute("SELECT id FROM jobs WHERE job_url = ?", (job.get("job_url"),)).fetchone()
        if existing:
            conn.execute(
                "UPDATE jobs SET last_seen = ?, relevance_score = ? WHERE id = ?",
                (now, job.get("relevance_score", 0), existing["id"]),
            )
            return False
        else:
            conn.execute(
                """INSERT INTO jobs (title, company, location, job_url, date_posted, site,
                   is_remote, salary_min, salary_max, description, relevance_score,
                   status, search_profile, search_term, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?)""",
                (
                    job.get("title", ""),
                    job.get("company", ""),
                    job.get("location", ""),
                    job.get("job_url", ""),
                    job.get("date_posted", ""),
                    job.get("site", ""),
                    1 if job.get("is_remote") else 0,
                    job.get("min_amount"),
                    job.get("max_amount"),
                    job.get("description", ""),
                    job.get("relevance_score", 0),
                    job.get("search_profile", ""),
                    job.get("search_term", ""),
                    now,
                    now,
                ),
            )
            return True


def get_jobs(status=None, profile=None, search_text=None, min_relevance=0,
             remote_only=False, sort_by="relevance_score", sort_dir="DESC",
             page=1, per_page=25):
    """Fetch jobs with filters and pagination."""
    conditions = []
    params = []

    if status and status != "all":
        conditions.append("status = ?")
        params.append(status)
    if profile and profile != "all":
        conditions.append("search_profile = ?")
        params.append(profile)
    if search_text:
        conditions.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
        like = f"%{search_text}%"
        params.extend([like, like, like])
    if min_relevance > 0:
        conditions.append("relevance_score >= ?")
        params.append(min_relevance)
    if remote_only:
        conditions.append("is_remote = 1")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    allowed_sorts = {"relevance_score", "date_posted", "first_seen", "title", "company"}
    if sort_by not in allowed_sorts:
        sort_by = "relevance_score"
    if sort_dir not in ("ASC", "DESC"):
        sort_dir = "DESC"

    offset = (page - 1) * per_page

    with db_session() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM jobs {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY {sort_by} {sort_dir} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

    return [dict(r) for r in rows], total


def get_job(job_id: int) -> dict | None:
    """Fetch a single job by ID."""
    with db_session() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def update_job_status(job_id: int, status: str):
    """Update job status."""
    valid = {"new", "interested", "applied", "rejected"}
    if status not in valid:
        raise ValueError(f"Invalid status: {status}")
    with db_session() as conn:
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))


def update_job_notes(job_id: int, notes: str):
    """Update job notes."""
    with db_session() as conn:
        conn.execute("UPDATE jobs SET notes = ? WHERE id = ?", (notes, job_id))


# --- Profile CRUD ---

def get_profiles(active_only=False):
    """Fetch all search profiles."""
    with db_session() as conn:
        q = "SELECT * FROM profiles"
        if active_only:
            q += " WHERE is_active = 1"
        rows = conn.execute(q).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["searches"] = json.loads(d["searches_json"])
        d["sites"] = json.loads(d["sites_json"])
        result.append(d)
    return result


def save_profile(profile_id, name, searches, sites, is_active):
    """Create or update a profile."""
    with db_session() as conn:
        if profile_id:
            conn.execute(
                "UPDATE profiles SET name=?, searches_json=?, sites_json=?, is_active=? WHERE id=?",
                (name, json.dumps(searches), json.dumps(sites), int(is_active), profile_id),
            )
        else:
            conn.execute(
                "INSERT INTO profiles (name, searches_json, sites_json, is_active) VALUES (?, ?, ?, ?)",
                (name, json.dumps(searches), json.dumps(sites), int(is_active)),
            )


def delete_profile(profile_id):
    """Delete a profile."""
    with db_session() as conn:
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))


# --- Scrape Runs ---

def log_scrape_run(profile, jobs_found, jobs_new, duration):
    """Record a scrape run."""
    with db_session() as conn:
        conn.execute(
            "INSERT INTO scrape_runs (timestamp, profile, jobs_found, jobs_new, duration_seconds) VALUES (?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), profile, jobs_found, jobs_new, duration),
        )


def get_scrape_history(limit=20):
    """Fetch recent scrape runs."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM scrape_runs ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# --- Stats ---

def get_stats():
    """Dashboard statistics."""
    with db_session() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        new = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'new'").fetchone()[0]
        interested = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'interested'").fetchone()[0]
        applied = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'applied'").fetchone()[0]
        rejected = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'rejected'").fetchone()[0]
        avg_score = conn.execute("SELECT COALESCE(AVG(relevance_score), 0) FROM jobs").fetchone()[0]
        today = datetime.now().strftime("%Y-%m-%d")
        new_today = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE first_seen LIKE ?", (f"{today}%",)
        ).fetchone()[0]
        last_run = conn.execute(
            "SELECT * FROM scrape_runs ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

    return {
        "total": total,
        "new": new,
        "interested": interested,
        "applied": applied,
        "rejected": rejected,
        "avg_score": round(avg_score, 1),
        "new_today": new_today,
        "last_run": dict(last_run) if last_run else None,
    }
