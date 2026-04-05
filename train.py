"""
E-Bike deal scraper + optimization loop.

Sources:
  - RABE-Bike.de  /en/sale   (refurbished, Vue SSR)
  - Airtracks.de  Bosch/Shimano collection + sale collection (Shopify)

Pipeline (run with: python3 train.py):
  1. Read current best score from best_score.txt (0 if missing).
  2. Scrape both sources for Bosch/Shimano bikes under €2500.
  3. Save results to data.json.
  4. Run prepare.py → capture RUN_SCORE from stdout.
  5. new_score > best_score?
       YES → update best_score.txt, fire ntfy.sh (topic: ebike-germany-99),
              git commit -am "New high score: {score}"
       NO  → git reset --hard
"""

import re
import json
import subprocess
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NTFY_TOPIC   = "ebike-germany-99"
NTFY_URL     = f"https://ntfy.sh/{NTFY_TOPIC}"
DATA_JSON    = Path("data.json")
BEST_SCORE   = Path("best_score.txt")
MAX_PRICE    = 2500.0

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_price(text: str):
    """Parse European price strings: '2.799,00 €' → 2799.0"""
    if not text:
        return None
    # Remove currency symbols, spaces, then convert German decimal
    cleaned = re.sub(r"[^\d,.]", "", text.strip())
    # Handle both 1.299,00 and 1299.00 formats
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
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


def read_best_score() -> float:
    try:
        return float(BEST_SCORE.read_text().strip())
    except Exception:
        return 0.0


def write_best_score(score: float):
    BEST_SCORE.write_text(f"{score}\n")


# ---------------------------------------------------------------------------
# Source 1 – RABE-Bike.de  /en/sale
# ---------------------------------------------------------------------------

