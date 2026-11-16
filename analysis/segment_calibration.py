#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
レースの形・グレード・時間帯ごとに、市場（3連単オッズ）が本命を強く見すぎ/弱く見すぎていないかを調べる:
  python3 analysis/segment_calibration.py --data <収集フォルダ> [--data ...] [--split 20260801]

各レースの3連単の市場確率 q（1/オッズを正規化）に対し、p ∝ q^β で β を最尤推定する。
  β > 1 … 市場は本命を弱く見すぎ（本命が過小評価）  β < 1 … 本命を強く見すぎ（穴が過小評価）
区分ごとの β を発見期間で推定し、検証期間で「全体の β」より対数損失が下がるか（＝偏りが続くか）を確かめる。
区分: ライン構成 / 種別（予選・準決・決勝など）/ グレード / 級班 / 頭数 / 時間帯 / バンク周長
出力は analysis/out/（公開リポジトリには入れない）。
"""
import argparse
import math
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import train_model as T  # noqa: E402
import feature_study as FS  # noqa: E402


def race_rows(D):
    races = D['races'].copy()
    races = races[pd.to_numeric(races.get('cancel', 0), errors='coerce').fillna(0) == 0]
    races['date'] = pd.to_numeric(races['date'], errors='coerce')
    races = races.rename(columns={'class': 'klass'})
    res = D['results'].copy()
    res['order'] = pd.to_numeric(res['order'], errors='coerce')
    top = res[res['order'].between(1, 3)].sort_values(['race_id', 'order'])
    t3 = top.groupby('race_id')['car_number'].apply(lambda s: '-'.join(str(int(float(x))) for x in s))
    t3 = t3[t3.str.count('-') == 2]
    oq = FS.odds_q(D, set(t3.index))
    cards = D['cards']
    lt = cards.groupby('race_id')['line_type'].first()
    bank = FS.bank_of_venue(races)
    rows = []
    for r in races.itertuples(index=False):
        o = oq.get(r.race_id)
        if r.race_id not in t3.index or not o:
            continue
        n = int(float(r.entries_number)) if r.entries_number == r.entries_number else 0
        full = n * (n - 1) * (n - 2)
        if full == 0 or len(o) < 0.6 * full or t3[r.race_id] not in o:
            continue
        ks = list(o)
        lq = np.log(1.0 / np.array([o[k] for k in ks]))
        lq = lq - np.log(np.exp(lq).sum())
        st = pd.to_datetime(r.start_at, errors='coerce')
        hm = st.hour + st.minute / 60 if st == st else np.nan
        band = 'モーニング' if hm < 12 else 'デイ' if hm < 16 else 'ナイター' if hm < 20.5 else 'ミッドナイト' if hm == hm else '不明'
        rows.append(dict(race_id=r.race_id, date=int(r.date), lq=lq, win=ks.index(t3[r.race_id]),
                         ライン構成=str(lt.get(r.race_id, '不明')), 種別=str(r.race_type), 級班=str(r.klass),
                         グレード={1: 'F2', 2: 'F1', 3: 'G3', 4: 'G2', 5: 'G1', 6: 'GP'}.get(int(float(r.grade)), str(r.grade)),
                         頭数=f'{n}車', 時間帯=band, バンク=f'{int(bank.get(r.venue_name, 400))}'))
    return rows


def nll(rows, beta):
    tot = 0.0
    for r in rows:
        u = beta * r['lq']
        m = u.max()
        tot -= u[r['win']] - m - math.log(np.exp(u - m).sum())
    return tot


def fit_beta(rows):
    if not rows:
        return 1.0
    return minimize_scalar(lambda b: nll(rows, b), bounds=(0.5, 1.6), method='bounded').x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', action='append', required=True)
    ap.add_argument('--split', type=int, default=20260801)
    ap.add_argument('--min-n', type=int, default=150)
    a = ap.parse_args()
    D = FS.load(a.data)
    rows = race_rows(D)
    disc = [r for r in rows if r['date'] < a.split]
    val = [r for r in rows if r['date'] >= a.split]
    b0 = fit_beta(disc)
    base_val = nll(val, b0) / len(val)
    raw_val = nll(val, 1.0) / len(val)
    L = ['# 区分ごとの市場の偏り（3連単オッズ p ∝ q^β）\n']
    L.append(f'- 発見 {len(disc)}R（〜{a.split - 1}）/ 検証 {len(val)}R。全体の β（発見）= {b0:.3f}')
    L.append(f'- 検証の対数損失: 生のオッズ {raw_val:.5f} / 全体の β {base_val:.5f}')
    L.append('- β > 1: 本命が過小評価（本命寄りに買うべき）。β < 1: 穴が過小評価。**検証の改善**がプラスなら偏りが続いている\n')
    for seg in ['ライン構成', '種別', 'グレード', '級班', '頭数', '時間帯', 'バンク']:
        groups = defaultdict(lambda: ([], []))
        for r in disc:
            groups[r[seg]][0].append(r)
        for r in val:
            groups[r[seg]][1].append(r)
        L.append(f'\n## {seg}\n')
        L.append('| 区分 | 発見 R | β（発見） | β（検証で推定・参考） | 検証 R | 検証の改善（全体βとの差×1000） |')
        L.append('|---|---:|---:|---:|---:|---:|')
        tot_gain = 0.0
        for k, (d, v) in sorted(groups.items(), key=lambda kv: -len(kv[1][0])):
            if len(d) < a.min_n:
                continue
            bs = fit_beta(d)
            bv = fit_beta(v) if len(v) >= 50 else float('nan')
            gain = (nll(v, b0) - nll(v, bs)) / len(v) if v else float('nan')
            tot_gain += (nll(v, b0) - nll(v, bs)) if v else 0.0
            L.append(f'| {k} | {len(d)} | {bs:.3f} | {bv:.3f} | {len(v)} | {gain * 1000:+.2f} |')
        L.append(f'\n区分ごとの β を使った場合の検証全体の改善: {tot_gain / len(val) * 1000:+.3f}（×1000）')
    path = os.path.join(HERE, 'out', 'segment_calibration.md')
    open(path, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
