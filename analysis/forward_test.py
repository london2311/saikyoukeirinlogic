#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v105 未来テスト（フォワードテスト）:  python3 analysis/forward_test.py [--since 20261001]

学習・買い方の選定に一度も使っていない期間（既定: 2026/10/01以降）のレースに、
9月までのデータで作った v105 をそのまま当てはめて成績を確認する。
  - モデル係数・融合の重み（Benter型 α・β）・買い方のルールは一切変えない
  - 事前に決めていた買い方: 2車複 × 融合EV1.10以上（1点100円）など
  - 事前に決めていなかった分析（ワイドのEVなど）は「参考」として分けて表示する
"""
import argparse
import math
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import train_model as T  # noqa: E402
import keirin_engine as E  # noqa: E402
from backtest import summarize  # noqa: E402

TYPES = ['3連単', '3連複', '2車単', '2車複', 'ワイド']


def load(since):
    D = T.load_all(ROOT)
    races = D['races']
    races = races[pd.to_numeric(races['date'], errors='coerce') >= since]
    ids = set(races['race_id'])
    D = {k: (v[v['race_id'].isin(ids)] if 'race_id' in v else v) for k, v in D.items()}
    D['races'] = races
    return D


def pct(x):
    return '-' if x != x else f'{x * 100:.1f}%'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', type=int, default=20261001)
    args = ap.parse_args()

    model = E.load_model()  # 拡張特徴量の設定もモデルに合わせる
    print(f'[model] {model["json"]["version"]} 学習 {model["json"]["trained_on"]} / α={model["alpha"]} β={model["beta"]}')
    D = load(args.since)
    races = T.build_races(D)
    odds = D['odds']
    if 'absent' in odds:
        odds = odds[pd.to_numeric(odds['absent'], errors='coerce').fillna(0) == 0]
    oi = defaultdict(lambda: defaultdict(dict))
    for rid, bt, cmb, od in zip(odds['race_id'], odds['bet_type'], odds['combination'], pd.to_numeric(odds['odds'], errors='coerce')):
        if bt in TYPES and od == od and od >= 1.0:
            oi[rid][bt][T.norm_combo(bt, cmb)] = float(od)
    for R in races:  # 的中組は払戻/100（=確定オッズ）。旧形式の払戻混入にも対応
        for bt in TYPES:
            if bt == 'ワイド':
                continue
            for cmb, pay, _ in R['payoffs'].get(bt, []):
                oi[R['race_id']][bt][T.norm_combo(bt, cmb)] = pay / 100.0
    dates = sorted({R['date'] for R in races})
    print(f'[data] {dates[0]}〜{dates[-1]}: 結果のあるレース {len(races)}')

    rows = []   # 券ごと
    ll = defaultdict(list)
    used = 0
    for R in races:
        o3 = oi[R['race_id']]['3連単']
        d = T.predict_dist(model, R)
        keys = [f'{a}-{b}-{c}' for (a, b, c) in d]
        if sum(k in o3 for k in keys) < 0.6 * len(keys):
            continue
        used += 1
        p = np.array([d[tuple(map(int, k.split('-')))] for k in keys])
        p /= p.sum()
        w = np.array([1.0 / o3[k] if k in o3 else np.nan for k in keys])
        w = np.where(np.isnan(w), np.nanmin(w) * 0.5, w)
        q = w / w.sum()
        u = model['alpha'] * np.log(np.maximum(p, 1e-12)) + model['beta'] * np.log(np.maximum(q, 1e-12))
        f = np.exp(u - u.max())
        f /= f.sum()
        win = keys.index('-'.join(map(str, R['top3'])))
        n = len(R['riders'])
        ll['一様'].append(-math.log(n * (n - 1) * (n - 2)))
        ll['モデル単独'].append(math.log(max(p[win], 1e-12)))
        ll['市場（オッズ）'].append(math.log(max(q[win], 1e-12)))
        ll['融合（v105）'].append(math.log(max(f[win], 1e-12)))
        acc = {t: defaultdict(lambda: [0.0, 0.0]) for t in TYPES}
        for k, pf, pq in zip(keys, f, q):
            a, b, c = k.split('-')
            tk = {'3連単': [k], '3連複': [T.norm_combo('3連複', k)], '2車単': [f'{a}-{b}'],
                  '2車複': [T.norm_combo('2車複', f'{a}-{b}')],
                  'ワイド': [T.norm_combo('ワイド', f'{x}-{y}') for x, y in ((a, b), (a, c), (b, c))]}
            for t, ks in tk.items():
                for kk in ks:
                    acc[t][kk][0] += pf
                    acc[t][kk][1] += pq
        pays = {t: {T.norm_combo(t, cmb): pay for cmb, pay, _ in R['payoffs'].get(t, [])} for t in TYPES}
        for t in TYPES:
            for kk, (pf, pq) in acc[t].items():
                rows.append((R['race_id'], R['date'], R['date'] // 100, t, kk, pf, pq, oi[R['race_id']][t].get(kk), pays[t].get(kk, 0.0)))
    df = pd.DataFrame(rows, columns=['race_id', 'date', 'month', 'bt', 'key', 'prob', 'prob_mkt', 'odds', 'payoff'])
    df['ev'] = df['prob'] * df['odds']
    df['ev_mkt'] = df['prob_mkt'] * df['odds']
    months = sorted(df['month'].unique())

    def bets(sub):
        return list(zip(sub['race_id'], sub['date'], [100] * len(sub), sub['payoff']))

    def line(name, sub, extra=''):
        s = summarize(bets(sub))
        per_m = ' / '.join(f'{m % 100}月 {pct(summarize(bets(sub[sub["month"] == m]))["roi"])}（{(sub["month"] == m).sum()}点）' for m in months)
        lo, hi = s['ci']
        return (f'| {name} | {s["bets"]} | {s["hits"]} | {pct(s["hit_rate"])} | **{pct(s["roi"])}** | {pct(lo)}〜{pct(hi)} | '
                f'{int(s["ret"] - s["stake"]):+,}円 | {s["max_losing"]} | {per_m}{extra} |')

    L = ['# v105 未来テスト（フォワードテスト）レポート\n']
    L.append(f'- 対象: {dates[0]}〜{dates[-1]}（v105 の学習・買い方の選定に一度も使っていない期間）')
    L.append(f'- レース数: 結果あり {len(races)} / うち3連単オッズがそろい判定できたもの **{used}**')
    L.append(f'- モデル: {model["json"]["version"]}（{model["json"]["trained_on"]["date_min"]}〜{model["json"]["trained_on"]["date_max"]} の'
             f' {model["json"]["trained_on"]["races"]}レースで学習）、融合 α={model["alpha"]} β={model["beta"]}。**一切変更なし**')
    L.append('- オッズは結果ページの最終オッズ。1点100円で計算。\n')

    L.append('## 1. 予測精度（3連単の実際の着順に付けた確率の対数損失。小さいほど良い）\n')
    L.append('| 予測 | 未来テスト | 参考: 検証期間7〜9月 |')
    L.append('|---|---:|---:|')
    ref = {'一様': 5.3779, 'モデル単独': 4.2542, '市場（オッズ）': 3.9916, '融合（v105）': 3.9892}
    for k, v in ll.items():
        L.append(f'| {k} | {-np.mean(v):.4f} | {ref[k]:.4f} |')

    hdr = '| 買い方 | 点数 | 的中 | 的中率 | 回収率 | 90%区間 | 収支 | 最大連敗(R) | 月別 |\n|---|---:|---:|---:|---:|---|---:|---:|---|'
    q2 = df[(df['bt'] == '2車複') & df['odds'].notna()]
    L.append('\n## 2. 回収率重視（事前に決めていた買い方）\n')
    L.append('参考: 9月までの検証では「2車複 × 融合EV1.10以上（全点）」が 選定期間 108.7% → 検証期間 154.6%。\n')
    L.append(hdr)
    L.append(line('**2車複 × 融合EV1.10以上（全点）**', q2[q2['ev'] >= 1.10]))
    top1 = q2[q2['ev'] >= 1.10].sort_values('ev', ascending=False).drop_duplicates('race_id')
    L.append(line('2車複 × 融合EV1.10以上（EV最大1点/R）', top1))
    L.append(line('2車複 × 融合EV1.10以上 × 30〜100倍', q2[(q2['ev'] >= 1.10) & (q2['odds'] >= 30) & (q2['odds'] < 100)]))
    L.append(line('（比較）市場のみ: 2車複 × EV1.10以上', q2[q2['ev_mkt'] >= 1.10]))

    L.append('\n### 3連単から見た確率 ÷ 2車複オッズの確率 で層別（構造が続いているか）\n')
    L.append('| 比 | 点数 | 実的中率 | 3連単からの確率 | 回収率 | 参考: 4〜9月の回収率 |')
    L.append('|---|---:|---:|---:|---:|---:|')
    q2 = q2.assign(ratio=q2['prob_mkt'] / (0.75 / q2['odds']))
    ref_r = ['37.8%', '69.3%', '83.5%', '93.1%', '130.4%', '0%（46点）']
    for (lo, hi), rr in zip([(0, 0.8), (0.8, 1.0), (1.0, 1.2), (1.2, 1.47), (1.47, 2.0), (2.0, 100)], ref_r):
        g = q2[(q2['ratio'] > lo) & (q2['ratio'] <= hi)]
        if len(g):
            L.append(f'| {lo}〜{hi if hi < 100 else ""} | {len(g)} | {pct((g["payoff"] > 0).mean())} | {pct(g["prob_mkt"].mean())} | '
                     f'**{pct(g["payoff"].sum() / (100 * len(g)))}** | {rr} |')

    L.append('\n## 3. 的中率重視（事前に決めていた条件。各レースで当たる確率が最も高い1点）\n')
    L.append(hdr)
    for bt, thr, ref_s in (('ワイド', 0.79, '89.4% / 90.8%'), ('2車複', 0.62, '76.0% / 90.2%'), ('3連複', 0.35, '49.3% / 88.1%'),
                           ('ワイド', 0.0, '56.0% / 77.8%'), ('2車複', 0.0, '38.1% / 78.5%')):
        top = df[df['bt'] == bt].sort_values('prob', ascending=False).drop_duplicates('race_id')
        g = top[top['prob'] >= thr]
        name = f'{bt} 本命1点（確率{int(thr * 100)}%以上）' if thr else f'{bt} 本命1点（全レース）'
        L.append(line(name, g, f' ／ 7〜9月: {ref_s}'))

    L.append('\n## 4. 参考（事前に決めていなかった分析。ここで良く見えても次の期間で再確認が必要）\n')
    w = df[(df['bt'] == 'ワイド') & df['odds'].notna()]
    L.append(hdr)
    L.append(line('ワイド × 融合EV1.10以上（最低倍率で計算）', w[w['ev'] >= 1.10]))
    for bt in ('2車単', '3連複'):
        g = df[(df['bt'] == bt) & df['odds'].notna()]
        L.append(line(f'{bt} × 融合EV1.10以上', g[g['ev'] >= 1.10]))

    out = os.path.join(HERE, 'forward_test_report.md')
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
