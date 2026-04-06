import json
import os

def calculate_value_score(bike):
    try:
        price = float(bike.get('price', 5000))
    except (ValueError, TypeError):
        price = 5000

    # Sanity Check: Reject 1 EUR placeholders and parsing errors like 1.45
    if price < 100 or price > 5000:
        return 0

    # Sanity Check: Ensure it's actually a bike, not a printer or car door
    title = str(bike.get('title', '')).lower()
    if any(junk in title for junk in ["tür", "pavillon", "drucker", "schweller"]):
        return 0

    motor_text = str(bike.get('motor', '')).lower()

    if any(m in motor_text for m in ["bosch", "yamaha", "shimano"]):
        motor_multiplier = 1.5
    else:
        return 0

    try:
        battery_wh = float(bike.get('battery_wh', 400))
    except (ValueError, TypeError):
        battery_wh = 400

    score = (battery_wh * motor_multiplier) / (price / 100)
    return score

if __name__ == "__main__":
    if not os.path.exists('data.json'):
        print(0)
    else:
        try:
            with open('data.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                if not data:
                    print(0)
                else:
                    total_score = sum(calculate_value_score(b) for b in data)
                    print(round(total_score, 2))
        except (json.JSONDecodeError, FileNotFoundError):
            print(0)
