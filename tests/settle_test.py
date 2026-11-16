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
    check(st['a'] == dict(result='hit', payout=560, payoff=560.0, settled_at=st['a']['settled_at']), f'的中（組の順番は問わない） {st["a"]}')
    check(st['b']['result'] == 'hit' and st['b']['payout'] == 960, 'ワイドの的中・賭け金に比例した払戻')
    check(st['c']['result'] == 'miss' and st['c']['payout'] == 0, '外れ')
    check(st['d']['result'] == 'void' and st['d']['payout'] == 100, 'レース中止は返還')
    check(st['e']['result'] == 'void', '欠車を含む買い目は返還')
    check('f' not in st and n == 5, '結果がまだ無いレースは保留')
    st2, n2 = SB.settle(d)
    check(n2 == 0 and len(st2) == 5, '精算済みは繰り返さない')
    print('\n全テスト成功')


if __name__ == '__main__':
    main()
