#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analysis/odds_drift.py と期待値ボードの「確定予想オッズ」のテスト（架空データ）:  python tests/drift_test.py"""
import csv
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'analysis'))
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import odds_drift as D  # noqa: E402


def check(c, msg):
    if not c:
        raise SystemExit(f'NG   {msg}')
    print(f'OK   {msg}')


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main():
    data = Path(tempfile.mkdtemp())
    snap, fin = [], []
    for r in range(40):
        rid = f'{r % 12 + 1:02d}2320991231'
        for k, (a, b) in enumerate([(1, 2), (1, 3), (2, 3)]):
            o = 5.0 + k
            snap.append(dict(race_id=rid, snap='C-2', bet_type='2車複', combination=f'{a}-{b}', odds=o, absent=0))
            fin.append(dict(race_id=rid, bet_type='2車複', combination=f'{a}-{b}', odds=round(o * 0.8, 1), payoff='', absent=0))
    write(data / '2099' / 'odds_snap' / 'snap_20991231.csv', snap)
    write(data / '2099' / 'odds' / 'odds_20991231.csv', fin)
    logs = D.collect(data)
    table = D.summarize(logs)
    t = table['2車複']['C-2']
    check(abs(t['|']['ratio'] - 0.8) < 0.02 and t['|']['down'] == 1.0, f'締切前→確定の比（2割下落）を学習 {t["|"]}')
    check(abs(D.ratio(table, '2車複', 'C-2', 6.0) - 0.8) < 0.02, '同じ券種・時点・オッズ帯の比を使う')
    check(D.ratio(table, '3連単', 'C-2', 50.0) == 1.0 and D.ratio({}, '2車複', 'C-2', 6.0) == 1.0, '学習データが無ければ比は 1.0（今のオッズのまま）')
    check(D.ratio(table, '2車複', 'C-2', 150.0) == t['|']['ratio'], 'オッズ帯にデータが無ければ券種×時点の値')

    # 期待値ボード: 確定予想オッズで期待値を出し、その順に並べる
    import keirin_collector as C
    import keirin_engine as E
    import netkeirin_site as S
    import now_test as NT
    _, _, full, _ = NT.build_states()
    cards, odds = C.parse_racecard(full), C.parse_odds(full)
    out = tempfile.mkdtemp()
    drift = {'2車複': {'C-2': {'|': dict(ratio=0.5, n=100, down=1.0)}}}
    sb = S.SiteBuilder(E.load_model(), 'correct horse battery staple', out, '20991231', cards_fn=lambda nk: cards, drift=drift)
    st = datetime(2099, 12, 31, 15, 0, tzinfo=timezone(timedelta(hours=9)))
    race = sb.on_snapshot('209912312305', 'C-2', st, odds, st - timedelta(minutes=7))
    rows = race['snaps']['C-2']['types']['2車複']
    k, p, od, ev, line, of, evf = rows[0]
    check(abs(of - round(od * 0.5, 1)) < 1e-9 and abs(evf - round(p * of, 3)) < 1e-9, f'確定予想オッズ = 今 × 比、期待値もそれで計算 {rows[0]}')
    check([x[6] for x in rows] == sorted([x[6] for x in rows], reverse=True), '確定予想の期待値の高い順')
    t3 = race['snaps']['C-2']['types']['3連単'][0]
    check(t3[5] == t3[2], '学習が無い券種は今のオッズのまま')
    # v2 回帰: 割安に見えた組ほど下がる（EV が高いほど 確定/締切前 が小さい）。データが足りない時点は使わない
    import math as _m
    import random as _r
    rnd = _r.Random(0)
    rows = []
    for i in range(3000):
        bt = ['2車複', 'ワイド', '2車単', '3連複', '3連単'][i % 5]
        ev = rnd.uniform(0.3, 2.0)
        o = rnd.uniform(3, 80)
        rows.append((bt, 'C-2', o, ev, -0.5 * _m.log(ev) + rnd.gauss(0, 0.05)))
    model = D.fit_model(rows + [(bt, 'C-5', o, ev, lr) for bt, _, o, ev, lr in rows[:100]])
    check('C-2' in model and 'C-5' not in model, f'組数が足りない時点は回帰を作らない {sorted(model)}')
    tbl = {'_model': model, '2車複': {'C-2': {'|': {'ratio': 1.3}}}}
    r_lo, r_hi = D.ratio(tbl, '2車複', 'C-2', 20.0, 0.8), D.ratio(tbl, '2車複', 'C-2', 20.0, 1.5)
    check(abs(r_hi - 1.5 ** -0.5) < 0.03 and r_lo > 1.0 > r_hi, f'割安に見えた組ほど下がる（EV0.8→{r_lo:.2f}・EV1.5→{r_hi:.2f}）')
    check(D.ratio(tbl, '2車複', 'C-2', 20.0, None) == 1.3 and D.ratio(tbl, '2車複', 'C-5', 20.0, 1.5) == 1.0,
          'EV が無い時・回帰が無い時点は従来の表')
    check(0.3 <= D.ratio(tbl, '2車複', 'C-2', 20.0, 100.0) <= 2.0, '極端な値は 0.3〜2.0 に収める')

    # v3: 割安に見えた組の補正（直近の候補で、実際の確定オッズが回帰の予想の何倍だったか）
    tb = {'2車複': {'C-2': {'|': {'ratio': 1.0}}}}  # 回帰なし・比 1.0 の単純な表で確かめる
    cand = []
    for i in range(400):
        d = f'209912{(i % 9) + 1:02d}'
        ev = 1.2 if i % 2 else 0.6   # 割安に見えた組（1.2）とそうでない組（0.6）
        f = 10.0 * (0.8 if ev >= 1 else 1.1)
        cand.append(dict(date=d, race_id='x', snap='C-2', bet_type='2車複', key='1-2', prob=round(ev / 10, 4), odds=10.0,
                         ev_now=ev, ev_d=ev, odds_final=f, has_fav=0, fav_win=0, payoff=0))
    cp = data / 'ledger' / 'cands.csv'
    write(cp, cand)
    sel = D.fit_selection(cp, tb)
    k = sel['C-2']['2車複']['k']
    check(k == D.SEL_CLIP[0] and sel['C-2']['2車複']['n'] < 400, f'直近の日の、割安に見えた組だけで測る（補正 ×{k}・下限で止める）')
    tb['_sel'] = sel
    check(D.ratio(tb, '2車複', 'C-2', 10.0, 1.2) == k and D.ratio(tb, '2車複', 'C-2', 10.0, 0.6) == 1.0,
          '補正は確定予想の期待値0.95以上だけ（割安でない組は変えない）')
    mid = D.ratio(tb, '2車複', 'C-2', 10.0, 0.9)
    check(k < mid < 1.0, f'0.85〜0.95 は徐々に補正 {mid:.3f}')
    check(D.ratio(tb, 'ワイド', 'C-2', 10.0, 1.2) == 1.0 and D.ratio(tb, '2車複', 'C-5', 10.0, 1.2) == 1.0, '測っていない券種・時点は補正しない')
    few = D.fit_selection(cp, tb, days=1)
    check(few['C-2']['2車複']['k'] > k, f'点数が少ない時は 1.0 に寄せる（×{few["C-2"]["2車複"]["k"]}）')
    print('\n全テスト成功')


if __name__ == '__main__':
    main()
