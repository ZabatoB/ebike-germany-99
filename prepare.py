import json
import os

def calculate_value_score(bike):
    try:
        price = float(bike.get('price', 5000))
    except (ValueError, TypeError):
        price = 5000

    if price <= 0:
        return 0

    motor_text = str(bike.get('motor', '')).lower()

    # Strict Motor Requirement: Force the AI to find the motor info
    if any(m in motor_text for m in ["bosch", "yamaha", "shimano"]):
        motor_multiplier = 1.5
    else:
        # 0 points if it can't confirm a premium motor
        return 0

    try:
        battery_wh = float(bike.get('battery_wh', 400))
    except (ValueError, TypeError):
        battery_wh = 400

    # Excellent Value Formula
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
