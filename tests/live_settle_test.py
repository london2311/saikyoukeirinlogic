#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""live_settle.py のテスト（架空データ・通信なし）:  python tests/live_settle_test.py"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import live_settle as LS  # noqa: E402
import netkeirin_collector as N  # noqa: E402


def check(c, msg):
    if not c:
        raise SystemExit(f'NG   {msg}')
    print(f'OK   {msg}')


def main():
    D1, D2 = '20991230', '20991231'
    nk1, nk2 = f'{D1}2309', f'{D2}2301'
    wt1, wt2 = N.to_wt_race_id(nk1), N.to_wt_race_id(nk2)
    bets = [dict(id='a', date=D1, race_id=wt1, bt='2車複', key='2-1', stake=200),
            dict(id='b', date=D1, race_id=wt1, bt='3連単', key='1-2-7', stake=100),
            dict(id='c', date=D1, race_id=wt1, bt='ワイド', key='1-3', stake=100),
            dict(id='d', date=D2, race_id=wt2, bt='2車単', key='1-2', stake=100),
            dict(id='e', date=D1, race_id=wt1, bt='2車複', key='3-4', stake=100, deleted=True)]
    remote = {'data/bets/bets_209912.json': json.dumps(bets), 'data/bets/settled.json': json.dumps({'c': dict(result='miss')})}
    reads, fetched = [], []

    def read_remote(paths):
        reads.append(paths)
        return {p: remote.get(p) for p in paths}
    res = {nk1: dict(orders=[dict(car_number=1, accident=''), dict(car_number=7, accident='欠車')],
                     payoffs=[dict(bet_type='2車複', combination='1-2', payoff=480)]),
           nk2: dict(orders=[], payoffs=[])}

    def fetch_result(nk):
        fetched.append(nk)
        return res[nk]
    repo = Path(tempfile.mkdtemp())
    s = LS.LiveSettler(repo, fetch_result=fetch_result, read_remote=read_remote)
    races1 = [(nk1, '23:00')]
    t = N.start_dt(D1, '23:00')
    check(not s.tick(races1, D1, t + timedelta(minutes=3)) and fetched == [], '発走から6分たつまでは結果を見に行かない')
    check(s.tick(races1, D1, t + timedelta(minutes=7)) and fetched == [nk1], '発走6分後に、記録があるレースの結果だけ取得')
    live = json.loads((repo / 'data' / 'bets' / 'settled_live.json').read_text(encoding='utf-8'))
    check(live['a']['result'] == 'hit' and live['a']['payout'] == 960 and live['a']['odds_final'] == 4.8, f'的中（組の順番は問わない） {live["a"]}')
    check(live['b']['result'] == 'void' and live['b']['payout'] == 100, '欠車を含む買い目は返還')
    check('c' not in live and 'e' not in live, '正式に精算済み・取り消した記録は触らない')
    fetched.clear()
    # 翌日に切り替わった後も、前日のレースの記録は覚えていて、当日のレースは払戻が出るまで2分おきに取り直す
    races2 = [(nk2, '08:30')]
    t2 = N.start_dt(D2, '08:30')
    check(not s.tick(races2, D2, t2 + timedelta(minutes=7)) and fetched == [nk2], '払戻がまだ無ければ精算しない')
    check(not s.tick(races2, D2, t2 + timedelta(minutes=8)) and fetched == [nk2], '2分たつまでは取り直さない')
    res[nk2] = dict(orders=[dict(car_number=1, accident='')], payoffs=[dict(bet_type='2車単', combination='2-1', payoff=990)])
    s.read_at = None
    check(s.tick(races2, D2, t2 + timedelta(minutes=10)) and fetched == [nk2, nk2], '2分後に取り直して精算')
    live = json.loads((repo / 'data' / 'bets' / 'settled_live.json').read_text(encoding='utf-8'))
    check(live['d']['result'] == 'miss' and live['d']['odds_final'] is None, f'外れ {live["d"]}')
    check(set(s.known) == {D1, D2}, '前日のレース一覧も覚えている（最終レースの精算用）')
    # 記録ジョブが止まっていた間に終わったレース（発走から何時間もたった）も精算する
    s2 = LS.LiveSettler(Path(tempfile.mkdtemp()), fetch_result=fetch_result, read_remote=read_remote)
    fetched.clear()
    check(s2.tick(races1, D1, t + timedelta(hours=5)) and fetched == [nk1], '発走から5時間たったレースも精算する')
    print('\n全テスト成功')


if __name__ == '__main__':
    main()
