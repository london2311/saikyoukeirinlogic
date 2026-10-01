#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
収集データの「そろい具合」チェック:  python3 analysis/check_data.py

融合重みαの推定や融合EV戦略の検証には、同じレースについて
「出走表・着順・払戻・3連単オッズ」が全部そろっている必要がある。
日付ごとに、そろったレース数・3連単オッズのカバー率・オッズ形式の正しさを表示する。
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd  # noqa: E402
import train_model as T  # noqa: E402


def main():
    D = T.load_all(T.ROOT)
    races = D.get('races')
    if races is None:
        sys.exit('races（レース情報）のCSVが見つかりません')
    date_of = dict(zip(races['race_id'], races['date'].astype(str)))
    n_ent = dict(zip(races['race_id'], races['entries_number']))

    has = defaultdict(set)
    if 'cards' in D:
        has['出走表'] = set(D['cards']['race_id'])
    if 'results' in D:
        r = D['results']
        top = r[r['order'].between(1, 3)].groupby('race_id')['order'].nunique()
        has['着順'] = set(top[top == 3].index)
    if 'payoffs' in D:
        has['払戻'] = set(D['payoffs']['race_id'])

    cover = {}
    old_format = 0
    if 'odds' in D:
        o = D['odds']
        o3 = o[(o['bet_type'] == '3連単') & o['odds'].notna()]
        cnt = o3.groupby('race_id').size()
        for rid, c in cnt.items():
            n = int(n_ent.get(rid, 0) or 0)
            exp = n * (n - 1) * (n - 2) if n >= 3 else 0
            cover[rid] = c / exp if exp else 0.0
        has['3連単オッズ'] = {rid for rid, c in cover.items() if c >= 0.6}
        # 旧形式の検出: 的中組の odds 列に払戻金額（例 10350）が入っている
        if 'payoffs' in D:
            p3 = D['payoffs'][D['payoffs']['bet_type'] == '3連単'][['race_id', 'combination', 'payoff']]
            m = o3.merge(p3, on=['race_id', 'combination'], suffixes=('', '_p'))
            if len(m):
                payoff_col = 'payoff_p' if 'payoff_p' in m.columns else 'payoff'
                old_format = int(((m['odds'] - m[payoff_col]).abs() < 1e-6).sum())
                ratio = (m['odds'] * 100 / m[payoff_col]).replace([float('inf')], pd.NA).dropna()
                print(f'[形式] 的中3連単 {len(m)}件: 倍率×100÷払戻 の中央値 {ratio.median():.3f}（1.0なら正常）'
                      + (f' / 旧形式（odds列=払戻金額）{old_format}件 → 学習時に自動補正' if old_format else ''))

    kinds = ['出走表', '着順', '払戻', '3連単オッズ']
    complete = set(date_of)
    for k in kinds:
        complete &= has.get(k, set())

    by_date = defaultdict(lambda: defaultdict(int))
    for rid, d in date_of.items():
        by_date[d]['レース'] += 1
        for k in kinds:
            by_date[d][k] += rid in has.get(k, set())
        by_date[d]['全部そろい'] += rid in complete

    cols = ['レース'] + kinds + ['全部そろい']
    print('\n| 日付 | ' + ' | '.join(cols) + ' |')
    print('|---|' + '---:|' * len(cols))
    for d in sorted(by_date):
        print(f'| {d} | ' + ' | '.join(str(by_date[d][c]) for c in cols) + ' |')
    print(f'\n全部そろったレース: {len(complete)} / {len(date_of)}')
    if len(complete) < 30:
        print('→ 融合重みαの自動推定には30レース以上必要です。収集を続けてください。')
    else:
        print('→ python3 analysis/train_model.py で融合方式とαが自動推定されます。')

    if 'cards' in D:
        c = D['cards']
        extra = ['standing_count', 'back_count', 'first_count', 'out_count']
        rates = {k: (pd.to_numeric(c[k], errors='coerce').notna().mean() if k in c else 0.0) for k in extra}
        print('\n[出走表の拡張列 充足率] ' + ' / '.join(f'{k} {v * 100:.0f}%' for k, v in rates.items()))
        if max(rates.values()) == 0:
            print('  ※ S/B・着順回数が空です。最新の keirin_collector.py で再収集すると埋まります'
                  '（キー名が違う場合は DEBUG_RECORD_KEYS=1 で確認）。')


if __name__ == '__main__':
    main()
