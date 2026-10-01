#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""racecards.csv の拡張列の入り具合を確認する簡易チェックスクリプト。"""
import csv
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
FIELDS = [
    "standing_count", "home_count", "back_count",
    "escape_count", "makuri_count", "sashi_count", "mark_count",
    "first_count", "second_count", "third_count", "out_count",
]

def main():
    for path in sorted(DATA_DIR.glob("20??/racecards.csv")):
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        print(f"\n{path}")
        print(f"行数: {len(rows)}")
        for field in FIELDS:
            filled = sum(1 for r in rows if str(r.get(field, "")) != "")
            print(f"  {field:16s}: {filled:7d} / {len(rows)} ({filled/len(rows)*100:5.1f}%)")
        print("サンプル:")
        sample = rows[0]
        print({k: sample.get(k, "") for k in ["race_id", "car_number", "player_name", *FIELDS, "first_rate", "second_rate", "third_rate"]})

if __name__ == "__main__":
    main()
