#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analysis/settle_bets.py のテスト（架空データ）:  python tests/settle_test.py"""
import csv
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'analysis'))
import settle_bets as SB  # noqa: E402


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
    d = Path(tempfile.mkdtemp())
    y = d / '2099'
    write(y / 'payoffs.csv', [dict(race_id='R1', bet_type='2車複', combination='2-1', payoff=560),
                              dict(race_id='R1', bet_type='ワイド', combination='1-2', payoff=210),
                              dict(race_id='R1', bet_type='ワイド', combination='1-5', payoff=480)])
    write(y / 'races.csv', [dict(race_id='R1', cancel=0), dict(race_id='R2', cancel=1), dict(race_id='R3', cancel=0)])
    write(y / 'racecards.csv', [dict(race_id='R1', car_number=7, absent=1)])
    bets = [dict(id='a', race_id='R1', bt='2車複', key='1-2', stake=100),
            dict(id='b', race_id='R1', bt='ワイド', key='1-5', stake=200),
            dict(id='c', race_id='R1', bt='2車複', key='1-3', stake=100),
            dict(id='d', race_id='R2', bt='2車複', key='1-3', stake=100),
            dict(id='e', race_id='R1', bt='ワイド', key='3-7', stake=100),
            dict(id='f', race_id='R3', bt='2車複', key='1-2', stake=100)]
    (d / 'bets').mkdir()
    json.dump(bets, open(d / 'bets' / 'bets_209912.json', 'w'))
    st, n = SB.settle(d)
    check({k: st['a'][k] for k in ('result', 'payout', 'payoff')} == dict(result='hit', payout=560, payoff=560.0), f'的中（組の順番は問わない） {st["a"]}')
    check(st['b']['result'] == 'hit' and st['b']['payout'] == 960, 'ワイドの的中・賭け金に比例した払戻')
    check(st['c']['result'] == 'miss' and st['c']['payout'] == 0, '外れ')
    check(st['d']['result'] == 'void' and st['d']['payout'] == 100, 'レース中止は返還')
    check(st['e']['result'] == 'void', '欠車を含む買い目は返還')
    check('f' not in st and n == 5, '結果がまだ無いレースは保留')
    st2, n2 = SB.settle(d)
    check(n2 == 0 and len(st2) == 5, '精算済みは繰り返さない')
    # 過去のレースを後から記録した分も精算し、確定オッズ（外れでも）を付ける
    rid = '0723' + '20991231'
    write(y / 'races.csv', [dict(race_id='R1', cancel=0), dict(race_id='R2', cancel=1), dict(race_id='R3', cancel=0), dict(race_id=rid, cancel=0)])
    write(y / 'payoffs.csv', [dict(race_id='R1', bet_type='2車複', combination='2-1', payoff=560),
                              dict(race_id='R1', bet_type='ワイド', combination='1-2', payoff=210),
                              dict(race_id='R1', bet_type='ワイド', combination='1-5', payoff=480),
                              dict(race_id=rid, bet_type='2車単', combination='3-1', payoff=2350)])
    write(y / 'odds' / 'odds_20991231.csv', [dict(race_id=rid, bet_type='2車単', combination='1-3', odds=18.7, absent=0),
                                              dict(race_id=rid, bet_type='2車単', combination='3-1', odds=24.1, absent=0)])
    json.dump(bets + [dict(id='g', race_id=rid, bt='2車単', key='1-3', stake=100), dict(id='h', race_id=rid, bt='2車単', key='3-1', stake=300)],
              open(d / 'bets' / 'bets_209912.json', 'w'))
    st3, _ = SB.settle(d)
    check(st3['g']['result'] == 'miss' and st3['g']['odds_final'] == 18.7, f'外れでも確定オッズを付ける {st3["g"]}')
    check(st3['h']['result'] == 'hit' and st3['h']['payout'] == 7050 and st3['h']['odds_final'] == 23.5, f'的中は払戻の倍率 {st3["h"]}')
    print('\n全テスト成功')


if __name__ == '__main__':
    main()
