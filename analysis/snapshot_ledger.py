#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
事前登録した買い方を、発走前に記録したオッズ（締切直前の代わり）で毎日判定し、結果を台帳に積み上げる:

  python3 analysis/snapshot_ledger.py --data _data_repo/data [--strategies analysis/strategies.json]

  * 入力: data/YYYY/odds_snap/snap_YYYYMMDD.csv（netkeirin_snapshot.py）と、翌朝収集の出走表・払戻
  * 買い方は analysis/strategies.json に固定（登録日より前のレースは数えない＝後出しにしない）
  * 判定には各レースの記録時点（T-4 を本番、T-12 を参考）のオッズだけを使う。払戻は実際の払戻金
  * 出力（データ用リポジトリ内のみ）: data/ledger/ledger.csv（買い目1点1行）と data/ledger/report.md
"""
import argparse
import csv
import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import keirin_engine as E  # noqa: E402
import odds_drift as DR  # noqa: E402
import train_model as T  # noqa: E402

CALIB_TYPES = ('2車複', 'ワイド')
EV_BANDS = ((1.1, 'EV1.10以上'), (1.0, 'EV1.00-1.10'), (0.0, 'EV1.00未満'))

LEDGER_FIELDS = ['date', 'race_id', 'venue', 'snap', 'strategy', 'bet_type', 'key', 'prob', 'prob_market', 'odds', 'ev',
                 'stake', 'payoff', 'model']


def read_csv(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def by_race(rows):
    out = defaultdict(list)
    for r in rows:
        out[r['race_id']].append(r)
    return out


def load_day(data: Path, date: str):
    """その日の (snapshots{race:{snap:[rows]}}, cards{race:[rows]}, payoffs{race:{(bt,key):payoff}}, venue{race:name})"""
    year = data / date[:4]
    snap_path = year / 'odds_snap' / f'snap_{date}.csv'
    snaps = defaultdict(lambda: defaultdict(list))
    for r in read_csv(snap_path):
        snaps[r['race_id']][r['snap']].append(r)
    ids = set(snaps)
    cards = defaultdict(list)
    for p in sorted(glob.glob(str(year / 'racecards.csv'))):
        for r in read_csv(p):
            if r['race_id'] in ids:
                cards[r['race_id']].append(r)
    pay, venue, settled = defaultdict(dict), {}, set()
    for p in sorted(glob.glob(str(year / 'payoffs.csv'))):
        for r in read_csv(p):
            if r['race_id'] in ids:
                try:
                    v = float(r['payoff'])
                except (TypeError, ValueError):
                    continue
                pay[r['race_id']][(r['bet_type'], T.norm_combo(r['bet_type'], r['combination']))] = v
                settled.add(r['race_id'])
    for p in sorted(glob.glob(str(year / 'races.csv'))):
        for r in read_csv(p):
            if r['race_id'] in ids:
                venue[r['race_id']] = r.get('venue_name', '')
                if str(r.get('cancel', '0')) not in ('0', '', '0.0'):
                    settled.discard(r['race_id'])
    return snaps, cards, pay, venue, settled


def select(strategy, tickets, res=None):
    """買い方の条件に合う買い目（tickets は E.analyze の tickets）。"girls": true の買い方はガールズのレースだけ"""
    s = strategy
    if s.get('girls') and not (res or {}).get('girls'):
        return []
    sig = s.get('signal', 'EV_f')
    if sig == 'top':  # その券種で当たる確率が最も高い1点（的中率重視）
        cand = [t for t in tickets if t['type'] == s['bet_type'] and t['prob'] >= s.get('p_min', 0.0)
                and s.get('odds_min', 1.0) <= t['odds'] < s.get('odds_max', 1e9)]
        allt = [t for t in tickets if t['type'] == s['bet_type']]
        best = max(allt, key=lambda t: t['prob']) if allt else None
        return [dict(best, val=best['prob'])] if best is not None and best in cand else []
    out = []
    types = s.get('bet_types') or [s['bet_type']]
    res = res or {}
    # fav_min: 1番人気（融合した1着の確率が最も高い選手）の1着の確率がこれ以上なら、その選手を含む組だけ
    fav = None
    if s.get('fav_min') and res.get('riders'):
        top = max(res['riders'], key=lambda r: r.get('win', 0))
        if top.get('win', 0) >= s['fav_min']:
            fav = str(top['num'])
    for t in tickets:
        if t['type'] not in types:
            continue
        if sig == 'EV_d':  # 期待値ボードと同じ: 締切後の下落を見込んだ確定予想オッズ（0.1倍単位）× 確率
            of = round(t['odds'] * DR.ratio(res.get('_drift') or {}, t['type'], res.get('_snap', 'C-2'), t['odds'], t['ev']), 1)
            val = t['prob'] * of
        else:
            val = t['ev'] if sig == 'EV_f' else ((t['prob_market'] or 0) * t['odds'])
        if val < s.get('min', 1.10) or t['prob'] < s.get('p_min', 0.0):
            continue
        if not (s.get('odds_min', 1.0) <= t['odds'] < s.get('odds_max', 1e9)):
            continue
        if val > s.get('max_val', 1e9):  # 期待値が極端に高い組は、売れていない組のゆがみの可能性が高い
            continue
        if fav and fav not in t['key'].split('-'):
            continue
        out.append(dict(t, val=val))
    out.sort(key=lambda x: -x['val'])
    if s.get('per_race'):
        out = out[:int(s['per_race'])]
    return out


def lines_of(cards):
    lines = {}
    for r in cards:
        if str(r.get('absent', 0)) in ('1', 'True', 'true'):
            continue
        single = str(r.get('is_single', '')) in ('1', '1.0', 'True', 'true')
        lid = f's{r["car_number"]}' if single or not r.get('line_order') else str(r.get('line_order'))
        try:
            pos = int(float(r.get('line_position') or 1))
        except ValueError:
            pos = 1
        lines.setdefault(lid, []).append((pos, int(r['car_number'])))
    return [[c for _, c in sorted(v)] for v in lines.values()]


def pick_group(s, res, cards):
    """買い目群（kind: group）の買い方: 期待値ボードと同じ買い目群（流し・フォーメーション・BOX・組み合わせ、✂切り後も）から、
    的中率 p_min 以上・合成オッズ odds_max 以下・確定予想での期待値 min〜max_val・強い1番人気（fav_min）はその選手を含む組だけ、
    の中で期待値が最も高い1つ。戻り値 (群, [(組, 賭け金, 今のオッズ, 確率)])。無ければ None"""
    import formations as F
    bt = s['bet_type']
    drift, snap = res.get('_drift') or {}, res.get('_snap', 'C-2')
    probs, odds, now = {}, {}, {}
    for t in res['tickets']:
        probs.setdefault(t['type'], {})[t['key']] = t['prob']
        odds.setdefault(t['type'], {})[t['key']] = round(t['odds'] * DR.ratio(drift, t['type'], snap, t['odds'], t['ev']), 1)
        now[(t['type'], t['key'])] = t['odds']
    if '3連単' not in probs or bt not in probs:
        return None
    dist3 = {tuple(int(x) for x in k.split('-')): p for k, p in probs['3連単'].items()}
    gs = F.build_groups(probs, odds, lines_of(cards), dist3)
    gs = gs + F.combine(gs, probs, odds, dist3)
    fav = None
    if s.get('fav_min') and res.get('riders'):
        top = max(res['riders'], key=lambda r: r.get('win', 0))
        if top.get('win', 0) >= s['fav_min']:
            fav = str(top['num'])
    p_min = s.get('p_min', 0.10)
    best = None
    for g in gs:
        if g['type'] != bt or g['p'] < p_min:
            continue
        pr = F.prune(g, probs, odds, p_min, dist3)
        for c in [g] + ([pr] if pr else []):
            if g['n'] < 2 or c['n'] < 2 or not c.get('odds') or c['odds'] > s.get('odds_max', 1e9):
                continue
            if not (s.get('min', 1.10) <= c['ev'] <= s.get('max_val', 1e9)):
                continue
            if fav and any(fav not in k.split('-') for k in c['keys']):
                continue
            if best is None or c['ev'] > best['ev']:
                best = c
    if best is None:
        return None
    _, st = F.realized_ev(best, probs, odds)
    return best, [(k, stake, now[(bt, k)], probs[bt].get(k, 0.0)) for k, stake in zip(best['keys'], st)]


def add_calib(calib, res, pay):
    """締切2分前の確率の当たり方（2車複・ワイドの全組を期待値の帯ごとに）と、2車複の的中組に付けた確率（融合 vs 市場）"""
    for t in res['tickets']:
        if t['type'] not in CALIB_TYPES or not t['prob']:
            continue
        band = next(lab for lo, lab in EV_BANDS if t['ev'] >= lo)
        c = calib['bands'].setdefault(f"{t['type']}|{band}", [0, 0.0, 0, 0.0])
        hit = 1 if pay.get((t['type'], t['key']), 0) > 0 else 0
        c[0] += 1
        c[1] += t['prob']
        c[2] += hit
        c[3] += t['prob'] * (1 - t['prob'])
    win = [t for t in res['tickets'] if t['type'] == '2車複' and pay.get(('2車複', t['key']), 0) > 0]
    if win and win[0]['prob'] and win[0]['prob_market']:
        import math
        calib['ll'].append([round(-math.log(win[0]['prob']), 5), round(-math.log(win[0]['prob_market']), 5)])


def evaluate_day(model, strategies, data: Path, date: str, drift=None, calib=None):
    drift = drift or {}
    calib = calib if calib is not None else dict(bands={}, ll=[])
    snaps, cards, pay, venue, settled = load_day(data, date)
    rows = []
    for rid in sorted(snaps):
        if rid not in settled or rid not in cards:
            continue
        for snap, orows in snaps[rid].items():
            res = E.analyze(model, cards[rid], orows, venue.get(rid, ''), {})
            if not res.get('ok') or not res.get('market_ok'):
                continue
            res['_snap'], res['_drift'] = snap, drift
            if snap == 'C-2':
                add_calib(calib, res, pay[rid])
            for s in strategies:
                if date < s['registered'] or snap not in s.get('snaps', ['C-2']):
                    continue
                if s.get('kind') == 'group':  # 買い目群: 群の各点を、合成オッズの配分の賭け金で記録
                    pk = pick_group(s, res, cards[rid])
                    if pk:
                        g, pts = pk
                        for k, stake, od, pr in pts:
                            rows.append(dict(date=date, race_id=rid, venue=venue.get(rid, ''), snap=snap, strategy=s['id'],
                                             bet_type=g['type'], key=k, prob=round(pr, 5), prob_market='', odds=od, ev=round(g['ev'], 4),
                                             stake=stake, payoff=pay[rid].get((g['type'], k), 0.0), model=model['json'].get('version', '')))
                    continue
                for t in select(s, res['tickets'], res):
                    rows.append(dict(date=date, race_id=rid, venue=venue.get(rid, ''), snap=snap, strategy=s['id'],
                                     bet_type=t['type'], key=t['key'], prob=round(t['prob'], 5),
                                     prob_market=round(t['prob_market'] or 0, 5), odds=t['odds'], ev=round(t['ev'], 4),
                                     stake=100, payoff=pay[rid].get((t['type'], t['key']), 0.0),
                                     model=model['json'].get('version', '')))
    return rows, len([r for r in snaps if r in settled and r in cards])


def roi_ci(stake, ret, groups, n_boot=400, seed=0):
    by = defaultdict(lambda: [0.0, 0.0])
    for s, r, g in zip(stake, ret, groups):
        by[g][0] += s
        by[g][1] += r
    arr = np.array(list(by.values()))
    if len(arr) == 0:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(n_boot, len(arr)))
    b = arr[idx, 1].sum(axis=1) / np.maximum(arr[idx, 0].sum(axis=1), 1)
    return float(np.percentile(b, 5)), float(np.percentile(b, 95))


def report(ledger, strategies, races_by_date):
    L = ['# 事前登録した買い方の成績（発走前オッズで判定・実際の払戻）\n']
    if ledger:
        dates = sorted({r['date'] for r in ledger} | set(races_by_date))
        L.append(f'- 期間: {dates[0]}〜{dates[-1]}（{len(dates)}日、判定できたレース {sum(races_by_date.values())}）')
    L.append('- 1点100円。90%区間はレース単位のブートストラップ。C-2 = 締切2分前のオッズで判定（本番）、C-10 = 締切10分前（参考）。締切は発走の5分前\n')
    L.append('| 買い方 | 時点 | 登録日 | 点数 | 的中 | 的中率 | 回収率 | 90%区間 | 収支 | 最大連敗(点) |')
    L.append('|---|---|---|---:|---:|---:|---:|---|---:|---:|')
    for s in strategies:
        for snap in s.get('snaps', ['C-2']):
            g = [r for r in ledger if r['strategy'] == s['id'] and r['snap'] == snap]
            n = len(g)
            stake = [float(r['stake']) for r in g]
            ret = [float(r['payoff']) * float(r['stake']) / 100 for r in g]
            hits = sum(x > 0 for x in ret)
            roi = sum(ret) / sum(stake) if stake else float('nan')
            lo, hi = roi_ci(stake, ret, [r['race_id'] for r in g]) if g else (np.nan, np.nan)
            streak = best = 0
            for x in sorted(g, key=lambda r: (r['date'], r['race_id'])):
                streak = 0 if float(x['payoff']) > 0 else streak + 1
                best = max(best, streak)
            f = lambda x: '-' if x != x else f'{x * 100:.1f}%'
            L.append(f'| {s["id"]}（{s["label"]}） | {snap} | {s["registered"]} | {n} | {hits} | {f(hits / n) if n else "-"} | '
                     f'**{f(roi)}** | {f(lo)}〜{f(hi)} | {int(sum(ret) - sum(stake)):+,}円 | {best} |')
    L.append('\n※ 点数が数百に届くまでは、回収率は運で大きく上下します。区間の下限が100%を超えて初めて「勝てる」と言えます。')
    return '\n'.join(L) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True, help='データ用リポジトリの data フォルダ')
    ap.add_argument('--strategies', default=os.path.join(HERE, 'strategies.json'))
    ap.add_argument('--redo', action='store_true', help='台帳を作り直す')
    ap.add_argument('--today', default=None, help='今日（JST、YYYYMMDD）。今日以降の分は、その日が終わるまで台帳に入れない')
    a = ap.parse_args(argv)
    data = Path(a.data)
    strategies = json.load(open(a.strategies, encoding='utf-8'))['strategies']
    model = E.load_model()
    out_dir = data / 'ledger'
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path, meta_path, calib_path = out_dir / 'ledger.csv', out_dir / 'days.json', out_dir / 'calib.json'
    drift = DR.load_table(str(out_dir / 'drift.json'))
    ledger = [] if a.redo or not ledger_path.exists() else read_csv(ledger_path)
    days = {} if a.redo or not meta_path.exists() else json.loads(meta_path.read_text(encoding='utf-8'))
    calib_all = {} if a.redo or not calib_path.exists() else json.loads(calib_path.read_text(encoding='utf-8'))
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    today = a.today or _dt.now(_tz(_td(hours=9))).strftime('%Y%m%d')
    for p in sorted(glob.glob(str(data / '*' / 'odds_snap' / 'snap_*.csv'))):
        date = Path(p).stem.split('_')[1]
        if date in days:
            continue
        n_snap = len({r['race_id'] for r in read_csv(p)})
        calib = dict(bands={}, ll=[])
        rows, n_races = evaluate_day(model, strategies, data, date, drift, calib)
        if n_races == 0:
            print(f'{date}: 結果がまだ無いため保留')
            continue
        if date >= today and n_races < n_snap:  # 途中で結果を集めた日は、全レースの結果がそろうか日が変わるまで入れない（一度入れた日は数え直さないため）
            print(f'{date}: 結果がそろっていないため保留（{n_races} / {n_snap}レース）')
            continue
        ledger.extend(rows)
        days[date] = n_races
        calib_all[date] = calib
        print(f'{date}: 判定 {n_races}レース / 買い目 {len(rows)}点')
    with open(ledger_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        w.writeheader()
        w.writerows(ledger)
    meta_path.write_text(json.dumps(days, indent=1, sort_keys=True), encoding='utf-8')
    calib_path.write_text(json.dumps(calib_all, sort_keys=True), encoding='utf-8')
    (out_dir / 'report.md').write_text(report(ledger, strategies, days), encoding='utf-8')
    print(f'台帳 {len(ledger)}点 / {len(days)}日')


if __name__ == '__main__':
    main()
