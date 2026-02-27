#!/usr/bin/env python3
"""
Job Scraper for Gustavo Lazzari — DevOps / SRE / SysAdmin / Infrastructure
Uses python-jobspy to scrape Indeed, LinkedIn, Glassdoor, and ZipRecruiter.

Usage:
    # Activate venv first:
    source .data/jobspy-venv/bin/activate

    # Run with defaults (all searches, all sites):
    python .data/job_scraper.py

    # Run specific search profile:
    python .data/job_scraper.py --profile devops
    python .data/job_scraper.py --profile sysadmin
    python .data/job_scraper.py --profile estagio
    python .data/job_scraper.py --profile remote-intl

    # Output to specific file:
    python .data/job_scraper.py --output my_results.csv

    # Limit results per search:
    python .data/job_scraper.py --limit 50
"""

import argparse
import os
import sys
from datetime import datetime

import pandas as pd
from jobspy import scrape_jobs

# ─── Search Profiles ─────────────────────────────────────────────────────────

PROFILES = {
    "devops": {
        "searches": [
            {"term": "DevOps junior",        "location": "Curitiba, PR, Brazil", "distance": 50},
            {"term": "DevOps estágio",        "location": "Curitiba, PR, Brazil", "distance": 50},
            {"term": "SRE junior",            "location": "Brazil",              "distance": None, "remote": True},
            {"term": "DevOps engineer junior", "location": "Brazil",             "distance": None, "remote": True},
        ],
        "sites": ["indeed", "linkedin", "glassdoor"],
    },
    "sysadmin": {
        "searches": [
            {"term": "administrador de sistemas junior",  "location": "Curitiba, PR, Brazil", "distance": 50},
            {"term": "analista infraestrutura junior",    "location": "Curitiba, PR, Brazil", "distance": 50},
            {"term": "analista TI junior",                "location": "Curitiba, PR, Brazil", "distance": 50},
            {"term": "infrastructure engineer junior",    "location": "Brazil",               "distance": None, "remote": True},
        ],
        "sites": ["indeed", "linkedin", "glassdoor"],
    },
    "estagio": {
        "searches": [
            {"term": "estágio TI infraestrutura",  "location": "Curitiba, PR, Brazil", "distance": 30},
            {"term": "estágio DevOps",              "location": "Curitiba, PR, Brazil", "distance": 50},
            {"term": "estágio cloud",               "location": "Curitiba, PR, Brazil", "distance": 50},
            {"term": "estágio Linux Docker",        "location": "Curitiba, PR, Brazil", "distance": 50},
            {"term": "estágio redes",               "location": "Curitiba, PR, Brazil", "distance": 50},
        ],
        "sites": ["indeed", "linkedin", "glassdoor"],
    },
    "remote-intl": {
        "searches": [
            {"term": "junior DevOps engineer",              "location": "Brazil",  "distance": None, "remote": True},
            {"term": "junior site reliability engineer",    "location": "Brazil",  "distance": None, "remote": True},
            {"term": "junior infrastructure engineer",      "location": "Brazil",  "distance": None, "remote": True},
            {"term": "junior systems administrator remote", "location": "",        "distance": None, "remote": True},
            {"term": "DevOps intern remote",                "location": "",        "distance": None, "remote": True},
        ],
        "sites": ["indeed", "linkedin", "glassdoor", "zip_recruiter"],
    },
}


