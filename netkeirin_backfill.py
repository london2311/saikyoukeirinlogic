#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netkeirin_backfill.py — 収集が抜けている過去の日を netkeirin から少しずつ補充する（私的利用のみ）

  python netkeirin_backfill.py --out-dir _data_repo/data --from 20260701 --budget-min 150

  * data/YYYY/races.csv の日別レース数が --min-races 未満の日を「抜けている日」とみなす
  * 古い日から順に、取得済みのレースは飛ばして補充する（WINTICKET 由来のデータは上書きしない）
  * --budget-min 分を超えたら次の日に進まず終了する（続きは次回の実行で）
  * 補充を試みた日は data/netkeirin_backfill.json に記録し、同じ日を繰り返さない
    （netkeirin 側にも少ししか無い日で、毎回取り直さないため）
  * リクエスト間隔は netkeirin_collector.REQUEST_INTERVAL（3秒）のまま
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import netkeirin_collector as N

PROGRESS = 'netkeirin_backfill.json'


def known_races(out_dir: Path) -> tuple:
    """(日別レース数 Counter, 取得済みレースID set)。年フォルダの races.csv を読む。"""
    per_day, ids = Counter(), set()
    for p in sorted(out_dir.glob('*/races.csv')):
        with open(p, newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                rid, d = row.get('race_id', ''), str(row.get('date', ''))[:8]
                if rid and rid not in ids:
                    ids.add(rid)
                    per_day[d] += 1
    return per_day, ids


def load_progress(out_dir: Path) -> dict:
    try:
        return json.loads((out_dir / PROGRESS).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_progress(out_dir: Path, prog: dict):
    (out_dir / PROGRESS).write_text(json.dumps(prog, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')


def todo_dates(per_day: Counter, prog: dict, d_from: str, d_to: str, min_races: int) -> list:
    d0 = datetime.strptime(d_from, '%Y%m%d')
    d1 = datetime.strptime(d_to, '%Y%m%d')
    out = []
    while d0 <= d1:
        d = d0.strftime('%Y%m%d')
        if per_day.get(d, 0) < min_races and d not in prog:
            out.append(d)
        d0 += timedelta(days=1)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='抜けている過去の日を netkeirin から補充（私的利用のみ）')
    ap.add_argument('--out-dir', required=True, help='データ用リポジトリの data フォルダ（公開リポジトリ内は不可）')
    ap.add_argument('--from', dest='date_from', default='20260701')
    ap.add_argument('--to', dest='date_to', help='既定: 2日前（前日分は毎朝の収集に任せる）')
    ap.add_argument('--min-races', type=int, default=120, help='これ未満のレース数の日を補充対象にする')
    ap.add_argument('--budget-min', type=float, default=150, help='この分数を超えたら次の日に進まない')
    a = ap.parse_args(argv)

    out_dir = Path(a.out_dir)
    d_to = a.date_to or (datetime.now(N.JST) - timedelta(days=2)).strftime('%Y%m%d')
    per_day, ids = known_races(out_dir)
    prog = load_progress(out_dir)
    dates = todo_dates(per_day, prog, a.date_from, d_to, a.min_races)
    print(f'補充対象の日: {len(dates)}（{a.date_from}〜{d_to}、{a.min_races}レース未満）')
    t0 = time.monotonic()
    done = 0
    try:
        for d in dates:
            if (time.monotonic() - t0) / 60 > a.budget_min:
                print('時間の上限に達したため、続きは次回に回します')
                break
            st = N.collect_date(d, out_dir, skip=ids)
            prog[d] = dict(listed=st.get('listed', 0), added=st['races'], failed=st['failed'],
                           at=datetime.now(N.JST).strftime('%Y-%m-%d %H:%M'))
            if st['failed'] and st['failed'] >= max(5, st.get('listed', 0) // 2):
                del prog[d]  # 失敗が多い日は記録せず、次回もう一度試す
            save_progress(out_dir, prog)
            done += 1
    except N.Blocked as e:
        print(f'!! サーバーから拒否されたため中断しました（回避はしません）: {e}')
        save_progress(out_dir, prog)
        sys.exit(2)
    left = len(dates) - done
    print(f'今回の補充: {done}日 / 残り {left}日')


if __name__ == '__main__':
    main()
