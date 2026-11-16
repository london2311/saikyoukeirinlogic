#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
組み合わせ買い（流し・フォーメーション・BOX）の検証:  python3 analysis/formation_study.py [--split 20260801]

all_types_study.py が作った analysis/out/all_types_tickets.pkl（各月より前だけで学習したモデル × 最終オッズ）を使う。
事前に決めた選び方:
  各レース・各券種で、的中率 P が下限以上の買い目群のうち、期待値（合成オッズ×的中率）が最大のものを1つ選び、
  その期待値が 1.10 以上なら、合計1000円を合成オッズの配分（100円単位）で買う。
  的中率の下限は 5% / 15% / 30% の3通り。比較として「1点だけ（EV最大）」も同じ条件で数える。
出力は analysis/out/formation_report.md（公開リポジトリには入れない）。
"""
import argparse
import os
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import formations as F  # noqa: E402
import deep_analysis as DA  # noqa: E402

P_MINS = [0.05, 0.15, 0.30]
TYPES = ['3連単', '3連複', '2車単', '2車複', 'ワイド']


def load_lines(roots):
    """出走表から各レースの並び（先頭から）。ライン決着の候補に使う"""
    import glob
    frames = []
    for r in roots:
        for p in glob.glob(os.path.join(r, 'data', '20??', 'racecards.csv')):
            frames.append(pd.read_csv(p, dtype=str, usecols=lambda c: c in ('race_id', 'car_number', 'line_order', 'line_position', 'is_single', 'absent')))
    c = pd.concat(frames).drop_duplicates(['race_id', 'car_number'], keep='last')
    c = c[pd.to_numeric(c['absent'], errors='coerce').fillna(0) == 0]
    c = c[pd.to_numeric(c['is_single'], errors='coerce').fillna(1) == 0]
    c['pos'] = pd.to_numeric(c['line_position'], errors='coerce')
    c['car'] = pd.to_numeric(c['car_number'], errors='coerce').astype('Int64')
    out = {}
    for (rid, lo), g in c.dropna(subset=['pos', 'car']).groupby(['race_id', 'line_order']):
        out.setdefault(rid, []).append([int(x) for x in g.sort_values('pos')['car']])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--split', type=int, default=20260801)
    ap.add_argument('--tickets', default=os.path.join(HERE, 'out', 'all_types_tickets.pkl'))
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    t = pd.read_pickle(a.tickets)
    lines = load_lines(['/home/user/datebase', os.path.dirname(HERE)])
    t0 = time.time()
    rows = []  # (race, date, type, rule, n, P, ev, stake, ret)
    races = t.groupby('race_id', sort=False)
    for i, (rid, g) in enumerate(races):
        if a.limit and i >= a.limit:
            break
        date = int(g['date'].iloc[0])
        probs, odds, pays = defaultdict(dict), defaultdict(dict), defaultdict(dict)
        for bt, k, f, od, pay in zip(g['bet_type'], g['key'], g['f'], g['odds'], g['payoff']):
            probs[bt][k] = f
            if od == od:
                odds[bt][k] = float(od)
            if pay:
                pays[bt][k] = pay
        if '3連単' not in odds:
            continue
        dist3 = {tuple(int(x) for x in k.split('-')): p for k, p in probs['3連単'].items()}
        groups = F.build_groups(probs, odds, lines.get(rid), dist3)
        combos = F.combine(groups, probs, odds, dist3)
        for bt in TYPES:
            gb = [x for x in groups if x['type'] == bt]
            if not gb:
                continue
            for pm in P_MINS:
                base = [x for x in gb if x['p'] >= pm]
                pruned = [y for y in (F.prune(x, probs, odds, pm, dist3) for x in base) if y]
                comb = [x for x in combos if x['type'] == bt and x['p'] >= pm]
                for kind, cand in (('買い目群', base), ('買い目群＋切り', base + pruned), ('買い目群＋組み合わせ', base + comb),
                                   ('1点', [x for x in gb if x['p'] >= pm and x['n'] == 1])):
                    if not cand:
                        continue
                    best = max(cand, key=lambda x: (x['ev'], -x['n']))
                    if best['ev'] < 1.10:
                        continue
                    ev_r, st = F.realized_ev(best, probs, odds)
                    ret = sum(s * pays[bt].get(k, 0.0) / 100.0 for s, k in zip(st, best['keys']))
                    rows.append((rid, date, bt, f'{kind} P≥{int(pm * 100)}%', best['n'], best['p'], best['ev'], sum(st), ret))
        if i % 1000 == 0:
            print(f'  {i}R {time.time() - t0:.0f}秒', flush=True)
    df = pd.DataFrame(rows, columns=['race_id', 'date', 'bt', 'rule', 'n', 'P', 'ev', 'stake', 'ret'])
    df.to_pickle(os.path.join(HERE, 'out', 'formation_bets.pkl'))
    L = ['# 組み合わせ買い（合成オッズ）の検証\n',
         f'- 発見期間（〜{a.split - 1}）/ 検証期間（{a.split}〜）。各月より前だけで学習したモデル × 最終オッズ。',
         '- 各レース・各券種で「的中率の下限を満たし、期待値が最大の買い目群」を1つ選び、期待値1.10以上なら合計1000円を合成オッズの配分で購入\n',
         '| 券種 | 選び方 | 期間 | レース数 | 平均点数 | 平均的中率(予想) | 実際の的中率 | 回収率 | 90%区間 |',
         '|---|---|---|---:|---:|---:|---:|---:|---|']
    for bt in TYPES:
        for rule in sorted(df.loc[df.bt == bt, 'rule'].unique(), key=lambda r: ({'1点': 0, '買い目群': 1, '買い目群＋切り': 2}.get(r.split()[0], 3), r)):
            for per, sub in (('発見', df[(df.bt == bt) & (df.rule == rule) & (df.date < a.split)]),
                             ('検証', df[(df.bt == bt) & (df.rule == rule) & (df.date >= a.split)])):
                if len(sub) == 0:
                    continue
                s = DA.roi_stats(sub['stake'].values, sub['ret'].values, sub['race_id'].values, n_boot=300)
                L.append(f'| {bt} | {rule} | {per} | {len(sub)} | {sub.n.mean():.1f} | {sub.P.mean() * 100:.0f}% | '
                         f'{(sub.ret > 0).mean() * 100:.0f}% | **{s["roi"] * 100:.1f}%** | {s["lo"] * 100:.0f}〜{s["hi"] * 100:.0f}% |')
    out = os.path.join(HERE, 'out', 'formation_report.md')
    open(out, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
