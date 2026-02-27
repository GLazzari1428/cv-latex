"""
Job scraper engine for Job Hunter.
Wraps python-jobspy and integrates with the SQLite database.
"""

import time
from datetime import datetime

import pandas as pd
from jobspy import scrape_jobs

from models import get_profiles, log_scrape_run, upsert_job


def score_relevance(row: dict) -> int:
    """Score a job's relevance to Gustavo's profile (0-100)."""
    score = 0
    text = f"{row.get('title', '')} {row.get('description', '')}".lower()

    # Strong signals (infrastructure/ops focus)
    strong = [
        "devops", "sre", "site reliability", "infrastructure", "sysadmin",
        "systems admin", "platform engineer", "cloud engineer",
        "infraestrutura", "operacoes", "observabilidade",
    ]
    for kw in strong:
        if kw in text:
            score += 15

    # Tech stack matches
    tech = [
        "docker", "linux", "proxmox", "prometheus", "grafana", "nginx",
        "wireguard", "cloudflare", "github actions", "ci/cd", "ansible",
        "terraform", "kubernetes", "k8s", "aws", "gcp", "zfs",
        "opnsense", "pihole", "bash", "python", "mqtt", "esphome",
        "home assistant", "containers", "lxc",
    ]
    for kw in tech:
        if kw in text:
            score += 3

    # Level signals (bonus for junior/intern)
    if any(w in text for w in ["junior", "junior", "jr", "estagio", "intern", "entry"]):
        score += 10

    # Location bonus
    if any(w in text for w in ["curitiba", "remoto", "remote", "latam"]):
        score += 5

    # Penalty for senior roles
    if any(w in text for w in ["senior", "senior", "sr.", "lead", "principal", "staff"]):
        score -= 20

    return max(0, min(100, score))


def run_single_search(term, location, sites, results_wanted=30,
                      distance=50, remote=False):
    """Run a single jobspy search and return list of dicts."""
    kwargs = {
        "site_name": sites,
        "search_term": term,
        "results_wanted": results_wanted,
        "hours_old": 168,  # Last 7 days
        "country_indeed": "Brazil",
    }

    if location:
        kwargs["location"] = location
    if distance is not None:
        kwargs["distance"] = distance
    if remote:
        kwargs["is_remote"] = True

    try:
        df = scrape_jobs(**kwargs)
        if df.empty:
            return []
        # Convert to list of dicts
        records = df.to_dict("records")
        # Clean up NaN values
        for r in records:
            for k, v in r.items():
                if pd.isna(v):
                    r[k] = None
            # Ensure job_url is a string
            if r.get("job_url"):
                r["job_url"] = str(r["job_url"])
        return records
    except Exception as e:
        print(f"[scraper] Error searching '{term}': {e}")
        return []


def scrape_profile(profile_name, results_per_search=30, progress_callback=None):
    """
    Scrape all searches in a profile and upsert into DB.
    
    Args:
        profile_name: Name of the profile to scrape, or "all" for all active.
        results_per_search: Max results per individual search query.
        progress_callback: Optional callable(message: str) for progress updates.
    
    Returns:
        dict with summary stats.
    """
    profiles = get_profiles(active_only=True)

    if profile_name != "all":
        profiles = [p for p in profiles if p["name"] == profile_name]

    if not profiles:
        if progress_callback:
            progress_callback(f"No active profile found: {profile_name}")
        return {"jobs_found": 0, "jobs_new": 0, "duration": 0}

    start = time.time()
    total_found = 0
    total_new = 0

    for profile in profiles:
        pname = profile["name"]
        searches = profile["searches"]
        sites = profile["sites"]

        if progress_callback:
            progress_callback(f"[{pname}] Starting ({len(searches)} searches)...")

        for i, search in enumerate(searches, 1):
            term = search["term"]
            location = search.get("location", "")
            distance = search.get("distance", 50)
            is_remote = search.get("remote", False)

            if progress_callback:
                loc_label = location or "Worldwide"
                mode = "remote" if is_remote else f"{distance}km"
                progress_callback(
                    f"[{pname}] ({i}/{len(searches)}) Searching: \"{term}\" in \"{loc_label}\" ({mode})"
                )

            results = run_single_search(
                term=term,
                location=location,
                sites=sites,
                results_wanted=results_per_search,
                distance=distance,
                remote=is_remote,
            )

            new_count = 0
            for job in results:
                job["search_profile"] = pname
                job["search_term"] = term
                job["relevance_score"] = score_relevance(job)
                is_new = upsert_job(job)
                if is_new:
                    new_count += 1

            total_found += len(results)
            total_new += new_count

            if progress_callback:
                progress_callback(
                    f"[{pname}] ({i}/{len(searches)}) Found {len(results)} results ({new_count} new)"
                )

    duration = time.time() - start

    # Log the run
    log_scrape_run(
        profile=profile_name,
        jobs_found=total_found,
        jobs_new=total_new,
        duration=round(duration, 1),
    )

    if progress_callback:
        progress_callback(
            f"Done. {total_found} total results, {total_new} new jobs. Took {duration:.1f}s."
        )

    return {
        "jobs_found": total_found,
        "jobs_new": total_new,
        "duration": round(duration, 1),
    }
