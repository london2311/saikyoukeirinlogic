#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
選手の運気（六星占術・四柱推命）と成績の関係を調べる（検証用。期待値の計算には使っていない）

  * 生年月日（data/riders/birthdays.json）と、過去の出走表・結果から、運気の区分ごとの 1着率・3着内率 を数える
  * 強さの違いをならすため、「同じ頭数のレースで、競走得点が何位だった選手の平均」を期待値として比べる（実際 − 期待 と z）
  * 運気はその日の暦から毎回計算できるので、レースごとに保存はしない（生年月日だけ集めればよい）
"""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import fortune as F  # noqa: E402


def _rows(data: Path, name):
    for p in [data / name] + sorted(data.glob(f'*/{name}')):
        try:
            with open(p, newline='', encoding='utf-8-sig') as f:
                yield from csv.DictReader(f)
        except OSError:
            continue


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def study(data: Path, birthdays=None):
    data = Path(data)
    if birthdays is None:
        try:
            birthdays = json.loads((data / 'riders' / 'birthdays.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            birthdays = {}
    birth = {k: F.parse_date(v.get('birth')) for k, v in birthdays.items() if v.get('birth')}
    if not birth:
        return None
    fin = {}
    for r in _rows(data, 'results.csv'):
        try:
            fin[(r['race_id'], int(r['car_number']))] = int(r['order'] or 0)
        except (TypeError, ValueError, KeyError):
            continue
    races = {}
    for r in _rows(data, 'racecards.csv'):
        if str(r.get('absent', 0)) in ('1', 'True', 'true'):
            continue
        races.setdefault(r['race_id'], {})[r['car_number']] = r
    # 強さの基準: 頭数 × 競走得点の順位 → 1着率・3着内率
    base, items = {}, []
    for rid, rs in races.items():
        rs = list(rs.values())
        if not any((rid, int(r['car_number'])) in fin for r in rs):
            continue
        n = len(rs)
        pts = sorted(rs, key=lambda r: -(_f(r.get('race_point')) or 0))
        for rk, r in enumerate(pts, start=1):
            o = fin.get((rid, int(r['car_number'])))
            if o is None:
                continue
            b = base.setdefault((n, rk), [0, 0, 0])
            b[0] += 1
            b[1] += o == 1
            b[2] += 1 <= o <= 3
            bd = birth.get(str(r.get('player_id', '')).zfill(6))
            if bd:
                d = r['schedule_id'][:8] if r.get('schedule_id') else ''
                on = F.parse_date(f'{d[:4]}-{d[4:6]}-{d[6:8]}')
                if on:
                    items.append(((n, rk), o, F.fortune(bd, on)))
    rate = {k: (v[1] / v[0], v[2] / v[0]) for k, v in base.items() if v[0]}
    groups = {}

    def add(cat, val, key, o):
        g = groups.setdefault(cat, {}).setdefault(val, [0, 0, 0.0, 0.0, 0, 0.0])
        pw, p3 = rate[key]
        g[0] += 1
        g[1] += o == 1
        g[2] += pw
        g[3] += pw * (1 - pw)
        g[4] += 1 <= o <= 3
        g[5] += p3

    for key, o, f in items:
        add('六星・年', f['grade_year'] + f['year'], key, o)
        add('六星・月', f['grade_month'] + f['month'], key, o)
        add('六星・日', f['grade_day'] + f['day'], key, o)
        add('六星・今週', f['week']['grade'], key, o)
        add('大殺界の年', 'はい' if f['grade_year'] == '✕' else 'いいえ', key, o)
        add('天中殺の日', 'はい' if f['tcs_day'] else 'いいえ', key, o)
        add('四柱・今日の通変星', f['tsuhen_day'], key, o)
        sc = F.score(f)
        add('運気点', '+3以上' if sc >= 3 else '+1〜2' if sc >= 1 else '0' if sc == 0 else '-1〜2' if sc >= -2 else '-3以下', key, o)
    out = {}
    for cat, vals in groups.items():
        rows = []
        for val, (n, w, ew, vw, t3, e3) in sorted(vals.items()):
            rows.append([val, n, round(w / n, 4), round(ew / n, 4), round((w - ew) / math.sqrt(vw), 2) if vw > 0 else 0.0,
                         round(t3 / n, 4), round(e3 / n, 4)])
        out[cat] = rows
    return dict(riders=len(birth), starts=len(items), table=out,
                note='期待＝同じ頭数のレースで競走得点が同じ順位だった選手の平均。z が ±3 を超えて続くまでは偶然の範囲として扱う')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    a = ap.parse_args(argv)
    r = study(Path(a.data))
    if not r:
        print('生年月日のデータがまだありません')
        return
    print(f'選手 {r["riders"]}人・出走 {r["starts"]}回')
    for cat, rows in r['table'].items():
        print(cat)
        for v in rows:
            print(f'  {v[0]:<8} {v[1]:>6}回  1着 {v[2] * 100:5.1f}%（期待 {v[3] * 100:5.1f}%・z {v[4]:+.1f}）  3着内 {v[5] * 100:5.1f}%（期待 {v[6] * 100:5.1f}%）')


if __name__ == '__main__':
    main()
