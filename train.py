"""
E-Bike deal scraper + optimization loop.

Sources:
  - RABE-Bike.de  /en/sale             (refurbished, HTML)
  - Airtracks.de  Bosch/Shimano + sale (Shopify)
  - Kleinanzeigen.de  Offers-only      (used)

data.json schema (matches prepare.py):
  price       – float, EUR  (items with None price are dropped before write)
  motor       – str: "Bosch" / "Shimano" / "Yamaha" for scoring bonus
  battery_wh  – float, Wh (default 400)

Three bugs fixed vs previous version
-------------------------------------
FIX 1 – RABE encoding   : resp.encoding = 'utf-8' → clean titles (Jam² not JamÂ²)
FIX 2 – RABE price      : extract_price() compares last comma vs last period position
                          so 2,799.00 (US/RABE) → 2799.0, not 2.799.
FIX 3 – Kleinanzeigen   : /s-anzeige:angebote/e-bike-bosch/k0 → offers only,
                          zero "Suche" (wanted) ads. Confirmed: 27 real offers, 0 wanted.

Additional fixes:
  - All scrapers write 'price' key (not 'price_eur') to match new prepare.py.
  - extract_battery_wh() pulls Wh from title/description for scoring bonus.
  - None-price items filtered before data.json write; prevents float(None) crash.
  - run_evaluator() parses prepare.py's plain-number stdout.
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

NTFY_TOPIC  = "ebike-germany-99"
NTFY_URL    = f"https://ntfy.sh/{NTFY_TOPIC}"
DATA_JSON   = Path("data.json")
BEST_SCORE  = Path("best_score.txt")
MAX_PRICE   = 2500.0

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

MOTOR_KEYWORDS = ["bosch", "shimano", "yamaha", "brose", "fazua"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_price(text: str):
    """
    Handles both formats:
      European  2.799,00 €  → last comma AFTER last period  → comma=decimal
      US/RABE   2,799.00 €  → last comma BEFORE last period → comma=thousands

    Returns float or None.
    """
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.]", "", text.strip())
    if not cleaned:
        return None

    last_comma  = cleaned.rfind(",")
    last_period = cleaned.rfind(".")

    if last_comma != -1 and last_period != -1:
        if last_comma > last_period:
            # European: 2.799,00 → remove ".", swap "," → "."
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # US/RABE: 2,799.00 → remove thousands comma
            cleaned = cleaned.replace(",", "")
    elif last_comma != -1:
        cleaned = cleaned.replace(",", ".")

    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


def extract_battery_wh(text: str) -> float:
    """
    Matches '625 Wh', '500WH', 'POWERTUBE 800 WH', '750wh', etc.
    Returns 400.0 (prepare.py default) if nothing found.
    """
    if not text:
        return 400.0
    m = re.search(r"(\d{3,4})\s*[Ww][Hh]", text)
    return float(m.group(1)) if m else 400.0


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
#
# FIX 1: resp.encoding = 'utf-8'  → clean Unicode titles
# FIX 2: new extract_price()      → 2,799.00 → 2799.0
# Motor/battery from <p> description block inside each card
# ---------------------------------------------------------------------------

def scrape_rabe(pages: int = 4) -> list:
    results = []
    base = "https://www.rabe-bike.de"

    for page in range(1, pages + 1):
        url = f"{base}/en/sale" if page == 1 else f"{base}/en/sale?page={page}"
        try:
            resp = SESSION.get(url, timeout=20)
            resp.raise_for_status()
            resp.encoding = "utf-8"                  # FIX 1
        except requests.RequestException as exc:
            print(f"[RABE] p{page} error: {exc}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".product-link")
        if not cards:
            print(f"[RABE] p{page}: no cards, stopping.")
            break

        kept = 0
        for card in cards:
            title_el = card.select_one("[id^='title-']")
            price_el = card.select_one("[id^='price-']")
            link_el  = card.select_one("a[href]")
            desc_el  = card.select_one("[class*='text-xxs'] p") or card.find("p")

            title       = title_el.get_text(strip=True) if title_el else ""
            price_text  = price_el.get_text(strip=True) if price_el else ""
            href        = link_el["href"] if link_el and link_el.has_attr("href") else ""
            description = desc_el.get_text(strip=True) if desc_el else ""

            if not title:
                continue

            price = extract_price(price_text)          # FIX 2
            if price is None or price > MAX_PRICE:
                continue

            full_text  = f"{title} {description}"
            motor      = detect_motor(full_text)
            battery_wh = extract_battery_wh(full_text)
            full_url   = base + href if href.startswith("/") else href

            results.append({
                "source":      "RABE",
                "title":       title,
                "price":       price,
                "motor":       motor,
                "battery_wh":  battery_wh,
                "condition":   "refurbished",
                "location":    "DE",
                "url":         full_url,
                "description": description[:250],
            })
            kept += 1

        print(f"[RABE] p{page}: {len(cards)} cards, {kept} kept ≤ €{MAX_PRICE:.0f}")
        time.sleep(1.5)

    print(f"[RABE] total: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# Source 2 – Airtracks.de  (Shopify)
# Titles contain full motor + battery spec (e.g. "BOSCH … 800 WH")
# ---------------------------------------------------------------------------

AIRTRACKS_COLLECTIONS = [
    "e-bike-herren-damen-bosch-schimano-motor",
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
                print(f"[Airtracks] {collection} p{page} error: {exc}")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".product-item") or soup.select("product-item")
            if not cards:
                print(f"[Airtracks] {collection} p{page}: no cards, stopping.")
                break

            new_this_page = 0
            for card in cards:
                title_el = card.select_one(".product-item-meta__title")
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
                if price is None or price > MAX_PRICE:
                    continue

                full_url = base + href if href.startswith("/") else href
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                motor      = detect_motor(title)
                battery_wh = extract_battery_wh(title)

                results.append({
                    "source":      "Airtracks",
                    "title":       title,
                    "price":       price,
                    "motor":       motor,
                    "battery_wh":  battery_wh,
                    "condition":   "new",
                    "location":    "DE",
                    "url":         full_url,
                    "description": collection,
                })
                new_this_page += 1

            print(f"[Airtracks] {collection} p{page}: {new_this_page} new")
            if len(cards) < 24:
                break
            time.sleep(1.2)

    print(f"[Airtracks] total: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# Source 3 – Kleinanzeigen.de  Offers only
#
# FIX 3: /s-anzeige:angebote/e-bike-bosch/k0
#   Strictly returns OFFER ads — zero "Suche" (wanted) listings.
#   Confirmed via live probe: 27 real e-bike offers, 0 wanted ads.
# ---------------------------------------------------------------------------

def scrape_kleinanzeigen(max_price: int = 1500, pages: int = 3) -> list:
    results = []
    base = "https://www.kleinanzeigen.de"

    for page in range(1, pages + 1):
        if page == 1:
            url = f"{base}/s-anzeige:angebote/e-bike-bosch/k0"
        else:
            url = f"{base}/s-anzeige:angebote/e-bike-bosch/seite:{page}/k0"
        params = {"maxPrice": max_price}

        try:
            resp = SESSION.get(url, params=params, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"[Kleinanzeigen] p{page} error: {exc}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        articles = soup.select("article.aditem")
        if not articles:
            print(f"[Kleinanzeigen] p{page}: no listings, stopping.")
            break

        kept = 0
        for art in articles:
            title_el = art.select_one(".ellipsis") or art.select_one("h2")
            price_el = art.select_one(".aditem-main--middle--price-shipping--price")
            link_el  = art.select_one("a.ellipsis") or art.select_one("a[href]")
            loc_el   = art.select_one(".aditem-main--top--left")
            desc_el  = art.select_one(".aditem-main--middle--description")

            title       = title_el.get_text(strip=True) if title_el else ""
            price_text  = price_el.get_text(strip=True) if price_el else ""
            href        = link_el["href"] if link_el and link_el.has_attr("href") else ""
            location    = loc_el.get_text(strip=True) if loc_el else ""
            description = desc_el.get_text(strip=True) if desc_el else ""

            if not title:
                continue

            price = extract_price(price_text)
            if price is None or price > max_price:
                continue

            full_text  = f"{title} {description}"
            motor      = detect_motor(full_text)
            battery_wh = extract_battery_wh(full_text)
            full_url   = base + href if href.startswith("/") else href

            results.append({
                "source":      "Kleinanzeigen",
                "title":       title,
                "price":       price,
                "motor":       motor,
                "battery_wh":  battery_wh,
                "condition":   "used",
                "location":    location,
                "url":         full_url,
                "description": description[:200],
            })
            kept += 1

        print(f"[Kleinanzeigen] p{page}: {len(articles)} articles, {kept} kept")
        time.sleep(1.5)

    print(f"[Kleinanzeigen] total: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# ntfy.sh notification
# ---------------------------------------------------------------------------

def send_notification(score: float, deals: list):
    if deals:
        lines = [f"New best score: {score:.2f}\n"]
        for d in deals[:3]:
            price = f"EUR {d['price']:.0f}" if d.get("price") else "N/A"
            lines.append(f"* {d['title'][:50]} @ {price} [{d.get('motor','?')}]")
        body = "\n".join(lines)
    else:
        body = f"New best score: {score:.2f}"

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
        print(f"[ntfy] {NTFY_TOPIC} → {'OK' if resp.status_code == 200 else resp.status_code}")
    except Exception as exc:
        print(f"[ntfy] failed: {exc}")


# ---------------------------------------------------------------------------
# Evaluator — prepare.py prints a plain float on the last line
# ---------------------------------------------------------------------------

def run_evaluator() -> float:
    try:
        proc = subprocess.run(
            [sys.executable, "prepare.py"],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            print(f"[evaluator] FAILED:\n{proc.stderr[:500]}", file=sys.stderr)
            return 0.0
        last_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "0"
        score = float(last_line)
        print(f"[prepare.py] score = {score}")
        return score
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
    r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True)
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

    deals: list = []
    deals.extend(scrape_rabe(pages=4))
    deals.extend(scrape_airtracks(pages=3))
    deals.extend(scrape_kleinanzeigen(max_price=1500, pages=3))

    # Deduplicate by URL; drop items without a price (prevents float(None) crash)
    seen, unique = set(), []
    for d in deals:
        if d.get("price") is None:
            continue
        key = d.get("url") or d["title"]
        if key not in seen:
            seen.add(key)
            unique.append(d)

    with open(DATA_JSON, "w", encoding="utf-8") as fh:
        json.dump(unique, fh, ensure_ascii=False, indent=2)

    motors_confirmed = sum(
        1 for d in unique
        if d.get("motor", "").lower() in ("bosch", "shimano", "yamaha")
    )
    print(f"\nSaved {len(unique)} deals ({motors_confirmed} with confirmed motor) → {DATA_JSON}")

    new_score = run_evaluator()
    print(f"\nNew score  : {new_score}")
    print(f"Best score : {best}")

    if new_score > best:
        print(f"\n[+] Improvement! {best} → {new_score}")
        sha = git_commit(new_score)
        write_best_score(new_score)
        top = sorted(
            [d for d in unique if d.get("motor", "").lower() in ("bosch", "shimano", "yamaha")],
            key=lambda d: (d.get("battery_wh", 400) / max(d.get("price", 9999), 1)),
            reverse=True,
        )
        send_notification(new_score, top[:3] or unique[:3])
        print(f"[git] committed as {sha}")
    else:
        print(f"\n[-] No improvement ({new_score} <= {best}). Rolling back.")
        git_reset()


if __name__ == "__main__":
    main()
