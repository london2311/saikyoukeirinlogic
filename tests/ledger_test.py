#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analysis/snapshot_ledger.py のオフラインテスト（架空の1日・通信なし）:  python tests/ledger_test.py
"""
import csv
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'analysis'))
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import keirin_collector as C  # noqa: E402
import now_test as NT  # noqa: E402  架空レース（7車・2車複 1=2 が割安）
import snapshot_ledger as L  # noqa: E402

DATE = '20991231'
RID = '012320991231'


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def check(c, msg):
    if not c:
        raise SystemExit(f'NG   {msg}')
    print(f'OK   {msg}')


def main():
    _, _, full, _ = NT.build_states()
    cards = C.parse_racecard(full)
    odds = C.parse_odds(full)
    data = Path(tempfile.mkdtemp())
    y = data / '2099'
    snap = [dict(race_id=RID, date=DATE, race_number=5, snap=s, snap_at='', start_at='', official_dt='',
                 bet_type=o['bet_type'], combination=o['combination'], odds=o['odds'], odds_max=o.get('odds_max', ''),
                 popularity='', absent=0) for s in ('T-4', 'T-12') for o in odds]
    # 2レース目（まだ結果が無い）も記録してある日
    write(y / 'odds_snap' / f'snap_{DATE}.csv', snap + [dict(snap[0], race_id='0623' + DATE)])
    write(y / 'racecards.csv', [dict(r, race_id=RID) for r in cards])
    write(y / 'races.csv', [dict(race_id=RID, date=DATE, venue_name='取手', cancel=0)])
    write(y / 'payoffs.csv', [dict(race_id=RID, bet_type='2車複', combination='1-2', payoff=900, popularity=1)])
    strat = Path(tempfile.mkdtemp()) / 's.json'
    strat.write_text(json.dumps({'strategies': [
        dict(id='Q1', label='2車複 EV1.10以上', bet_type='2車複', signal='EV_f', min=1.10, registered='20990101',
             snaps=['T-4', 'T-12']),
        dict(id='F1', label='未来に登録', bet_type='2車複', signal='EV_f', min=1.10, registered='21000101'),
    ]}, ensure_ascii=False), encoding='utf-8')

    L.main(['--data', str(data), '--strategies', str(strat), '--today', DATE])
    check(not (data / 'ledger' / 'ledger.csv').exists() or not list(csv.DictReader(open(data / 'ledger' / 'ledger.csv', encoding='utf-8'))),
          '今日の分は、結果がそろうまで台帳に入れない（途中で結果を集めても）')
    L.main(['--data', str(data), '--strategies', str(strat), '--today', '21000101'])
    led = list(csv.DictReader(open(data / 'ledger' / 'ledger.csv', encoding='utf-8')))
    check([(r['strategy'], r['snap'], r['key']) for r in led] == [('Q1', 'T-4', '1-2'), ('Q1', 'T-12', '1-2')] or
          sorted((r['strategy'], r['snap'], r['key']) for r in led) == [('Q1', 'T-12', '1-2'), ('Q1', 'T-4', '1-2')],
          f'割安な 2車複 1=2 を両時点で記録 {[(r["strategy"], r["snap"], r["key"]) for r in led]}')
    check(all(float(r['payoff']) == 900 for r in led), '実際の払戻を付ける')
    check(not any(r['strategy'] == 'F1' for r in led), '登録日より前のレースは数えない（後出し防止）')
    rep = (data / 'ledger' / 'report.md').read_text(encoding='utf-8')
    check('Q1' in rep and '900.0%' in rep, '成績表に回収率を出す')

    L.main(['--data', str(data), '--strategies', str(strat), '--today', '21000101'])
    led2 = list(csv.DictReader(open(data / 'ledger' / 'ledger.csv', encoding='utf-8')))
    check(len(led2) == len(led), '同じ日を二重に数えない')

    # ボードの推奨（EV_d: 下落の学習で補正・複数券種・1レース最大6点）と、確率の当たり方の記録（締切2分前）
    data2 = Path(tempfile.mkdtemp())
    y2 = data2 / '2099'
    write(y2 / 'odds_snap' / f'snap_{DATE}.csv', [dict(r, snap='C-2') for r in snap if r['snap'] == 'T-4'])
    write(y2 / 'racecards.csv', [dict(r, race_id=RID) for r in cards])
    write(y2 / 'races.csv', [dict(race_id=RID, date=DATE, venue_name='取手', cancel=0)])
    write(y2 / 'payoffs.csv', [dict(race_id=RID, bet_type='2車複', combination='1-2', payoff=900, popularity=1)])
    (data2 / 'ledger').mkdir()
    (data2 / 'ledger' / 'drift.json').write_text(json.dumps({'table': {'2車複': {'C-2': {'|': {'ratio': 0.5}}}}}), encoding='utf-8')
    strat2 = Path(tempfile.mkdtemp()) / 's.json'
    strat2.write_text(json.dumps({'strategies': [
        dict(id='R', label='推奨', bet_type='2車複', bet_types=['2車複', 'ワイド'], signal='EV_d', min=1.10, per_race=6,
             registered='20990101', snaps=['C-2']),
        dict(id='Q', label='2車複', bet_type='2車複', signal='EV_f', min=1.10, registered='20990101', snaps=['C-2'])]},
        ensure_ascii=False), encoding='utf-8')
    L.main(['--data', str(data2), '--strategies', str(strat2), '--today', '21000101'])
    led3 = list(csv.DictReader(open(data2 / 'ledger' / 'ledger.csv', encoding='utf-8')))
    q = [r for r in led3 if r['strategy'] == 'Q']
    rr = [r for r in led3 if r['strategy'] == 'R']
    check(q and all(float(r['prob']) * round(float(r['odds']) * 0.5, 1) >= 1.10 for r in rr) and len(rr) < len(q),
          f'推奨は締切後の下落（確定予想オッズ）で判定する（{len(rr)}点 < 下落なし {len(q)}点）')
    cal = json.loads((data2 / 'ledger' / 'calib.json').read_text(encoding='utf-8'))[DATE]
    check(sum(v[0] for k, v in cal['bands'].items() if k.startswith('2車複')) == 21 and len(cal['ll']) == 1,
          '締切2分前の確率の当たり方（2車複 21組・的中組の対数損失）を記録')
    # REC2 の条件: 100倍以下・判定値2.0以下・強い1番人気（1着45%以上）はその選手を含む組だけ
    s2 = dict(id='R2', bet_type='2車複', bet_types=['2車複', 'ワイド'], signal='EV_f', min=1.1, max_val=2.0, odds_max=100.0001, fav_min=0.45)
    tk = [dict(type='2車複', key='1-2', prob=0.2, prob_market=0.18, odds=6.0, ev=1.2),      # 1番人気を含む → 残る
          dict(type='2車複', key='3-4', prob=0.02, prob_market=0.01, odds=60.0, ev=1.2),    # 1番人気に逆らう → 除く
          dict(type='ワイド', key='1-5', prob=0.01, prob_market=0.01, odds=150.0, ev=1.5),  # 100倍超 → 除く
          dict(type='2車複', key='1-7', prob=0.05, prob_market=0.01, odds=50.0, ev=2.5)]    # 判定値2.0超 → 除く
    strong = dict(riders=[dict(num=1, win=0.55), dict(num=2, win=0.2)])
    weak = dict(riders=[dict(num=1, win=0.35), dict(num=2, win=0.3)])
    check([t['key'] for t in L.select(s2, tk, strong)] == ['1-2'], 'REC2: 強い1番人気は含む組だけ・100倍超と判定値2.0超は除く')
    check(sorted(t['key'] for t in L.select(s2, tk, weak)) == ['1-2', '3-4'], 'REC2: 1番人気が強くなければ逆らう組も残す')
    print('\n全テスト成功')


if __name__ == '__main__':
    main()