def run_search(term: str, location: str, sites: list[str], results_wanted: int = 30,
               distance: int | None = 50, remote: bool = False) -> pd.DataFrame:
    """Run a single jobspy search and return results as DataFrame."""
    print(f"  🔍 Searching: \"{term}\" in \"{location or 'Worldwide'}\" "
          f"({'remote' if remote else f'{distance}km radius'})...")

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
        jobs = scrape_jobs(**kwargs)
        print(f"    ✅ Found {len(jobs)} results")
        return jobs
    except Exception as e:
        print(f"    ⚠️  Error: {e}")
        return pd.DataFrame()


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicate listings based on title + company."""
    if df.empty:
        return df

    before = len(df)
    # Deduplicate on title + company (case-insensitive)
    df["_dedup_key"] = (df["title"].str.lower().str.strip() + "|" +
                        df["company"].str.lower().str.strip())
    df = df.drop_duplicates(subset="_dedup_key", keep="first")
    df = df.drop(columns=["_dedup_key"])
    after = len(df)

    if before != after:
        print(f"  🧹 Removed {before - after} duplicates ({before} → {after})")

    return df


def score_relevance(row: pd.Series) -> int:
    """Score a job's relevance to Gustavo's profile (0-100)."""
    score = 0
    text = f"{row.get('title', '')} {row.get('description', '')}".lower()

    # Strong signals (infrastructure/ops focus)
    strong = ["devops", "sre", "site reliability", "infrastructure", "sysadmin",
              "systems admin", "platform engineer", "cloud engineer",
              "infraestrutura", "operações"]
    for kw in strong:
        if kw in text:
            score += 15

    # Tech stack matches
    tech = ["docker", "linux", "proxmox", "prometheus", "grafana", "nginx",
            "wireguard", "cloudflare", "github actions", "ci/cd", "ansible",
            "terraform", "kubernetes", "k8s", "aws", "gcp", "zfs",
            "opnsense", "pihole", "bash", "python", "mqtt", "esphome",
            "home assistant", "containers", "lxc"]
    for kw in tech:
        if kw in text:
            score += 3

    # Level signals (bonus for junior/intern)
    if any(w in text for w in ["junior", "júnior", "jr", "estágio", "intern", "entry"]):
        score += 10

    # Location bonus
    if any(w in text for w in ["curitiba", "remoto", "remote", "latam"]):
        score += 5

    # Penalty for senior roles
    if any(w in text for w in ["senior", "sênior", "sr.", "lead", "principal", "staff"]):
        score -= 20

    return max(0, min(100, score))


def main():
    parser = argparse.ArgumentParser(description="Scrape jobs matching Gustavo's profile")
    parser.add_argument("--profile", choices=list(PROFILES.keys()) + ["all"], default="all",
                        help="Search profile to use (default: all)")
    parser.add_argument("--output", default=None,
                        help="Output CSV path (default: .data/jobs_YYYY-MM-DD.csv)")
    parser.add_argument("--limit", type=int, default=30,
                        help="Max results per search query (default: 30)")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y-%m-%d")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = args.output or os.path.join(script_dir, f"jobs_{timestamp}.csv")

    # Determine which profiles to run
    if args.profile == "all":
        profiles_to_run = list(PROFILES.keys())
    else:
        profiles_to_run = [args.profile]

    all_results = []

    for profile_name in profiles_to_run:
        profile = PROFILES[profile_name]
        print(f"\n{'='*60}")
        print(f"📋 Profile: {profile_name.upper()}")
        print(f"{'='*60}")

        for search in profile["searches"]:
            df = run_search(
                term=search["term"],
                location=search.get("location", ""),
                sites=profile["sites"],
                results_wanted=args.limit,
                distance=search.get("distance", 50),
                remote=search.get("remote", False),
            )
            if not df.empty:
                df["search_profile"] = profile_name
                df["search_term"] = search["term"]
                all_results.append(df)

    if not all_results:
        print("\n❌ No results found across any search. Try again later or adjust search terms.")
        sys.exit(1)

    # Combine and deduplicate
    combined = pd.concat(all_results, ignore_index=True)
    combined = deduplicate(combined)

    # Score relevance
    print("\n📊 Scoring relevance...")
    combined["relevance_score"] = combined.apply(score_relevance, axis=1)
    combined = combined.sort_values("relevance_score", ascending=False)

    # Select and reorder columns
    desired_cols = [
        "relevance_score", "title", "company", "location",
        "job_url", "date_posted", "search_profile", "search_term",
        "site", "is_remote", "min_amount", "max_amount",
        "description"
    ]
    available_cols = [c for c in desired_cols if c in combined.columns]
    combined = combined[available_cols]

    # Save
    combined.to_csv(output_path, index=False)

    # Summary
    print(f"\n{'='*60}")
    print(f"✅ RESULTS SUMMARY")
    print(f"{'='*60}")
    print(f"Total unique listings: {len(combined)}")
    print(f"Saved to: {output_path}")

    # Top 10 preview
    print(f"\n🏆 Top 10 by relevance:")
    print("-" * 80)
    for i, (_, row) in enumerate(combined.head(10).iterrows(), 1):
        title = str(row.get("title", "N/A"))[:50]
        company = str(row.get("company", "N/A"))[:25]
        score = row.get("relevance_score", 0)
        url = row.get("job_url", "N/A")
        print(f"  {i:2d}. [{score:3d}] {title:<50s} @ {company}")
        print(f"       {url}")

    print(f"\n💡 Open {output_path} in a spreadsheet to sort/filter all results.")
    print(f"💡 Run daily: python .data/job_scraper.py --profile estagio")


if __name__ == "__main__":
    main()
