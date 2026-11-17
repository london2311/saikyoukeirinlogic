#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analysis/daily_report.py のテスト（架空データ）:  python tests/daily_report_test.py"""
import csv
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'analysis'))
import daily_report as DR  # noqa: E402
import snapshot_ledger as SL  # noqa: E402

JST = timezone(timedelta(hours=9))


def check(c, msg):
    if not c:
        raise SystemExit(f'NG   {msg}')
    print(f'OK   {msg}')


def main():
    d = Path(tempfile.mkdtemp())
    led = d / 'ledger'
    led.mkdir()
    rows = []
    for day in ('20991229', '20991230'):
        for i in range(10):
            rid = f'{i + 1:02d}23{day}'
            rows.append(dict(date=day, race_id=rid, venue='取手', snap='C-2', strategy='REC', bet_type='2車複', key='1-2', prob=0.1,
                             prob_market=0.09, odds=12.0, ev=1.2, stake=100, payoff=1200 if i == 0 else 0, model='v105'))
    with open(led / 'ledger.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=SL.LEDGER_FIELDS)
        w.writeheader()
        w.writerows(rows)
    json.dump({'20991229': 10, '20991230': 4}, open(led / 'days.json', 'w'))
    # 2車複 EV1.10以上: 予想 100回 に対して実際 60回（z≈-4）→ ⚠
    calib = {'20991230': dict(bands={'2車複|EV1.10以上': [1000, 100.0, 60, 90.0], '2車複|EV1.00未満': [5000, 400.0, 401, 360.0]},
                              ll=[[2.0, 2.1], [2.2, 2.25]] * 20)}
    json.dump(calib, open(led / 'calib.json', 'w'))
    (d / '2099').mkdir()
    with open(d / '2099' / 'races.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['race_id', 'date', 'cancel'])
        w.writeheader()
        w.writerows([dict(race_id=f'r{i}', date='20991230', cancel=0) for i in range(10)])
    (d / 'bets').mkdir()
    bets = [dict(id='a', date='20991230', race_id='0123' + '20991230', venue='取手', rn=1, bt='2車複', key='1-2', stake=100),
            dict(id='b', date='20991230', race_id='0223' + '20991230', venue='取手', rn=2, bt='2車複', key='3-4', stake=100),
            dict(id='c', date='20991227', race_id='0923' + '20991227', venue='取手', rn=9, bt='2車複', key='1-2', stake=100)]
    json.dump(bets, open(d / 'bets' / 'bets_209912.json', 'w'))
    json.dump({'a': dict(result='hit', payout=1200), 'b': dict(result='miss', payout=0)}, open(d / 'bets' / 'settled.json', 'w'))
    strategies = [dict(id='REC', label='ボードの推奨', registered='20991201')]
    r = DR.build(d, strategies, now=datetime(2099, 12, 31, 8, 0, tzinfo=JST))
    y = r['yesterday']
    check(y['date'] == '20991230' and y['sum']['n'] == 10 and y['sum']['hits'] == 1 and abs(y['sum']['roi'] - 1.2) < 1e-9, f'前日の推奨と結果 {y["sum"]}')
    rec = r['strategies'][0]
    check(rec['all']['n'] == 20 and rec['week']['n'] == 20, '買い方ごとの累計・直近7日')
    m = r['mine']
    check(m['all']['n'] == 2 and abs(m['all']['roi'] - 6.0) < 1e-9 and m['off_recs'] == 1, f'実際に賭けた分と推奨外の点数 {m["all"]["roi"]} / {m["off_recs"]}')
    al = ' / '.join(r['alerts'])
    check('2車複|EV1.10以上' in al and '予想が高すぎる' in al, '確率のずれ（|z|≥3）を ⚠')
    check('EV1.00未満' not in al, '運の範囲のずれは ⚠ にしない')
    check('記録漏れ' in al and '20991230' in al, 'その日のレースの7割未満しか判定できなければ ⚠')
    check('結果待ち' in al and '20991227' in al, '2日以上前の記録が結果待ちなら ⚠')
    check(r['ll']['week']['z'] is not None and r['ll']['week']['z'] < 0 and 'オッズだけより' not in al, '融合がオッズより良ければ ⚠ なし')
    DR.main(['--data', str(d), '--strategies', os.path.join(ROOT, 'analysis', 'strategies.json')])
    check((led / 'daily_report.json').exists(), 'daily_report.json を書く')
    print('\n全テスト成功')


if __name__ == '__main__':
    main()
