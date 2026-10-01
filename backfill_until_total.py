#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_until_total.py — 総レース数が目標件数に届くまで過去へ遡る収集スクリプト

特徴:
- 既存の data/YYYY/races.csv を数えて、累計 TARGET_TOTAL_RACES まで収集。
- GitHub Actionsの時間制限を考慮し、BACKFILL_MAX_MINUTES で安全停止。
- data/backfill_progress.json のカーソルから再開するので、何度Runしても続きから進む。
- オッズは keirin_collector.append_odds_csv により data/YYYY/odds/odds_YYYYMMDD.csv へ日別分割保存。
"""

import csv
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import backfill as bf


def count_existing_races() -> int:
    """data/20xx/races.csv の行数を合計する。data直下の旧CSVは数えない。"""
    total = 0
    for path in sorted(bf.DATA_DIR.glob("20??/races.csv")):
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                total += sum(1 for _ in reader)
        except FileNotFoundError:
            continue
    return total


def main():
    target_total = int(os.environ.get("TARGET_TOTAL_RACES", "10000"))
    max_start_days = int(os.environ.get("MAX_START_DAYS", "365"))
    max_minutes = int(os.environ.get("BACKFILL_MAX_MINUTES", "330"))
    default_limit = (datetime.now(bf.JST) - timedelta(days=730)).strftime("%Y%m%d")
    limit_date = os.environ.get("BACKFILL_LIMIT_DATE", default_limit)

    cursor = bf.load_cursor()
    started = time.time()
    existing_at_start = count_existing_races()
    total_now = existing_at_start
    added_this_run = 0

    print("=== 10000レース級バックフィル開始 ===")
    print(f"既存レース数: {existing_at_start}")
    print(f"目標累計レース数: {target_total}")
    print(f"開始カーソル: {cursor}")
    print(f"最大探索開始日数: {max_start_days}")
    print(f"実行時間上限: {max_minutes}分")
    print(f"遡り限界日: {limit_date}")

    if total_now >= target_total:
        print(f"すでに目標 {target_total} レースに到達しています。")
        return

    for day_i in range(max_start_days):
        if total_now >= target_total:
            print(f"\n目標 {target_total} レースに到達しました。")
            break

        if cursor < limit_date:
            print(f"\n限界日({limit_date})に到達しました。現在 {total_now}/{target_total} レースです。")
            break

        elapsed_min = (time.time() - started) / 60
        if elapsed_min > max_minutes:
            print(f"\n実行時間上限({max_minutes}分)に達しました。次回 {cursor} から再開します。")
            break

        if bf.parse_fail_count > 30:
            print("\n解析失敗が多発しています。サイト構造変更の可能性があるため中断します。")
            sys.exit(1)

        print(f"\n--- {cursor} を初日とする開催を探索 ({day_i + 1}/{max_start_days}) ---")
        cups = bf.discover_cups(cursor)
        if not cups:
            print("  (この日に始まる開催なし)")

        for cup_id, vid, slug, state in cups:
            if total_now >= target_total:
                break
            elapsed_min = (time.time() - started) / 60
            if elapsed_min > max_minutes:
                print(f"\n実行時間上限({max_minutes}分)に達しました。")
                break
            try:
                result = bf.collect_cup(cup_id, vid, slug, state)
                added = int(result.get("races", 0))
                added_this_run += added
                total_now += added
                print(f"    累計: {total_now}/{target_total} レース (+{added})")
            except Exception as e:
                print(f"  ! 開催{cup_id}の収集失敗: {e}")

        # 1日分を処理したらカーソルを1日前へ進める。
        cursor = (datetime.strptime(cursor, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        bf.save_cursor(cursor)

    final_total = count_existing_races()
    print("\n=== バックフィル終了 ===")
    print(f"今回の新規保存レース数: {added_this_run}")
    print(f"CSV上の現在累計レース数: {final_total}")
    print(f"次回カーソル: {cursor}")
    if final_total < target_total:
        print("まだ目標未達です。Actionsから同じワークフローを再実行すると続きから収集します。")


if __name__ == "__main__":
    main()
