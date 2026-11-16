#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ガールズの「1番人気の番手を狙って2着を拾う」傾向と、選手ごとの傾向を調べる:
    python3 analysis/girls_study.py --data <データ用リポジトリの data/2026>

入力: analysis/out/all_types_tickets.pkl（all_types_study.py が作る。各月より前だけで学習したモデル × 3連単オッズの融合 f）
      出走表・着順・開催（--data）
出力: analysis/out/girls_study.md（公開リポジトリには入れない）

  1. 1番人気が1着のレースで、他の各選手が2着に来た回数を、融合 f の「1番人気が1着の時の2着の確率」と比べる
     （級別 × 得点順位・脚質・コメント・差し/マーク率・2着率）
  2. 補正 P(2着=j | 1番人気が1着) ∝ f · exp(θ·x_j) を前の期間で推定し、後の期間の対数損失で確かめる
  3. 選手ごとの残差（実際 − f の予想）を、その日より前だけで縮小平均し、その後の残差を予測できるか（持続性）
"""
import argparse
import os
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.optimize import minimize

HERE = os.path.dirname(os.path.abspath(__file__))
FE = ['r2', 'r4', 'r5', 'nige']


def marginals(tickets):
    t = tickets[tickets.bet_type == '3連単'][['race_id', 'key', 'f', 'q', 'date']]
    k = t['key'].str.split('-', expand=True).astype(int)
    t = t.assign(a=k[0], b=k[1], c=k[2])
    rows = []
    for rid, g in t.groupby('race_id', sort=False):
        a, b, f, q = g.a.values, g.b.values, g.f.values / g.f.values.sum(), g.q.values / g.q.values.sum()
        cars = np.unique(np.r_[a, b, g.c.values])
        Q1 = {x: q[a == x].sum() for x in cars}
        fav = max(Q1, key=Q1.get)
        m = a == fav
        pf = f[m].sum()
        for x in cars:
            rows.append((rid, int(g.date.iloc[0]), x, f[a == x].sum(), f[b == x].sum(), f[g.c.values == x].sum(), int(x == fav),
                         f[m & (b == x)].sum() / pf if x != fav else np.nan))
    return pd.DataFrame(rows, columns=['race_id', 'date', 'car', 'P1', 'P2', 'P3', 'fav', 'c2'])


def join(d, data):
    res = pd.read_csv(os.path.join(data, 'results.csv'), dtype={'race_id': str})
    res = res.assign(car=pd.to_numeric(res.car_number, errors='coerce'), order=pd.to_numeric(res.order, errors='coerce'))
    c = pd.read_csv(os.path.join(data, 'racecards.csv'), dtype={'race_id': str, 'player_id': str})
    c['car'] = pd.to_numeric(c.car_number, errors='coerce')
    r = pd.read_csv(os.path.join(data, 'races.csv'), dtype={'race_id': str})[['race_id', 'class']]
    x = d.merge(res[['race_id', 'car', 'order']], on=['race_id', 'car'], how='left').merge(
        c[['race_id', 'car', 'player_id', 'style', 'race_point', 'comment']], on=['race_id', 'car'], how='left').merge(r, on='race_id')
    x = x[x.race_id.isin(x[x.order.notna()].race_id)].copy()
    for i in (1, 2, 3):
        x[f'y{i}'] = (x.order == i) * 1.0
    x['ptrank'] = pd.to_numeric(x.race_point, errors='coerce').groupby(x.race_id).rank(ascending=False, method='first')
    x['favwin'] = x.race_id.isin(set(x[(x.fav == 1) & (x.y1 == 1)].race_id))
    x['r2'], x['r4'], x['r5'] = (x.ptrank == 2) * 1.0, (x.ptrank == 4) * 1.0, (x.ptrank >= 5) * 1.0
    x['nige'] = (x['style'] == '逃') * 1.0
    return x


def table(G, col):
    g = G.groupby(col).agg(n=('y2', 'size'), act=('y2', 'sum'), exp=('c2', 'sum'))
    g['ratio'] = g.act / g.exp
    g['z'] = (g.act - g.exp) / np.sqrt(g.exp * (1 - g.exp / g.n))
    return g


def cl_sets(df):
    out = []
    for _, g in df.groupby('race_id'):
        if g.y2.sum() == 1:
            out.append((np.log(np.maximum(g.c2.values, 1e-12)), g[FE].values, int(np.argmax(g.y2.values))))
    return out


def nll(th, R):
    s = 0.0
    for lc, X, i in R:
        u = lc + X @ th
        m = u.max()
        s -= u[i] - m - np.log(np.exp(u - m).sum())
    return s / max(len(R), 1)


def persistence(x, start=20260701, k=20):
    x = x.dropna(subset=['player_id']).sort_values(['date', 'race_id']).reset_index(drop=True)
    x['yT'], x['PT'] = x.y1 + x.y2 + x.y3, x.P1 + x.P2 + x.P3
    E = np.c_[x.y1 - x.P1, x.y2 - x.P2, x.yT - x.PT]
    acc, pend, cur, H = defaultdict(lambda: np.zeros(4)), [], None, np.zeros((len(x), 4))
    for i, (pid, dt) in enumerate(zip(x.player_id.values, x.date.values)):
        if dt != cur:
            for p, v in pend:
                acc[p] += v
            pend, cur = [], dt
        H[i] = acc[pid]
        pend.append((pid, np.r_[E[i], 1.0]))
    out = []
    F = (x.date.values >= start) & (H[:, 3] >= 8)
    for j, name in enumerate(['1着', '2着', '3着内']):
        s = H[F, j] / (H[F, 3] + k)
        out.append((name, int(F.sum()), float(np.corrcoef(s, E[F, j])[0, 1])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--tickets', default=os.path.join(HERE, 'out', 'all_types_tickets.pkl'))
    ap.add_argument('--cut', type=int, default=20260801)
    a = ap.parse_args()
    x = join(marginals(pd.read_pickle(a.tickets)), a.data)
    L = ['# ガールズの番手狙い・選手ごとの傾向\n']
    for cls in ('L級', 'A級', 'S級'):
        G = x[(x['class'] == cls) & x.favwin & (x.fav == 0)].copy()
        G['得点順位'] = G.ptrank.clip(upper=5).astype(int)
        L.append(f'## {cls}: 1番人気が1着の {G.race_id.nunique()}R、その時の2着（実際 ÷ 融合 f の予想）\n')
        for col in ('得点順位', 'style'):
            L.append('```\n' + table(G, col).round(3).to_string() + '\n```\n')
        tr, te = cl_sets(G[G.date < a.cut]), cl_sets(G[G.date >= a.cut])
        th = minimize(nll, np.zeros(len(FE)), args=(tr,), method='BFGS').x
        L.append(f'- 補正 θ（{a.cut}より前の {len(tr)}R で推定）: ' + ', '.join(f'{k}={v:+.3f}' for k, v in zip(FE, th)))
        L.append(f'- 検証（{a.cut}以降の {len(te)}R）の対数損失の改善: **{nll(np.zeros(len(FE)), te) - nll(th, te):+.4f}**/R\n')
    L.append('## 選手ごとの残差（実際 − 融合 f）の持続性（前の残差の縮小平均 と その後の残差の相関）\n')
    for name, n, r in persistence(x):
        L.append(f'- {name}: 相関 {r:+.4f}（{n}出走）')
    path = os.path.join(HERE, 'out', 'girls_study.md')
    open(path, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
