#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
締切前のオッズ → 確定オッズの「下がり方」を学習する:  python3 analysis/odds_drift.py --data <データ用リポジトリの data>

締切後にも票が入るため、締切前に見たオッズより確定オッズ（払戻の倍率）は下がりやすい。
特に「締切前に割安に見えた組」ほど、後から票が集まって下がりやすい（だから割安に見えていた）。
そこで、記録した締切前オッズ（data/YYYY/odds_snap/snap_*.csv）と確定オッズ（data/YYYY/odds/odds_*.csv、
的中組は払戻÷100）を突き合わせ、券種 × 時点 × オッズ帯 × 割安さ（締切前のEV帯）ごとに
「確定 ÷ 締切前」の比の中央値を求め、analysis/drift.json に保存する（集計値のみ。個別のオッズは含めない）。

期待値ボード・通知・台帳は、この比で「確定時の予想オッズ」を出し、それで期待値を判定する。

v2（回帰）: 下がり方は「締切前にどれだけ割安に見えたか（EV）」で大きく変わる（割安に見えた組ほど下がる）。
区分ごとの中央値では EV の高い区分の点数が足りず、全体（安い組はむしろ上がる）の値が使われて甘くなっていたため、
時点ごとに  log(確定/締切前) = 券種の定数 + b1·log(EV) + b2·min(log(EV), 0) + c·log(オッズ)  を
重み付き最小二乗で当てはめる（EV 0.8 以上の組を重く、3連単を軽く）。データが足りない時点は従来の表を使う。
"""
import argparse
import csv
import glob
import json
import math
import os
from collections import defaultdict
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = ['3連単', '3連複', '2車単', '2車複', 'ワイド']
BANDS = [(1, 3), (3, 5), (5, 10), (10, 20), (20, 50), (50, 100), (100, 300), (300, 100000)]
MIN_N = 30  # これ未満の区分は、上の区分（オッズ帯を無視）の値を使う
MODEL_MIN_PAIRS = 1500   # 回帰を使うのに必要な、その時点の組数
MODEL_MIN_HIGH = 60      # うち EV 0.9 以上の組数（割安に見えた組の下がり方を学ぶため）
RATIO_CLIP = (0.3, 2.0)


def _x(bt, ev, odds):
    le = math.log(min(max(ev, 0.2), 4.0))
    return [1.0 if bt == t else 0.0 for t in TYPES] + [le, min(le, 0.0), math.log(odds)]


def fit_model(rows):
    """rows: [(券種, 時点, 締切前オッズ, 締切前EV, log(確定/締切前))] → {時点: {coef, n, n_high}}"""
    import numpy as np
    by = defaultdict(list)
    for r in rows:
        by[r[1]].append(r)
    out = {}
    for snap, rs in by.items():
        n_high = sum(1 for r in rs if r[3] >= 0.9)
        if len(rs) < MODEL_MIN_PAIRS or n_high < MODEL_MIN_HIGH:
            continue
        A = np.array([_x(bt, ev, o) for bt, _, o, ev, _ in rs])
        y = np.array([r[4] for r in rs])
        w = np.sqrt(np.array([(0.2 if r[0] == '3連単' else 1.0) * (3.0 if r[3] > 0.8 else 1.0) for r in rs]))
        coef = np.linalg.lstsq(A * w[:, None], y * w, rcond=None)[0]
        out[snap] = dict(coef=[round(float(c), 5) for c in coef], n=len(rs), n_high=n_high)
    return out


def model_ratio(m, bt, odds, ev):
    c = m['coef']
    u = sum(a * b for a, b in zip(c, _x(bt, ev, odds)))
    if bt not in TYPES:  # 知らない券種は券種の定数の平均
        u += sum(c[:len(TYPES)]) / len(TYPES)
    return min(max(math.exp(u), RATIO_CLIP[0]), RATIO_CLIP[1])


def band_of(o):
    for lo, hi in BANDS:
        if lo <= o < hi:
            return f'{lo}-{hi if hi < 100000 else ""}'
    return None


def norm(bt, combo):
    parts = [int(x) for x in str(combo).split('-') if x.strip().isdigit()]
    if bt in ('3連複', '2車複', 'ワイド'):
        parts = sorted(parts)
    return '-'.join(map(str, parts))


def read(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def final_odds(data: Path, date: str):
    """確定オッズ {(race_id, 券種, 組): 倍率}（的中組は払戻÷100。ワイドは最低倍率）"""
    out = {}
    p = data / date[:4] / 'odds' / f'odds_{date}.csv'
    if not p.exists():
        return out
    for r in read(p):
        if r.get('bet_type') not in TYPES or str(r.get('absent', '0')) not in ('0', ''):
            continue
        try:
            o = float(r['odds'])
        except (TypeError, ValueError):
            continue
        k = (r['race_id'], r['bet_type'], norm(r['bet_type'], r['combination']))
        pay = r.get('payoff')
        if pay not in (None, '') and r['bet_type'] != 'ワイド':
            try:
                o = float(pay) / 100.0
            except ValueError:
                pass
        if o >= 1.0:
            out[k] = o
    return out


def collect(data: Path, ev_of=None):
    """(券種, 時点, オッズ帯, 割安さ帯) → [log(確定/締切前), ...]。ev_of(race, bt, key, snap) は締切前のEV（無ければ None）
    回帰用の行 [(券種, 時点, オッズ, EV, log比)] は collect.rows に入れる"""
    logs = defaultdict(list)
    collect.rows = []
    for sp in sorted(glob.glob(str(data / '*' / 'odds_snap' / 'snap_*.csv'))):
        date = Path(sp).stem.split('_')[1]
        fin = final_odds(data, date)
        if not fin:
            continue
        for r in read(sp):
            bt = r.get('bet_type')
            if bt not in TYPES:
                continue
            try:
                o = float(r['odds'])
            except (TypeError, ValueError):
                continue
            k = (r['race_id'], bt, norm(bt, r['combination']))
            f = fin.get(k)
            if f is None or o < 1.0:
                continue
            b = band_of(o)
            evb = ''
            if ev_of:
                ev = ev_of(r['race_id'], bt, k[2], r['snap'])
                evb = '' if ev is None else ('EV1.1以上' if ev >= 1.1 else ('EV0.9-1.1' if ev >= 0.9 else 'EV0.9未満'))
                if ev is not None and ev > 0:
                    collect.rows.append((bt, r['snap'], o, ev, math.log(f / o)))
            logs[(bt, r['snap'], b, evb)].append(math.log(f / o))
    return logs


def summarize(logs):
    def med(xs):
        xs = sorted(xs)
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    table = {}
    agg = defaultdict(list)
    for (bt, snap, b, evb), xs in logs.items():
        agg[(bt, snap, b, '')] += xs
        agg[(bt, snap, '', '')] += xs
        if evb:
            agg[(bt, snap, b, evb)] += xs
    for (bt, snap, b, evb), xs in agg.items():
        if len(xs) < MIN_N:
            continue
        table.setdefault(bt, {}).setdefault(snap, {})[f'{b}|{evb}'] = dict(ratio=round(math.exp(med(xs)), 4), n=len(xs),
                                                                          down=round(sum(x < 0 for x in xs) / len(xs), 3))
    return table


def ratio(table, bt, snap, odds, ev=None):
    """確定 ÷ 締切前 の予想比。細かい区分 → オッズ帯 → 券種×時点 の順に、データのあるもの。無ければ 1.0"""
    m = (table or {}).get('_model', {}).get(snap)
    if m and ev is not None and ev > 0:
        return model_ratio(m, bt, odds, ev)
    t = (table or {}).get(bt, {}).get(snap)
    if not t:
        return 1.0
    b = band_of(odds) or ''
    evb = '' if ev is None else ('EV1.1以上' if ev >= 1.1 else ('EV0.9-1.1' if ev >= 0.9 else 'EV0.9未満'))
    for key in (f'{b}|{evb}', f'{b}|', '|'):
        if key in t:
            return t[key]['ratio']
    return 1.0


def load_table(path=os.path.join(HERE, 'drift.json')):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f).get('table', {})
    except (OSError, ValueError):
        return {}


def build_ev_cache(data: Path):
    """締切前の各時点の EV（期待値ボードと同じ v105 融合）。出走表が無い日は空"""
    import sys
    sys.path.insert(0, HERE)
    sys.path.insert(0, os.path.dirname(HERE))
    import keirin_engine as E
    import snapshot_ledger as L
    model = E.load_model()
    cache = {}
    for sp in sorted(glob.glob(str(data / '*' / 'odds_snap' / 'snap_*.csv'))):
        date = Path(sp).stem.split('_')[1]
        if not (data / date[:4] / 'odds' / f'odds_{date}.csv').exists():
            continue
        snaps, cards, _, venue, _ = L.load_day(data, date)
        for rid, by_snap in snaps.items():
            if rid not in cards:
                continue
            for snap, rows in by_snap.items():
                res = E.analyze(model, cards[rid], rows, venue.get(rid, ''), {})
                if res.get('ok') and res.get('market_ok'):
                    for t in res['tickets']:
                        cache[(rid, t['type'], t['key'], snap)] = t['ev']
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', default=os.path.join(HERE, 'drift.json'))
    a = ap.parse_args()
    data = Path(a.data)
    ev_cache = build_ev_cache(data)

    def ev_of(rid, bt, key, snap):
        return ev_cache.get((rid, bt, key, snap))
    logs = collect(data, ev_of)
    table = summarize(logs)
    model = fit_model(collect.rows)
    if model:
        table['_model'] = model
    n = sum(len(v) for v in logs.values())
    json.dump(dict(note='締切前オッズ → 確定オッズの比。_model = 時点ごとの回帰（券種の定数・log EV・log オッズ）、'
                        'それ以外 = 券種 → 時点 → "オッズ帯|割安さ帯" の中央値（回帰が無い時点用）。集計値のみ',
                   pairs=n, table=table), open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'突き合わせた組 {n} / 区分 {sum(len(s) for t in table.values() for s in t.values())}')
    for snap, m in sorted(model.items()):
        ex = '・'.join(f'EV{e}→{model_ratio(m, "2車複", 20.0, e):.2f}' for e in (0.9, 1.1, 1.3, 1.6))
        print(f'  回帰 {snap}: {m["n"]}組（EV0.9以上 {m["n_high"]}）。2車複20倍の確定/締切前: {ex}')
    for bt in TYPES:
        for snap, t in sorted((table.get(bt) or {}).items()):
            if '|' in t:
                print(f'  {bt} {snap}: 確定/締切前 = {t["|"]["ratio"]:.3f}（下がった割合 {t["|"]["down"] * 100:.0f}%、{t["|"]["n"]}組）')


if __name__ == '__main__':
    main()