def scrape_rabe(pages: int = 4) -> list:
    results = []
    base = "https://www.rabe-bike.de"

    for page in range(1, pages + 1):
        url = f"{base}/en/sale" if page == 1 else f"{base}/en/sale?page={page}"
        try:
            resp = SESSION.get(url, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"[RABE] page {page} error: {exc}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".product-link")
        if not cards:
            print(f"[RABE] no cards on page {page}, stopping.")
            break

        for card in cards:
            # Title: <span id="title-XXXXXX">
            title_el = card.select_one("[id^='title-']")
            # Price: <span id="price-XXXXXX">
            price_el = card.select_one("[id^='price-']")
            # Link: first <a href> that is a product URL (not a size-variant icon)
            link_el = card.select_one("a[href]")

            title      = title_el.get_text(strip=True) if title_el else ""
            price_text = price_el.get_text(strip=True) if price_el else ""
            href       = link_el["href"] if link_el and link_el.has_attr("href") else ""

            if not title:
                continue

            price = extract_price(price_text)
            if price and price > MAX_PRICE:
                continue

            motor = detect_motor(title)
            full_url = base + href if href.startswith("/") else href

            results.append({
                "source":      "RABE",
                "title":       title,
                "price_eur":   price,
                "motor":       motor,
                "condition":   "refurbished",
                "location":    "DE",
                "url":         full_url,
                "description": f"RABE Sale – {title[:100]}",
            })

        print(f"[RABE] page {page}: {len(cards)} cards found")
        time.sleep(1.5)

    print(f"[RABE] kept {len(results)} listings under €{MAX_PRICE:.0f}")
    return results


# ---------------------------------------------------------------------------
# Source 2 – Airtracks.de  (Shopify)
# ---------------------------------------------------------------------------

AIRTRACKS_COLLECTIONS = [
    # Bosch/Shimano-motor focused collection
    "e-bike-herren-damen-bosch-schimano-motor",
    # Dedicated sale/discount collection
    "e-bikes-fahrrader-sale",
]


def scrape_airtracks(pages: int = 3) -> list:
    results = []
    base = "https://www.airtracks.de"
    seen_urls: set = set()

    for collection in AIRTRACKS_COLLECTIONS:
        for page in range(1, pages + 1):
            url = (
                f"{base}/collections/{collection}"
                if page == 1
                else f"{base}/collections/{collection}?page={page}"
            )
            try:
                resp = SESSION.get(url, timeout=20)
                resp.raise_for_status()
            except requests.RequestException as exc:
                print(f"[Airtracks] {collection} page {page} error: {exc}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".product-item") or soup.select("product-item")
            if not cards:
                print(f"[Airtracks] {collection} page {page}: no cards, stopping.")
                break

            page_new = 0
            for card in cards:
                title_el = card.select_one(".product-item-meta__title")
                # Prefer highlighted (sale) price, fall back to any price
                price_el = (
                    card.select_one(".price--highlight")
                    or card.select_one(".price")
                )
                link_el = card.select_one("a[href]")

                title      = title_el.get_text(strip=True) if title_el else ""
                price_text = price_el.get_text(strip=True) if price_el else ""
                href       = link_el["href"] if link_el and link_el.has_attr("href") else ""

                if not title:
                    continue

                price = extract_price(price_text)
                if price and price > MAX_PRICE:
                    continue

                full_url = base + href if href.startswith("/") else href
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                motor = detect_motor(title)

                results.append({
                    "source":      "Airtracks",
                    "title":       title,
                    "price_eur":   price,
                    "motor":       motor,
                    "condition":   "new",
                    "location":    "DE",
                    "url":         full_url,
                    "description": f"{collection} – {title[:100]}",
                })
                page_new += 1

            print(f"[Airtracks] {collection} page {page}: {page_new} new items")

            # Shopify: if fewer cards than expected, we're on the last page
            if len(cards) < 24:
                break

            time.sleep(1.2)

    print(f"[Airtracks] kept {len(results)} listings under €{MAX_PRICE:.0f}")
    return results


# ---------------------------------------------------------------------------
# ntfy.sh notification
# ---------------------------------------------------------------------------

def send_notification(score: float, deals: list):
    if deals:
        top = deals[:3]
        lines = [f"New best score: {score:.2f}\n"]
        for d in top:
            price = f"EUR {d['price_eur']:.0f}" if d.get("price_eur") else "N/A"
            motor = d.get("motor", "?")
            lines.append(f"* {d['title'][:50]} @ {price} [{motor}]")
        body = "\n".join(lines)
    else:
        body = f"New best score: {score:.2f} – no top deals."

    try:
        resp = SESSION.post(
            NTFY_URL,
            data=body.encode("utf-8"),
            headers={
                "Title": f"E-Bike Alert - Score {score:.2f}".encode("ascii", "ignore").decode(),
                "Priority": "high",
                "Tags": "bike,deal,germany",
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=10,
        )
        status = "OK" if resp.status_code == 200 else f"HTTP {resp.status_code}"
        print(f"[ntfy] {NTFY_TOPIC} → {status}")
    except Exception as exc:
        print(f"[ntfy] failed: {exc}")


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

def run_evaluator() -> float:
    """Run prepare.py and return the RUN_SCORE float."""
    try:
        proc = subprocess.run(
            [sys.executable, "prepare.py"],
            capture_output=True, text=True, timeout=60,
        )
        output = proc.stdout
        print(output)
        if proc.returncode != 0:
            print(f"[evaluator] stderr: {proc.stderr[:300]}", file=sys.stderr)
            return 0.0
        for line in reversed(output.splitlines()):
            m = re.search(r"RUN_SCORE=([0-9.]+)", line)
            if m:
                return float(m.group(1))
    except Exception as exc:
        print(f"[evaluator] error: {exc}", file=sys.stderr)
    return 0.0


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_commit(score: float) -> str:
    msg = f"New high score: {score:.2f}"
    subprocess.run(["git", "add", "-A"], check=False)
    subprocess.run(["git", "commit", "-m", msg], check=False)
    r = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def git_reset():
    subprocess.run(["git", "reset", "--hard"], check=False)
    print("[git] reset – score did not improve.")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    best = read_best_score()
    print(f"Current best score : {best}")
    print("=" * 60)

    # --- Scrape ---
    deals: list = []
    deals.extend(scrape_rabe(pages=4))
    deals.extend(scrape_airtracks(pages=3))

    # Deduplicate by URL
    seen, unique = set(), []
    for d in deals:
        key = d.get("url") or d["title"]
        if key not in seen:
            seen.add(key)
            unique.append(d)

    with open(DATA_JSON, "w", encoding="utf-8") as fh:
        json.dump(unique, fh, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(unique)} unique deals → {DATA_JSON}")

    # --- Evaluate ---
    new_score = run_evaluator()
    print(f"\nNew score  : {new_score}")
    print(f"Best score : {best}")

    # --- Decide ---
    if new_score > best:
        print(f"\n[+] Improvement! {best} → {new_score}")
        sha = git_commit(new_score)
        write_best_score(new_score)
        send_notification(new_score, unique[:5])
        print(f"[git] committed as {sha}")
    else:
        print(f"\n[-] No improvement ({new_score} <= {best}). Rolling back.")
        git_reset()


if __name__ == "__main__":
    main()
