"""
E-Bike deal scraper + optimization loop.

Pipeline:
  1. Read current best score from results.tsv (baseline = 0).
  2. Scrape Kleinanzeigen.de for 'E-Bike Bosch' under €1500.
  3. Save data.json.
  4. Run prepare.py → capture RUN_SCORE.
  5. If new_score > best_score:
       - Overwrite results.tsv with new best.
       - Send ntfy.sh push notification (topic: ebike-germany-99).
       - git commit -am "New high score: {score}".
  6. Else:
       - git reset --hard  (discard any changes).
"""

import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NTFY_TOPIC = "ebike-germany-99"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"
RESULTS_TSV = Path("results.tsv")
DATA_JSON = Path("data.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

MOTOR_KEYWORDS = ["bosch", "yamaha", "shimano", "brose", "fazua"]

# Words that MUST appear in title/desc for a Kleinanzeigen hit to be kept
BIKE_WORDS = {"bike", "e-bike", "ebike", "pedelec", "fahrrad", "rad", "bosch"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_price(text: str):
    """Parse a German price string like '1.299 €' → 1299.0"""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.]", "", text.replace(".", "").replace(",", "."))
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


def detect_motor(text: str) -> str:
    lower = text.lower()
    for kw in MOTOR_KEYWORDS:
        if kw in lower:
            return kw.capitalize()
    return "Unknown"


def is_bike_listing(title: str, description: str) -> bool:
    """Return True only if the listing looks like an actual bike."""
    combined = (title + " " + description).lower()
    return any(w in combined for w in BIKE_WORDS)


# ---------------------------------------------------------------------------
# Score bookkeeping
# ---------------------------------------------------------------------------

def read_best_score() -> float:
    """Read the top run_score row from results.tsv; return 0 if missing."""
    if not RESULTS_TSV.exists():
        return 0.0
    try:
        with open(RESULTS_TSV, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            best = 0.0
            for row in reader:
                try:
                    best = max(best, float(row.get("run_score", 0)))
                except ValueError:
                    pass
            return best
    except Exception:
        return 0.0


def write_best_score(score: float, listings: int, commit_sha: str = "pending"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    # Preserve existing rows and prepend the new best
    existing_rows = []
    if RESULTS_TSV.exists():
        try:
            with open(RESULTS_TSV, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    existing_rows.append(row)
        except Exception:
            pass

    fieldnames = ["run_score", "listings", "timestamp", "commit"]
    new_row = {
        "run_score": score,
        "listings": listings,
        "timestamp": ts,
        "commit": commit_sha,
    }
    with open(RESULTS_TSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerow(new_row)
        for row in existing_rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def send_notification(score: float, top_deals: list):
    """POST a push notification to ntfy.sh."""
    if not top_deals:
        body = f"New best score: {score:.2f} – no deals to highlight."
    else:
        lines = [f"🏆 New best score: {score:.2f}\n"]
        for d in top_deals[:3]:
            price = f"€{d['price_eur']:.0f}" if d.get("price_eur") else "N/A"
            lines.append(f"• {d['title'][:50]} @ {price}")
        body = "\n".join(lines)

    try:
        resp = requests.post(
            NTFY_URL,
            data=body.encode("utf-8"),
            headers={
                "Title": f"E-Bike Alert – Score {score:.2f}",
                "Priority": "high",
                "Tags": "bike,deal,germany",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            print(f"[ntfy] Notification sent to {NTFY_TOPIC} ✓")
        else:
            print(f"[ntfy] Unexpected status {resp.status_code}: {resp.text[:100]}")
    except Exception as exc:
        print(f"[ntfy] Failed to send notification: {exc}")


# ---------------------------------------------------------------------------
# Scraper – Kleinanzeigen.de
# ---------------------------------------------------------------------------

def scrape_kleinanzeigen(max_price: int = 1500, pages: int = 3) -> list:
    results = []
    base = "https://www.kleinanzeigen.de"

    for page in range(1, pages + 1):
        # Use keyword search query params for accurate matching
        params = {
            "keywords": "E-Bike Bosch",
            "maxPrice": max_price,
        }
        if page == 1:
            url = f"{base}/s-suche/k0"
        else:
            url = f"{base}/s-suche/seite:{page}/k0"

        try:
            resp = SESSION.get(url, params=params, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"[Kleinanzeigen] page {page} error: {exc}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        articles = soup.select("article.aditem")
        if not articles:
            print(f"[Kleinanzeigen] no listings on page {page}, stopping.")
            break

        for art in articles:
            title_el  = art.select_one(".ellipsis") or art.select_one("h2")
            price_el  = art.select_one(".aditem-main--middle--price-shipping--price")
            link_el   = art.select_one("a.ellipsis") or art.select_one("a[href]")
            loc_el    = art.select_one(".aditem-main--top--left")
            desc_el   = art.select_one(".aditem-main--middle--description")

            title       = title_el.get_text(strip=True) if title_el else ""
            price_text  = price_el.get_text(strip=True) if price_el else ""
            href        = link_el["href"] if link_el and link_el.has_attr("href") else ""
            location    = loc_el.get_text(strip=True) if loc_el else ""
            description = desc_el.get_text(strip=True) if desc_el else ""

            if not title:
                continue

            # Drop clearly off-topic results
            if not is_bike_listing(title, description):
                continue

            price = extract_price(price_text)

            # Hard filter: skip anything priced above the ceiling
            if price and price > max_price:
                continue

            motor = detect_motor(f"{title} {description}")
            full_url = base + href if href.startswith("/") else href

            results.append({
                "source":      "Kleinanzeigen",
                "title":       title,
                "price_eur":   price,
                "motor":       motor,
                "condition":   "used",
                "location":    location,
                "url":         full_url,
                "description": description[:200],
            })

        time.sleep(1.5)

    print(f"[Kleinanzeigen] kept {len(results)} relevant listings")
    return results


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

def run_evaluator() -> float:
    """Run prepare.py in a subprocess and parse RUN_SCORE=<value>."""
    try:
        result = subprocess.run(
            [sys.executable, "prepare.py"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"[evaluator] stderr: {result.stderr[:300]}", file=sys.stderr)
            return 0.0

        for line in reversed(result.stdout.splitlines()):
            m = re.search(r"RUN_SCORE=([0-9.]+)", line)
            if m:
                return float(m.group(1))
    except Exception as exc:
        print(f"[evaluator] error: {exc}", file=sys.stderr)
    return 0.0


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_commit(score: float):
    msg = f"New high score: {score:.2f}"
    subprocess.run(["git", "add", "-A"], check=False)
    subprocess.run(["git", "commit", "-m", msg], check=False)
    # Capture SHA
    sha_result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True
    )
    return sha_result.stdout.strip()


def git_reset():
    subprocess.run(["git", "reset", "--hard"], check=False)
    print("[git] Reset – score did not improve.")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    best_score = read_best_score()
    print(f"Current best score: {best_score}")

    # --- Scrape ---
    deals = scrape_kleinanzeigen(max_price=1500, pages=3)

    # Deduplicate by URL
    seen, unique = set(), []
    for d in deals:
        key = d.get("url") or d["title"]
        if key not in seen:
            seen.add(key)
            unique.append(d)

    with open(DATA_JSON, "w", encoding="utf-8") as fh:
        json.dump(unique, fh, ensure_ascii=False, indent=2)
    print(f"Saved {len(unique)} unique deals → {DATA_JSON}")

    # --- Evaluate ---
    new_score = run_evaluator()
    print(f"\nNew score:  {new_score}")
    print(f"Best score: {best_score}")

    # --- Decide ---
    if new_score > best_score:
        print(f"\n✅ Improvement! {best_score} → {new_score}")
        sha = git_commit(new_score)
        write_best_score(new_score, len(unique), sha)
        # Load scored data for notification
        try:
            import csv as _csv
            top_deals = unique[:3]
        except Exception:
            top_deals = []
        send_notification(new_score, top_deals)
    else:
        print(f"\n❌ No improvement ({new_score} ≤ {best_score}). Rolling back.")
        git_reset()


if __name__ == "__main__":
    main()
