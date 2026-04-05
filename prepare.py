"""
Evaluator / scorer for e-bike deals.
Reads data.json, scores each listing, prints a report, and writes results.tsv.

Scoring rubric (max 100 pts per listing):
  price_score  (40 pts) – lower price relative to €2500 ceiling = higher score
  motor_score  (30 pts) – Bosch=30, Yamaha/Shimano=20, other known=10, unknown=0
  value_score  (20 pts) – refurbished=20, used=10, new=5
  source_score (10 pts) – bonus for diversity / trusted sources

Aggregate metric: mean score across all listings (reported as "run score").
"""

import json
import sys
import csv
from pathlib import Path

PRICE_CEILING = 2500.0
MOTOR_SCORES = {
    "bosch": 30,
    "yamaha": 20,
    "shimano": 20,
    "brose": 15,
    "fazua": 15,
    "specialized": 10,
}
CONDITION_SCORES = {
    "refurbished": 20,
    "used": 10,
    "new": 5,
}
SOURCE_BONUS = {
    "Kleinanzeigen": 10,
    "Rebike": 10,
    "RABE": 8,
}


def score_listing(item: dict) -> dict:
    # --- price score ---
    price = item.get("price_eur")
    if price is None or price <= 0:
        price_score = 0
    elif price >= PRICE_CEILING:
        price_score = 0
    else:
        # Linear: €0 → 40 pts, €2500 → 0 pts
        price_score = round(40 * (1 - price / PRICE_CEILING), 1)

    # --- motor score ---
    motor = (item.get("motor") or "").lower()
    motor_score = MOTOR_SCORES.get(motor, 0)

    # --- value / condition score ---
    condition = (item.get("condition") or "").lower()
    value_score = CONDITION_SCORES.get(condition, 0)

    # --- source bonus ---
    source = item.get("source", "")
    source_score = SOURCE_BONUS.get(source, 5)

    total = price_score + motor_score + value_score + source_score

    return {
        **item,
        "price_score": price_score,
        "motor_score": motor_score,
        "value_score": value_score,
        "source_score": source_score,
        "total_score": round(total, 1),
    }


def main():
    data_file = Path("data.json")
    if not data_file.exists():
        print("ERROR: data.json not found. Run train.py first.")
        sys.exit(1)

    with open(data_file, encoding="utf-8") as fh:
        deals = json.load(fh)

    if not deals:
        print("data.json is empty – no deals to score.")
        sys.exit(0)

    scored = [score_listing(d) for d in deals]
    scored.sort(key=lambda x: x["total_score"], reverse=True)

    # Aggregate metric
    run_score = round(sum(d["total_score"] for d in scored) / len(scored), 2)

    print(f"\n{'='*60}")
    print(f"  RUN SCORE: {run_score}  ({len(scored)} listings)")
    print(f"{'='*60}")
    print(f"\n{'Rank':<5} {'Score':<7} {'Price':>7} {'Motor':<12} {'Title'}")
    print("-" * 70)
    for rank, d in enumerate(scored[:20], 1):
        price_str = f"€{d['price_eur']:.0f}" if d.get("price_eur") else "N/A"
        print(
            f"{rank:<5} {d['total_score']:<7} {price_str:>7}  "
            f"{d.get('motor', 'Unknown'):<12} {d['title'][:40]}"
        )

    # Write TSV results
    tsv_path = Path("results.tsv")
    fieldnames = [
        "rank", "total_score", "price_eur", "motor", "condition",
        "source", "title", "url", "description",
        "price_score", "motor_score", "value_score", "source_score",
    ]
    with open(tsv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for rank, d in enumerate(scored, 1):
            writer.writerow({"rank": rank, **d})

    print(f"\nWrote {len(scored)} rows → {tsv_path}")
    print(f"\nRUN_SCORE={run_score}")
    return run_score


if __name__ == "__main__":
    main()
