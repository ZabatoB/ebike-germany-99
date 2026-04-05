"""
Baseline scraper: Kleinanzeigen.de (E-Bike Bosch ≤€1500) + Rebike.com (Sale).
Outputs data.json with a list of deal dicts.
"""

import json
import re
import time
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

MOTOR_KEYWORDS = ["bosch", "yamaha", "shimano", "brose", "fazua", "specialized"]


def extract_price(text: str):
    """Parse a German price string like '1.299 €' → 1299.0"""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.]", "", text.replace(".", "").replace(",", "."))
    try:
        return float(cleaned)
    except ValueError:
        return None


def detect_motor(text: str) -> str:
    lower = text.lower()
    for kw in MOTOR_KEYWORDS:
        if kw in lower:
            return kw.capitalize()
    return "Unknown"


# ---------------------------------------------------------------------------
# Source 1 – Kleinanzeigen.de
# ---------------------------------------------------------------------------

def scrape_kleinanzeigen(max_price: int = 1500, pages: int = 3) -> list[dict]:
    results = []
    base = "https://www.kleinanzeigen.de"

    for page in range(1, pages + 1):
        # Kleinanzeigen URL pattern: /s-{query}/preis::maxPrice/k0 for page 1,
        # /s-{query}/preis::maxPrice/seite:{n}/k0 for subsequent pages
        if page == 1:
            url = f"{base}/s-e-bike-bosch/preis::{max_price}/k0"
        else:
            url = f"{base}/s-e-bike-bosch/preis::{max_price}/seite:{page}/k0"

        try:
            resp = SESSION.get(url, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"[Kleinanzeigen] page {page} error: {exc}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        # Each listing is an <article> with class "aditem"
        articles = soup.select("article.aditem")
        if not articles:
            print(f"[Kleinanzeigen] no listings on page {page}, stopping.")
            break

        for art in articles:
            title_el = art.select_one(".ellipsis") or art.select_one("h2")
            price_el = art.select_one(".aditem-main--middle--price-shipping--price")
            link_el = art.select_one("a.ellipsis") or art.select_one("a[href]")
            location_el = art.select_one(".aditem-main--top--left")
            desc_el = art.select_one(".aditem-main--middle--description")

            title = title_el.get_text(strip=True) if title_el else ""
            price_text = price_el.get_text(strip=True) if price_el else ""
            href = link_el["href"] if link_el and link_el.has_attr("href") else ""
            location = location_el.get_text(strip=True) if location_el else ""
            description = desc_el.get_text(strip=True) if desc_el else ""

            price = extract_price(price_text)
            full_text = f"{title} {description}"
            motor = detect_motor(full_text)

            if not title:
                continue

            results.append({
                "source": "Kleinanzeigen",
                "title": title,
                "price_eur": price,
                "motor": motor,
                "condition": "used",
                "location": location,
                "url": base + href if href.startswith("/") else href,
                "description": description[:200],
            })

        time.sleep(1.5)  # polite crawl delay

    print(f"[Kleinanzeigen] found {len(results)} listings")
    return results


# ---------------------------------------------------------------------------
# Source 2 – Rebike.com (sale / refurbished)
# ---------------------------------------------------------------------------

def scrape_rebike(pages: int = 3) -> list[dict]:
    results = []
    base = "https://www.rebike.com"

    for page in range(1, pages + 1):
        # Rebike sale collection (Shopify-style pagination)
        url = f"{base}/collections/sale" if page == 1 else f"{base}/collections/sale?page={page}"

        try:
            resp = SESSION.get(url, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"[Rebike] page {page} error: {exc}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        # Rebike product cards – try several common selectors
        cards = (
            soup.select(".product-card")
            or soup.select(".bike-card")
            or soup.select("article")
            or soup.select("[class*='product']")
        )

        if not cards:
            print(f"[Rebike] no cards on page {page}, stopping.")
            break

        for card in cards:
            # Rebike uses .visually-hidden span for the product title inside the link
            title_el = card.select_one(".visually-hidden") or card.select_one("[class*='title']")
            price_el = card.select_one("[class*='price']")
            link_el = card.select_one("a[href]")
            badge_el = card.select_one("[class*='badge'], [class*='label'], [class*='tag']")

            title = title_el.get_text(strip=True) if title_el else ""
            price_text = price_el.get_text(strip=True) if price_el else ""
            href = link_el["href"] if link_el and link_el.has_attr("href") else ""
            badge = badge_el.get_text(strip=True) if badge_el else "sale"

            price = extract_price(price_text)
            motor = detect_motor(title)

            if not title:
                continue

            results.append({
                "source": "Rebike",
                "title": title,
                "price_eur": price,
                "motor": motor,
                "condition": "refurbished",
                "location": "DE",
                "url": base + href if href.startswith("/") else href,
                "description": badge,
            })

        time.sleep(1.5)

    print(f"[Rebike] found {len(results)} listings")
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    deals = []
    deals.extend(scrape_kleinanzeigen(max_price=1500, pages=3))
    deals.extend(scrape_rebike(pages=3))

    # Deduplicate by URL
    seen = set()
    unique = []
    for d in deals:
        key = d.get("url", d["title"])
        if key not in seen:
            seen.add(key)
            unique.append(d)

    with open("data.json", "w", encoding="utf-8") as fh:
        json.dump(unique, fh, ensure_ascii=False, indent=2)

    print(f"Saved {len(unique)} unique deals → data.json")


if __name__ == "__main__":
    main()
