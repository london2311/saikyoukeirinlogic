#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期待値ボードの［賭けた］記録を、確定した払戻で精算する:  python3 analysis/settle_bets.py --data <データ用リポジトリの data>

  入力: data/bets/bets_YYYYMM.json（閲覧ページが書く。1件 = 1点の購入）
        data/YYYY/payoffs.csv・races.csv・racecards.csv・results.csv（毎朝の収集）
  出力: data/bets/settled.json  {賭けID: {result: hit/miss/void, payout: 円, payoff: 100円あたり, settled_at}}

  * 的中: 払戻 × 賭け金 ÷ 100（ワイドは当たった組すべて）
  * 返還（void）: レース中止、または買い目に欠車の選手が含まれる → 賭け金を返す
  * 結果がまだ無いレースは保留（次回の実行で精算）
"""
import argparse
import csv
import glob
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))


def norm(bt, combo):
    parts = [int(x) for x in str(combo).replace('=', '-').split('-') if x.strip().isdigit()]
    if bt in ('3連複', '2車複', 'ワイド', '枠複'):
        parts = sorted(parts)
    return '-'.join(map(str, parts))


def read(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def load_results(data: Path, race_ids: set):
    """(払戻 {race: {(券種, 組): 100円あたり}}, 中止のレース, 欠車 {race: {車番}}, 結果が出たレース)"""
    pay, cancel, absent, done = {}, set(), {}, set()
    for p in glob.glob(str(data / '20??' / 'payoffs.csv')):
        for r in read(p):
            if r.get('race_id') in race_ids:
                try:
                    v = float(r['payoff'])
                except (TypeError, ValueError):
                    continue
                pay.setdefault(r['race_id'], {})[(r['bet_type'], norm(r['bet_type'], r['combination']))] = v
                done.add(r['race_id'])
    for p in glob.glob(str(data / '20??' / 'races.csv')):
        for r in read(p):
            if r.get('race_id') in race_ids and str(r.get('cancel', '0')) not in ('0', '', '0.0'):
                cancel.add(r['race_id'])
    for name in ('racecards.csv', 'results.csv'):
        for p in glob.glob(str(data / '20??' / name)):
            for r in read(p):
                if r.get('race_id') not in race_ids:
                    continue
                if str(r.get('absent', '0')) in ('1', '1.0', 'True') or r.get('accident') == '欠車':
                    try:
                        absent.setdefault(r['race_id'], set()).add(int(float(r['car_number'])))
                    except (TypeError, ValueError):
                        pass
    return pay, cancel, absent, done


def settle(data: Path, now=None):
    bets = []
    for p in sorted(glob.glob(str(data / 'bets' / 'bets_*.json'))):
        try:
            bets += json.load(open(p, encoding='utf-8'))
        except (OSError, ValueError):
            continue
    out_path = data / 'bets' / 'settled.json'
    try:
        settled = json.load(open(out_path, encoding='utf-8'))
    except (OSError, ValueError):
        settled = {}
    todo = [b for b in bets if b.get('id') and b['id'] not in settled and not b.get('deleted')]
    if not todo:
        return settled, 0
    pay, cancel, absent, done = load_results(data, {b['race_id'] for b in todo})
    stamp = (now or datetime.now(JST)).strftime('%Y-%m-%d %H:%M')
    n = 0
    for b in todo:
        rid, bt, key = b['race_id'], b['bt'], norm(b['bt'], b['key'])
        stake = float(b.get('stake') or 0)
        cars = {int(x) for x in key.split('-')}
        if rid in cancel or cars & absent.get(rid, set()):
            settled[b['id']] = dict(result='void', payout=stake, payoff=None, settled_at=stamp)
            n += 1
        elif rid in done:
            v = pay.get(rid, {}).get((bt, key))
            settled[b['id']] = dict(result='hit' if v else 'miss', payout=round(stake * v / 100.0) if v else 0,
                                    payoff=v, settled_at=stamp)
            n += 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(settled, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, sort_keys=True)
    return settled, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    a = ap.parse_args()
    settled, n = settle(Path(a.data))
    print(f'賭けの精算: 今回 {n}件 / 精算済み 合計 {len(settled)}件')


if __name__ == '__main__':
    main()
