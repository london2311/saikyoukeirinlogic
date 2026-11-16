#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netkeirin_snapshot.py / netkeirin_backfill.py のオフラインテスト（架空データ・通信なし）:
  python tests/netkeirin_snapshot_test.py
"""
import csv
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import netkeirin_collector as N  # noqa: E402
import netkeirin_snapshot as S  # noqa: E402
import netkeirin_backfill as B  # noqa: E402

DATE = '20991231'


def check(c, msg):
    if not c:
        raise SystemExit(f'NG   {msg}')
    print(f'OK   {msg}')


def odds_resp(nk):
    cars = [1, 2, 3, 4, 5]
    l9 = [[f'{a:02d}{b:02d}{c:02d}', '50.0', '0', '1'] for a in cars for b in cars for c in cars if len({a, b, c}) == 3]
    l7 = [[f'{a:02d}{b:02d}', '3.0', '4.5', '1'] for a in cars for b in cars if a < b]
    l3 = [['0102', '9.9', '0', '1']]  # 枠複（保存しない）
    return {'status': 'OK', 'data': {f'nkrace_odds::{nk}': {'official_dt': '2099-12-31 10:00:00',
                                                            'list_9': l9, 'list_7': l7, 'list_3': l3}}}


def test_snapshot():
    out = Path(tempfile.mkdtemp())
    t = [datetime(2099, 12, 31, 9, 0, tzinfo=N.JST)]
    races = [(f'{DATE}2301', '09:30'), (f'{DATE}2302', '10:00'), (f'{DATE}2303', '')]
    calls, flushes = [], []
    N.list_today = lambda date, venue=None: races

    def fake_fetch(url, params=None, interval=0, data=None):
        calls.append((t[0], data['race_id']))
        return json.dumps(odds_resp(data['race_id']))
    N.fetch = fake_fetch

    def sleep(sec):
        t[0] += timedelta(seconds=sec)
    S.main(['--out-dir', str(out), '--date', DATE, '--until', '11:00', '--flush-min', '30',
            '--flush-cmd', 'true'], clock=lambda: t[0], sleep=lambda s: (sleep(s), flushes.append(1)))
    rows = list(csv.DictReader(open(out / '2099' / 'odds_snap' / f'snap_{DATE}.csv', encoding='utf-8-sig')))
    snaps = sorted({(r['race_id'], r['snap']) for r in rows})
    check(len(calls) == 4 and len(snaps) == 4, f'2レース × 2時点（12分前・4分前）を1回ずつ記録 {snaps}')
    check(all(timedelta(minutes=3) <= (datetime(2099, 12, 31, 9, 30, tzinfo=N.JST) - c[0]) <= timedelta(minutes=12)
              for c in calls if c[1].endswith('2301')), '発走 12〜4 分前の範囲で取得')
    check({r['bet_type'] for r in rows} == {'3連単', 'ワイド'}, '枠番の券種は保存しない')
    check(t[0] < datetime(2099, 12, 31, 10, 1, tzinfo=N.JST), '記録すべきレースが無くなったら --until を待たずに終了')

    # 途中から再開しても、記録済みのものは取り直さない
    calls.clear()
    t[0] = datetime(2099, 12, 31, 9, 57, tzinfo=N.JST)
    S.main(['--out-dir', str(out), '--date', DATE, '--until', '11:00'], clock=lambda: t[0], sleep=sleep)
    check(calls == [], '再開時は記録済みを飛ばす')

    # 開始が遅れた場合: 12分前の記録は 3 分以上遅れたら取らない（4分前は取る）
    out2 = Path(tempfile.mkdtemp())
    calls.clear()
    t[0] = datetime(2099, 12, 31, 9, 24, tzinfo=N.JST)
    S.main(['--out-dir', str(out2), '--date', DATE, '--until', '9:40'], clock=lambda: t[0], sleep=sleep)
    rows = list(csv.DictReader(open(out2 / '2099' / 'odds_snap' / f'snap_{DATE}.csv', encoding='utf-8-sig')))
    got = sorted({(r['race_id'], r['snap']) for r in rows})
    check([s for _, s in got] == ['T-4'], f'遅れた 12分前は取らず、4分前だけ記録 {got}')


def test_backfill():
    out = Path(tempfile.mkdtemp())
    (out / '2099').mkdir()
    with open(out / '2099' / 'races.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['race_id', 'date'])
        for i in range(130):
            w.writerow([f'x{i}', '20991201'])
        w.writerow(['old1', '20991202'])
    seen = []

    def fake_collect(d, out_dir, skip=None):
        seen.append((d, 'old1' in skip))
        return dict(races=5, failed=0, listed=6)
    N.collect_date = fake_collect
    B.main(['--out-dir', str(out), '--from', '20991201', '--to', '20991203', '--budget-min', '100'])
    check([d for d, _ in seen] == ['20991202', '20991203'], f'レース数が少ない日だけ補充 {seen}')
    check(all(s for _, s in seen), '取得済みのレースIDを渡して取り直さない')
    seen.clear()
    B.main(['--out-dir', str(out), '--from', '20991201', '--to', '20991203'])
    check(seen == [], '一度補充した日は繰り返さない')


def test_refetch_odds():
    out = Path(tempfile.mkdtemp())
    (out / '2099').mkdir()
    with open(out / '2099' / 'races.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['race_id', 'date', 'cancel'])
        for i in range(130):
            w.writerow([f'{(i % 12) + 1:02d}{23 + i // 12:02d}20991201', '20991201', 0])
    calls = []

    def fake_odds(nk, payoffs):
        calls.append(nk)
        rid = N.to_wt_race_id(nk)
        return [dict(race_id=rid, date='20991201', race_number=1, bet_type='ワイド', combination='1-2', odds=2.0,
                     odds_max=3.0, payoff='', popularity=1, absent=0)]
    N.fetch_odds = fake_odds
    B.main(['--out-dir', str(out), '--from', '20991201', '--to', '20991201', '--odds-from', '20991201',
            '--odds-budget-min', '10'])
    check(len(calls) == 130 and calls[0] == '209912012301', f'旧形式のレースのオッズを netkeirin 形式のIDで取り直す {calls[:2]}')
    calls.clear()
    B.main(['--out-dir', str(out), '--from', '20991201', '--to', '20991201', '--odds-from', '20991201',
            '--odds-budget-min', '10'])
    check(calls == [], '取り直し済みの日は繰り返さない')


if __name__ == '__main__':
    test_snapshot()
    test_backfill()
    test_refetch_odds()
    print('\n全テスト成功')
