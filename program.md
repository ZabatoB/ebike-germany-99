# Goal
Optimize `train.py` to find e-bike deals in Germany.
Target: Price < 2500€, Motor: Bosch/Yamaha/Shimano.

# Notification Setup
- Topic: ebike-germany-99 (Change this to your unique name)
- Logic: ONLY send notification if `prepare.py` returns a score HIGHER than the current baseline.

# Execution Loop
1. Agent reads `results.tsv` for the "Current Best Score".
2. Agent modifies `train.py` (adding sites like *JobRad* used-bike-auctions or *Bike-Discount.de*).
3. Run `python3 train.py`.
4. Run `python3 prepare.py`.
5. IF new_score > best_score:
   - Update `results.tsv`.
   - Send ntfy.sh notification with the bike title/price.
   - `git commit -am "New high score: [score]"`
6. ELSE:
   - `git reset --hard`