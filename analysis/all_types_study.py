#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全券種（3連単・3連複・2車単・2車複・ワイド）の買い方検証:
  python3 analysis/all_types_study.py --data <収集フォルダ> [--split 20260801] [--start 202604]

方針（後から都合よく選ばないための決まりごと）:
  * モデルは「その月より前のデータだけ」で学習し直して予測する（月ごとのローリング。学習に使った月は評価しない）
  * 融合は v105 と同じ Benter 型 f ∝ q^β · p^α（α・β は現行モデルの値で固定）
  * 買い方は 券種 × 判定指標 × 基準値 × オッズ帯 の格子で全部数え、
    発見期間（--split より前）で選んだものを、検証期間でそのまま確かめる
  * 判定指標:
      EV_f = 融合確率 × その券種の最終オッズ（ワイドは最低倍率）
      EV_q = 3連単オッズから見た確率 × その券種の最終オッズ（3連単自身には使えない）
  * 回収率の90%区間はレース単位のブートストラップ
出力は analysis/out/（公開リポジトリには入れない）。
"""
import argparse
import os
import pickle
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import train_model as T  # noqa: E402
import keirin_engine as E  # noqa: E402
import deep_analysis as DA  # noqa: E402

TYPES = ['3連単', '3連複', '2車単', '2車複', 'ワイド']
BANDS = [(1, 3), (3, 5), (5, 10), (10, 20), (20, 50), (50, 100), (100, 300), (300, 100000)]
THRS = [1.0, 1.1, 1.2, 1.3, 1.5, 2.0]


def band_label(lo, hi):
    return f'{lo}〜{hi}倍' if hi < 100000 else f'{lo}倍〜'


# ------------------------------------------------------------ モデル確率（月ごとローリング）
def oos_model_dists(data_root, start_month, cache):
    if cache and os.path.exists(cache):
        with open(cache, 'rb') as f:
            return pickle.load(f)
    D = T.load_all(data_root)
    T.configure(T.ext_fill_rate(D['cards']) >= 0.9)
    races = T.build_races(D)
    months = sorted({R['date'] // 100 for R in races if R['date'] // 100 >= start_month})
    out = {}
    for m in months:
        t0 = time.time()
        train = [R for R in races if R['date'] // 100 < m]
        test = [R for R in races if R['date'] // 100 == m]
        model = T.fit_model(train)
        for R in test:
            d = T.predict_dist(model, R)
            out[R['race_id']] = {f'{a}-{b}-{c}': p for (a, b, c), p in d.items()}
        print(f'  [model] {m}: 学習 {len(train)} → 予測 {len(test)}（{time.time() - t0:.0f}秒）', flush=True)
    if cache:
        with open(cache, 'wb') as f:
            pickle.dump(out, f)
    return out


# ------------------------------------------------------------ 3連単 → 各券種へ集計
def aggregate(tri):
    """tri: race_id, key(a-b-c), f, q → 各券種の (race_id, bet_type, key, f, q)"""
    parts = tri['key'].str.split('-', expand=True)
    a, b, c = parts[0], parts[1], parts[2]
    ai, bi, ci = a.astype(int), b.astype(int), c.astype(int)
    srt2 = lambda x, y, xi, yi: np.where(xi < yi, x + '-' + y, y + '-' + x)
    s3 = np.sort(np.c_[ai, bi, ci], axis=1).astype(str)
    keys = {
        '3連単': tri['key'].values,
        '3連複': np.char.add(np.char.add(np.char.add(np.char.add(s3[:, 0], '-'), s3[:, 1]), '-'), s3[:, 2]),
        '2車単': (a + '-' + b).values,
        '2車複': srt2(a, b, ai, bi),
    }
    out = []
    for bt, k in keys.items():
        g = pd.DataFrame({'race_id': tri['race_id'].values, 'key': k, 'f': tri['f'].values, 'q': tri['q'].values})
        g = g.groupby(['race_id', 'key'], sort=False)[['f', 'q']].sum().reset_index()
        g['bet_type'] = bt
        out.append(g)
    w = []
    for x, y, xi, yi in ((a, b, ai, bi), (a, c, ai, ci), (b, c, bi, ci)):
        w.append(pd.DataFrame({'race_id': tri['race_id'].values, 'key': srt2(x, y, xi, yi),
                               'f': tri['f'].values, 'q': tri['q'].values}))
    g = pd.concat(w).groupby(['race_id', 'key'], sort=False)[['f', 'q']].sum().reset_index()
    g['bet_type'] = 'ワイド'
    out.append(g)
    return pd.concat(out, ignore_index=True)


def build_tickets(data_root, dists, alpha, beta):
    races, _, pay = DA.build([data_root], 20990101)
    races = races[races['race_id'].isin(dists)]
    odds = DA.read_odds([data_root], set(races['race_id']))
    payt = DA.payoff_table(pay)
    odds = DA.fix_odds(odds, payt)
    o3 = odds[(odds['bet_type'] == '3連単') & (odds['odds'] >= 1.0)][['race_id', 'key', 'odds']]
    o3 = {rid: g.set_index('key')['odds'] for rid, g in o3.groupby('race_id')}
    ok = set(races['race_id'])
    rows = []
    used = 0
    for rid, d in dists.items():
        od = o3.get(rid)
        if od is None or rid not in ok:
            continue
        keys = list(d)
        w = od.reindex(keys).values
        if np.isfinite(w).sum() < 0.6 * len(keys):
            continue
        used += 1
        w = 1.0 / w
        w = np.where(np.isfinite(w), w, np.nanmin(w) * 0.5)
        q = w / w.sum()
        p = np.array([d[k] for k in keys])
        p = p / p.sum()
        u = alpha * np.log(np.maximum(p, 1e-12)) + beta * np.log(np.maximum(q, 1e-12))
        f = np.exp(u - u.max())
        f /= f.sum()
        rows.append(pd.DataFrame({'race_id': rid, 'key': keys, 'f': f, 'q': q}))
    tri = pd.concat(rows, ignore_index=True)
    print(f'[data] 3連単オッズがそろうレース {used}', flush=True)
    t = aggregate(tri)
    own = odds[['race_id', 'bet_type', 'key', 'odds']]
    t = t.merge(own, on=['race_id', 'bet_type', 'key'], how='left')
    t = t.merge(payt, on=['race_id', 'bet_type', 'key'], how='left')
    t['payoff'] = t['payoff'].fillna(0.0)
    t = t.merge(races[['race_id', 'date']], on='race_id', how='left')
    t['month'] = t['date'] // 100
    t['EV_f'] = t['f'] * t['odds']
    t['EV_q'] = np.where(t['bet_type'] == '3連単', np.nan, t['q'] * t['odds'])
    return t[t['odds'].notna()].reset_index(drop=True)


# ------------------------------------------------------------ 集計
def stats(g, n_boot=300):
    if len(g) == 0:
        return dict(n=0, races=0, hits=0, roi=np.nan, lo=np.nan, hi=np.nan)
    s = DA.roi_stats(np.full(len(g), 100.0), g['payoff'].values, g['race_id'].values, n_boot=n_boot)
    return dict(n=s['n'], races=g['race_id'].nunique(), hits=s['hits'], roi=s['roi'], lo=s['lo'], hi=s['hi'])


def grid(t, split):
    out = []
    for bt in TYPES:
        tb = t[t['bet_type'] == bt]
        for sig in ('EV_f', 'EV_q'):
            if bt == '3連単' and sig == 'EV_q':
                continue
            for thr in THRS:
                sel = tb[tb[sig] >= thr]
                for lo, hi in BANDS + [(1, 100000)]:
                    g = sel[(sel['odds'] >= lo) & (sel['odds'] < hi)]
                    if len(g) < 30:
                        continue
                    a = stats(g[g['date'] < split], n_boot=200)
                    b = stats(g[g['date'] >= split], n_boot=200)
                    out.append(dict(券種=bt, 指標=sig, 基準=thr, オッズ帯='全部' if (lo, hi) == (1, 100000) else band_label(lo, hi),
                                    発見_点数=a['n'], 発見_的中=a['hits'], 発見_回収率=a['roi'], 発見_下限=a['lo'],
                                    検証_点数=b['n'], 検証_的中=b['hits'], 検証_回収率=b['roi'], 検証_下限=b['lo'], 検証_上限=b['hi']))
    return pd.DataFrame(out)


def base_curve(t, split):
    """全組を買った場合の、券種 × オッズ帯の回収率（いわゆる本命・大穴の偏り）"""
    out = []
    for bt in TYPES:
        tb = t[t['bet_type'] == bt]
        for lo, hi in BANDS:
            g = tb[(tb['odds'] >= lo) & (tb['odds'] < hi)]
            a, b = g[g['date'] < split], g[g['date'] >= split]
            out.append(dict(券種=bt, オッズ帯=band_label(lo, hi),
                            発見_回収率=a['payoff'].sum() / (100 * max(1, len(a))), 発見_点数=len(a),
                            検証_回収率=b['payoff'].sum() / (100 * max(1, len(b))), 検証_点数=len(b)))
    return pd.DataFrame(out)


def pct(x):
    return '-' if x != x else f'{x * 100:.1f}%'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--split', type=int, default=20260801)
    ap.add_argument('--start', type=int, default=202604, help='この月からローリング予測する')
    ap.add_argument('--out', default=os.path.join(HERE, 'out'))
    ap.add_argument('--min-n', type=int, default=300, help='発見期間でこの点数以上の買い方だけ候補にする')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    model = E.load_model()
    alpha, beta = model['alpha'], model['beta']
    dists = oos_model_dists(a.data, a.start, os.path.join(a.out, f'oos_dist_{a.start}.pkl'))
    t = build_tickets(a.data, dists, alpha, beta)
    t.to_pickle(os.path.join(a.out, 'all_types_tickets.pkl'))
    n_a = t.loc[t['date'] < a.split, 'race_id'].nunique()
    n_b = t.loc[t['date'] >= a.split, 'race_id'].nunique()
    print(f'[data] 発見 {n_a}R / 検証 {n_b}R', flush=True)

    G = grid(t, a.split)
    G.to_csv(os.path.join(a.out, 'all_types_grid.csv'), index=False, encoding='utf-8-sig')
    BC = base_curve(t, a.split)

    L = ['# 全券種の買い方検証（ローリング予測・発見/検証）\n']
    L.append(f'- 対象: {t["date"].min()}〜{t["date"].max()}、発見期間 {n_a}R（〜{a.split - 1}）/ 検証期間 {n_b}R（{a.split}〜）')
    L.append(f'- モデルは各月より前のデータだけで学習（{a.start}〜を予測）。融合 α={alpha} β={beta}（現行値で固定）')
    L.append('- オッズは結果ページの最終オッズ（ワイドは最低倍率）。1点100円。90%区間はレース単位のブートストラップ\n')
    L.append('## 1. 全組を買った場合の回収率（券種 × オッズ帯）\n')
    L.append('| 券種 | ' + ' | '.join(band_label(lo, hi) for lo, hi in BANDS) + ' |')
    L.append('|---|' + '---:|' * len(BANDS))
    for bt in TYPES:
        b = BC[BC['券種'] == bt]
        L.append(f'| {bt} 発見 | ' + ' | '.join(pct(x) for x in b['発見_回収率']) + ' |')
        L.append(f'| {bt} 検証 | ' + ' | '.join(pct(x) for x in b['検証_回収率']) + ' |')

    L.append(f'\n## 2. 発見期間で選んだ買い方（点数{a.min_n}以上・回収率105%以上・90%下限90%以上）→ 検証期間\n')
    cand = G[(G['発見_点数'] >= a.min_n) & (G['発見_回収率'] >= 1.05) & (G['発見_下限'] >= 0.90)]
    cand = cand.sort_values(['券種', '発見_回収率'], ascending=[True, False])
    L.append('| 券種 | 指標 | 基準 | オッズ帯 | 発見 点数 | 発見 回収率（下限） | 検証 点数 | 検証 的中 | **検証 回収率** | 検証 90%区間 |')
    L.append('|---|---|---:|---|---:|---:|---:|---:|---:|---|')
    for _, r in cand.iterrows():
        L.append(f'| {r.券種} | {r.指標} | {r.基準} | {r.オッズ帯} | {r.発見_点数} | {pct(r.発見_回収率)}（{pct(r.発見_下限)}） | '
                 f'{r.検証_点数} | {r.検証_的中} | **{pct(r.検証_回収率)}** | {pct(r.検証_下限)}〜{pct(r.検証_上限)} |')
    if cand.empty:
        L.append('| （該当なし） |||||||||')

    L.append('\n## 3. 券種ごとの「EV 1.10以上・全オッズ帯」（参考: 事前に決めていた基準）\n')
    L.append('| 券種 | 指標 | 発見 点数 | 発見 回収率 | 検証 点数 | 検証 回収率 | 検証 90%区間 |')
    L.append('|---|---|---:|---:|---:|---:|---|')
    ref = G[(G['基準'] == 1.1) & (G['オッズ帯'] == '全部')]
    for _, r in ref.iterrows():
        L.append(f'| {r.券種} | {r.指標} | {r.発見_点数} | {pct(r.発見_回収率)} | {r.検証_点数} | {pct(r.検証_回収率)} | '
                 f'{pct(r.検証_下限)}〜{pct(r.検証_上限)} |')

    # 月別の安定性（候補のみ）
    L.append('\n## 4. 候補の月別回収率\n')
    for _, r in cand.head(15).iterrows():
        lo, hi = (1, 100000) if r.オッズ帯 == '全部' else next(b for b in BANDS if band_label(*b) == r.オッズ帯)
        g = t[(t['bet_type'] == r.券種) & (t[r.指標] >= r.基準) & (t['odds'] >= lo) & (t['odds'] < hi)]
        cells = []
        for m, gm in g.groupby('month'):
            cells.append(f'{m % 100}月 {pct(gm["payoff"].sum() / (100 * len(gm)))}（{len(gm)}）')
        L.append(f'- {r.券種} {r.指標}≥{r.基準} {r.オッズ帯}: ' + ' / '.join(cells))

    path = os.path.join(a.out, 'all_types_report.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print('\n'.join(L))
    print(f'\n[out] {path}')


if __name__ == '__main__':
    main()
