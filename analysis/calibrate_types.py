#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
券種ごとの第2段の較正（Benter 型）:  python3 analysis/calibrate_types.py [--split 20260801]

all_types_study.py が作った analysis/out/all_types_tickets.pkl を使う。
3連単オッズ由来の融合確率 f は、大穴に票が集まる 3連単の偏りをそのまま引き継ぐため、
2車単・3連複などに集計すると大穴の組を高く見すぎる。そこで券種ごとに
    p ∝ f^a · q_own^b      （q_own = その券種自身のオッズの逆数を正規化）
を発見期間のレースだけで最尤推定し（1レース1的中の券種はレース内ソフトマックス）、
検証期間で EV = p × オッズ の買い方をそのまま確かめる。ワイドは的中が3組あるため対象外（データ不足）。
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import deep_analysis as DA  # noqa: E402

TYPES = ['3連単', '3連複', '2車単', '2車複']
BANDS = [(1, 5), (5, 10), (10, 20), (20, 50), (50, 100), (100, 300), (1, 300)]
THRS = [1.0, 1.05, 1.1, 1.2, 1.3]


def prep(tb):
    tb = tb.copy()
    w = 1.0 / tb['odds']
    tb['q_own'] = w / w.groupby(tb['race_id']).transform('sum')
    tb['lf'] = np.log(np.maximum(tb['f'], 1e-12))
    tb['lq'] = np.log(np.maximum(tb['q_own'], 1e-12))
    tb['hit'] = (tb['payoff'] > 0).astype(float)
    # 的中組がオッズ表にあるレースだけ（1レース1的中）
    ok = tb.groupby('race_id')['hit'].transform('sum') == 1
    return tb[ok]


def fit(tb):
    codes, uniq = pd.factorize(tb['race_id'])
    X = tb[['lf', 'lq']].values
    y = tb['hit'].values

    def nll(w):
        u = X @ w
        m = np.full(len(uniq), -np.inf)
        np.maximum.at(m, codes, u)
        e = np.exp(u - m[codes])
        s = np.zeros(len(uniq))
        np.add.at(s, codes, e)
        lp = u - m[codes] - np.log(s[codes])
        return -(lp * y).sum()
    r = minimize(nll, np.array([1.0, 0.0]), method='Nelder-Mead', options=dict(xatol=1e-4, fatol=1e-3, maxiter=400))
    return r.x, r.fun / len(uniq)


def apply(tb, w):
    u = tb[['lf', 'lq']].values @ w
    u = pd.Series(u, index=tb.index)
    e = np.exp(u - u.groupby(tb['race_id']).transform('max'))
    return e / e.groupby(tb['race_id']).transform('sum')


def pct(x):
    return '-' if x != x else f'{x * 100:.1f}%'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--split', type=int, default=20260801)
    ap.add_argument('--tickets', default=os.path.join(HERE, 'out', 'all_types_tickets.pkl'))
    a = ap.parse_args()
    t = pd.read_pickle(a.tickets)
    L = ['# 券種ごとの第2段の較正 p ∝ f^a · q_own^b（発見期間で推定 → 検証期間で確認）\n']
    params = {}
    rows = []
    for bt in TYPES:
        tb = prep(t[t['bet_type'] == bt])
        disc = tb[tb['date'] < a.split]
        w, ll = fit(disc)
        params[bt] = dict(a=float(w[0]), b=float(w[1]))
        tb = tb.assign(p_c=apply(tb, w))
        tb['EV_c'] = tb['p_c'] * tb['odds']
        # 対数損失の比較（検証期間・的中組）
        val = tb[tb['date'] >= a.split]
        h = val[val['hit'] == 1]
        lls = {k: -np.log(np.maximum(h[c], 1e-12)).mean() for k, c in (('融合f', 'f'), ('自券種オッズ', 'q_own'), ('較正後', 'p_c'))}
        # f は券種に集計した確率（合計1）
        L.append(f'- **{bt}**: a={w[0]:.3f}, b={w[1]:.3f}（発見 {disc["race_id"].nunique()}R）／ 検証の対数損失 ' +
                 ' / '.join(f'{k} {v:.4f}' for k, v in lls.items()))
        for thr in THRS:
            for lo, hi in BANDS:
                g = tb[(tb['EV_c'] >= thr) & (tb['odds'] >= lo) & (tb['odds'] < hi)]
                if len(g) < 20:
                    continue
                d, v = g[g['date'] < a.split], g[g['date'] >= a.split]
                sd = DA.roi_stats(np.full(len(d), 100.0), d['payoff'].values, d['race_id'].values, n_boot=200) if len(d) else None
                sv = DA.roi_stats(np.full(len(v), 100.0), v['payoff'].values, v['race_id'].values, n_boot=200) if len(v) else None
                rows.append(dict(券種=bt, 基準=thr, オッズ帯=f'{lo}〜{hi}倍',
                                 発見_点数=len(d), 発見_回収率=sd['roi'] if sd else np.nan, 発見_下限=sd['lo'] if sd else np.nan,
                                 検証_点数=len(v), 検証_的中=sv['hits'] if sv else 0, 検証_回収率=sv['roi'] if sv else np.nan,
                                 検証_下限=sv['lo'] if sv else np.nan, 検証_上限=sv['hi'] if sv else np.nan))
    G = pd.DataFrame(rows)
    G.to_csv(os.path.join(HERE, 'out', 'calibrated_grid.csv'), index=False, encoding='utf-8-sig')
    json.dump(params, open(os.path.join(HERE, 'out', 'calibration_params.json'), 'w'), ensure_ascii=False, indent=1)
    L.append('\n| 券種 | 基準 | オッズ帯 | 発見 点数 | 発見 回収率（下限） | 検証 点数 | 検証 的中 | 検証 回収率 | 検証 90%区間 |')
    L.append('|---|---:|---|---:|---:|---:|---:|---:|---|')
    for _, r in G.iterrows():
        L.append(f'| {r.券種} | {r.基準} | {r.オッズ帯} | {r.発見_点数} | {pct(r.発見_回収率)}（{pct(r.発見_下限)}） | {r.検証_点数} | '
                 f'{r.検証_的中} | {pct(r.検証_回収率)} | {pct(r.検証_下限)}〜{pct(r.検証_上限)} |')
    out = os.path.join(HERE, 'out', 'calibrated_report.md')
    open(out, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
