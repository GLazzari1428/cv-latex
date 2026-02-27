"""
Job Hunter — Flask Web Application
Self-hosted job scraping dashboard with Catppuccin Mocha theming.
"""

import csv
import io
import json
import queue
import threading

from flask import (Flask, Response, redirect, render_template, request,
                   jsonify, stream_with_context, url_for)

from models import (delete_profile, get_job, get_jobs, get_profiles,
                    get_scrape_history, get_stats, init_db, save_profile,
                    seed_default_profiles, update_job_notes, update_job_status)
from scraper import scrape_profile

app = Flask(__name__)

# Global state for scrape progress
scrape_lock = threading.Lock()
scrape_in_progress = False
scrape_messages = queue.Queue()


# --- Initialize DB on startup ---

with app.app_context():
    init_db()
    seed_default_profiles()


# --- Template Helpers ---

@app.context_processor
def utility_processor():
    def build_sort_url(column):
        """Build a sort URL toggling direction for the given column."""
        args = request.args.to_dict()
        current_sort = args.get("sort", "relevance_score")
        current_dir = args.get("dir", "DESC")
        args["sort"] = column
        if current_sort == column:
            args["dir"] = "ASC" if current_dir == "DESC" else "DESC"
        else:
            args["dir"] = "DESC"
        args["page"] = "1"
        return "&".join(f"{k}={v}" for k, v in args.items())
    return {"build_sort_url": build_sort_url}


# --- Routes ---

@app.route("/")
def dashboard():
    """Main dashboard with job listings."""
    status = request.args.get("status", "all")
    profile = request.args.get("profile", "all")
    search = request.args.get("search", "")
    min_rel = int(request.args.get("min_relevance", 0))
    remote = request.args.get("remote", "") == "1"
    sort_by = request.args.get("sort", "relevance_score")
    sort_dir = request.args.get("dir", "DESC")
    page = int(request.args.get("page", 1))

    jobs, total = get_jobs(
        status=status,
        profile=profile,
        search_text=search if search else None,
        min_relevance=min_rel,
        remote_only=remote,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        per_page=25,
    )

    stats = get_stats()
    profiles = get_profiles()
    total_pages = max(1, (total + 24) // 25)

    return render_template(
        "dashboard.html",
        jobs=jobs,
        stats=stats,
        profiles=profiles,
        total=total,
        page=page,
        total_pages=total_pages,
        # Pass current filters back to template
        f_status=status,
        f_profile=profile,
        f_search=search,
        f_min_relevance=min_rel,
        f_remote=remote,
        f_sort=sort_by,
        f_dir=sort_dir,
    )


@app.route("/job/<int:job_id>")
def job_detail(job_id):
    """Single job detail view."""
    job = get_job(job_id)
    if not job:
        return "Not found", 404
    return render_template("job_detail.html", job=job)


@app.route("/job/<int:job_id>/status", methods=["POST"])
def set_job_status(job_id):
    """Update job status via AJAX or form."""
    status = request.form.get("status") or request.json.get("status")
    try:
        update_job_status(job_id, status)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if request.is_json:
        return jsonify({"ok": True, "status": status})
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/job/<int:job_id>/notes", methods=["POST"])
def set_job_notes(job_id):
    """Update job notes."""
    notes = request.form.get("notes", "")
    update_job_notes(job_id, notes)
    if request.is_json:
        return jsonify({"ok": True})
    return redirect(url_for("job_detail", job_id=job_id))


@app.route("/profiles")
def profiles_page():
    """Search profile management."""
    profiles = get_profiles()
    return render_template("profiles.html", profiles=profiles)


@app.route("/profiles/save", methods=["POST"])
def save_profile_route():
    """Save a profile (create or update)."""
    data = request.json
    profile_id = data.get("id")
    name = data.get("name", "").strip()
    searches = data.get("searches", [])
    sites = data.get("sites", [])
    is_active = data.get("is_active", True)

    if not name:
        return jsonify({"error": "Name is required"}), 400
    if not searches:
        return jsonify({"error": "At least one search is required"}), 400
    if not sites:
        return jsonify({"error": "At least one site is required"}), 400

    save_profile(profile_id, name, searches, sites, is_active)
    return jsonify({"ok": True})


@app.route("/profiles/<int:profile_id>/delete", methods=["POST"])
def delete_profile_route(profile_id):
    """Delete a profile."""
    delete_profile(profile_id)
    return jsonify({"ok": True})


@app.route("/scrape", methods=["POST"])
def trigger_scrape():
    """Start a scrape in the background."""
    global scrape_in_progress

    with scrape_lock:
        if scrape_in_progress:
            return jsonify({"error": "A scrape is already running"}), 409
        scrape_in_progress = True

    profile = request.json.get("profile", "all") if request.is_json else "all"
    limit = request.json.get("limit", 30) if request.is_json else 30

    def run():
        global scrape_in_progress
        try:
            def on_progress(msg):
                scrape_messages.put(msg)

            result = scrape_profile(profile, results_per_search=limit,
                                    progress_callback=on_progress)
            scrape_messages.put(f"DONE|{json.dumps(result)}")
        except Exception as e:
            scrape_messages.put(f"ERROR|{str(e)}")
        finally:
            with scrape_lock:
                scrape_in_progress = False

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return jsonify({"ok": True, "message": "Scrape started"})


@app.route("/scrape/status")
def scrape_status_stream():
    """SSE endpoint for live scrape progress."""
    def generate():
        while True:
            try:
                msg = scrape_messages.get(timeout=30)
                yield f"data: {msg}\n\n"
                if msg.startswith("DONE|") or msg.startswith("ERROR|"):
                    break
            except queue.Empty:
                yield f"data: waiting...\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/stats")
def stats_api():
    """JSON stats for AJAX refresh."""
    return jsonify(get_stats())


@app.route("/history")
def history_page():
    """Scrape run history."""
    runs = get_scrape_history(limit=50)
    return render_template("history.html", runs=runs)


@app.route("/export")
def export_csv():
    """Export current filtered jobs as CSV."""
    status = request.args.get("status", "all")
    profile = request.args.get("profile", "all")
    search = request.args.get("search", "")
    min_rel = int(request.args.get("min_relevance", 0))
    remote = request.args.get("remote", "") == "1"

    jobs, _ = get_jobs(
        status=status, profile=profile,
        search_text=search if search else None,
        min_relevance=min_rel, remote_only=remote,
        page=1, per_page=10000,
    )

    output = io.StringIO()
    if jobs:
        writer = csv.DictWriter(output, fieldnames=jobs[0].keys())
        writer.writeheader()
        writer.writerows(jobs)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=jobs_export.csv"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
