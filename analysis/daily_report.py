#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
毎朝の検証レポート（期待値ボードの 📊 に出す）:  python3 analysis/daily_report.py --data _data_repo/data

  入力（データ用リポジトリ内）:
    data/ledger/ledger.csv・days.json・calib.json（snapshot_ledger.py）、data/bets/（［賭けた］の記録と精算）、data/YYYY/races.csv
  出力: data/ledger/daily_report.json（期待値ボードが暗号化して載せる。公開リポジトリには入れない）

  * 前日の「ボードの推奨（REC）」と結果、買い方ごとの累計・直近7日の成績
  * 実際に賭けた分と、推奨を全部買った場合の比較（推奨外の記録の点数も）
  * 異常の自動チェック（⚠）: 運では説明できない大きさのずれだけを知らせる。ロジックを直すのは ⚠ が出た時だけ
      - 予想の確率と実際の的中のずれ（2車複・ワイド、期待値の帯ごと）: |z| ≥ 3
      - 融合した確率がオッズだけより当たっていない（2車複の的中組の対数損失、直近7日）: z ≥ 3
      - 買い方の負け確定（200点以上で 90%区間の上限 < 100%）
      - 記録漏れ（その日のレースの 70% 未満しか判定できていない）、精算の遅れ（2日以上前の記録が結果待ち）
"""
import argparse
import csv
import glob
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import snapshot_ledger as SL  # noqa: E402

JST = timezone(timedelta(hours=9))
Z_ALERT = 3.0


def read_csv(path):
    try:
        with open(path, newline='', encoding='utf-8-sig') as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def load_json(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except (OSError, ValueError):
        return default


def summarize(rows):
    stake = [float(r['stake']) for r in rows]
    ret = [float(r['payoff']) * float(r['stake']) / 100 for r in rows]
    n, hits = len(rows), sum(x > 0 for x in ret)
    roi = sum(ret) / sum(stake) if stake else None
    lo, hi = SL.roi_ci(stake, ret, [r['race_id'] for r in rows]) if rows else (None, None)
    streak = worst = 0
    for x in sorted(rows, key=lambda r: (r['date'], r['race_id'])):
        streak = 0 if float(x['payoff']) > 0 else streak + 1
        worst = max(worst, streak)
    return dict(n=n, hits=hits, stake=sum(stake), ret=sum(ret), roi=roi, lo=lo, hi=hi, worst=worst)


def calib_check(calib, dates):
    """{帯: [点数, 予想の的中数, 実際の的中数, z]}（指定した日の合計）"""
    agg = defaultdict(lambda: [0, 0.0, 0, 0.0])
    for d in dates:
        for k, v in (calib.get(d) or {}).get('bands', {}).items():
            for i in range(4):
                agg[k][i] += v[i]
    out = {}
    for k, (n, ep, hit, var) in sorted(agg.items()):
        out[k] = [n, round(ep, 1), hit, round((hit - ep) / math.sqrt(var), 2) if var > 0 else 0.0]
    return out


def ll_check(calib, dates):
    d = [f - q for day in dates for f, q in (calib.get(day) or {}).get('ll', [])]
    if len(d) < 30:
        return dict(n=len(d), diff=None, z=None)
    m = sum(d) / len(d)
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (len(d) - 1))
    return dict(n=len(d), diff=round(m, 4), z=round(m / (sd / math.sqrt(len(d))), 2) if sd > 0 else 0.0)


# 提案のあり方の比較（探索）: 候補（cands.csv）から、いろいろな選び方を同じ日々で比べる。
# 実際の回収率は運の影響が大きいので、「確定オッズでの期待値」（確率 × 実際の確定オッズ）と、的中の予想と実際も並べる
VARIANTS = [
    ('REC', '推奨（確定予想EV1.10以上・300倍以下）', dict(ev_d=1.1, omax=300)),
    ('REC2', '推奨v2（＋100倍以下・EV2.0以下・強い1番人気は軸）', dict(ev_d=1.1, omax=100, ev_d_max=2.0, fav=True)),
    ('D100', '確定予想EV1.0以上・100倍以下', dict(ev_d=1.0, omax=100)),
    ('D105', '確定予想EV1.05以上・100倍以下', dict(ev_d=1.05, omax=100)),
    ('D120', '確定予想EV1.2以上・100倍以下', dict(ev_d=1.2, omax=100)),
    ('D110_30', '確定予想EV1.10以上・30倍以下', dict(ev_d=1.1, omax=30)),
    ('D110_Q', '2車複だけ・確定予想EV1.10以上・100倍以下', dict(ev_d=1.1, omax=100, bt='2車複')),
    ('D110_W', 'ワイドだけ・確定予想EV1.10以上・100倍以下', dict(ev_d=1.1, omax=100, bt='ワイド')),
    ('TOP1', '各レース1点（確定予想EVが最も高い組・1.10以上・100倍以下）', dict(ev_d=1.1, omax=100, top1=True)),
    ('N110', '今のオッズでEV1.10以上・300倍以下（下落を見込まない）', dict(ev_now=1.1, omax=300)),
    ('ALL', '全部（比較の基準）', dict(omax=300)),
]


def variants(data: Path, dates, snap='C-2'):
    import csv as _csv
    p = data / 'ledger' / 'cands.csv'
    try:
        rows = [r for r in _csv.DictReader(open(p, encoding='utf-8')) if r['snap'] == snap and r['date'] in set(dates)]
    except OSError:
        return []
    for r in rows:
        for k in ('prob', 'odds', 'ev_now', 'ev_d', 'fav_win', 'payoff'):
            r[k] = float(r[k] or 0)
        r['odds_final'] = float(r['odds_final']) if r['odds_final'] not in ('', None) else None
        r['has_fav'] = r['has_fav'] in ('1', 1, True)
    out = []
    for vid, label, c in VARIANTS:
        sel = [r for r in rows if r['ev_d'] >= c.get('ev_d', -1) and r['ev_now'] >= c.get('ev_now', -1) and r['odds'] <= c.get('omax', 1e9)
               and r['ev_d'] <= c.get('ev_d_max', 1e9) and (not c.get('bt') or r['bet_type'] == c['bt'])
               and (not c.get('fav') or r['fav_win'] < 0.45 or r['has_fav'])]
        if c.get('top1'):
            best = {}
            for r in sel:
                if r['race_id'] not in best or r['ev_d'] > best[r['race_id']]['ev_d']:
                    best[r['race_id']] = r
            sel = list(best.values())
        n = len(sel)
        if not n:
            out.append(dict(id=vid, label=label, n=0))
            continue
        ret = sum(r['payoff'] for r in sel) / 100.0
        hits = sum(1 for r in sel if r['payoff'] > 0)
        exp_hits = sum(r['prob'] for r in sel)
        fin = [r['prob'] * r['odds_final'] for r in sel if r['odds_final']]
        lo, hi = SL.roi_ci([1.0] * n, [r['payoff'] / 100.0 for r in sel], [r['race_id'] for r in sel])
        out.append(dict(id=vid, label=label, n=n, hits=hits, exp_hits=round(exp_hits, 2), roi=round(ret / n, 4), lo=lo, hi=hi,
                        ev_final=round(sum(fin) / len(fin), 4) if fin else None, avg_odds=round(sum(r['odds'] for r in sel) / n, 1),
                        days=len({r['date'] for r in sel})))
    return out


def race_counts(data: Path):
    n = defaultdict(int)
    for p in glob.glob(str(data / '20??' / 'races.csv')):
        for r in read_csv(p):
            if str(r.get('cancel', '0')) in ('0', '', '0.0'):
                n[str(r.get('date', ''))] += 1
    return n


def load_bets(data: Path):
    bets = []
    for p in sorted(glob.glob(str(data / 'bets' / 'bets_*.json'))):
        bets += [b for b in load_json(p, []) if not b.get('deleted')]
    return bets, load_json(data / 'bets' / 'settled.json', {})


def build(data: Path, strategies, now=None):
    now = now or datetime.now(JST)
    led = data / 'ledger'
    ledger = read_csv(led / 'ledger.csv')
    days = load_json(led / 'days.json', {})
    calib = load_json(led / 'calib.json', {})
    dates = sorted(days)
    last = dates[-1] if dates else None
    recent = dates[-7:]
    alerts, notes = [], []

    # 前日の推奨と結果
    rec_rows = [r for r in ledger if r['strategy'] == 'REC' and r['snap'] == 'C-2']
    y_rows = [r for r in rec_rows if r['date'] == last]
    yesterday = dict(date=last, races=days.get(last, 0) if last else 0, sum=summarize(y_rows),
                     rows=[[r['venue'], int(r['race_id'][:2]), r['bet_type'], r['key'], float(r['odds']), float(r['ev']),
                            float(r['payoff'])] for r in sorted(y_rows, key=lambda r: (r['venue'], r['race_id']))][:60])

    # 買い方ごと（本番 C-2）: 累計と直近7日
    strat = []
    for s in strategies:
        g = [r for r in ledger if r['strategy'] == s['id'] and r['snap'] == 'C-2']
        a, w = summarize(g), summarize([r for r in g if r['date'] in recent])
        strat.append(dict(id=s['id'], label=s['label'], registered=s['registered'], all=a, week=w))
        if a['n'] >= 200 and a['hi'] is not None and a['hi'] < 1.0:
            alerts.append(f'{s["id"]}（{s["label"]}）: {a["n"]}点で回収率 {a["roi"] * 100:.0f}%、90%区間の上限も100%未満 → 負けている可能性が高い')

    # 実際に賭けた分 と 推奨を全部買った場合
    bets, settled = load_bets(data)
    rec_keys = {(r['race_id'], r['bet_type'], r['key']) for r in rec_rows}
    rec_dates = set(r['date'] for r in rec_rows)
    mine_rows, off = [], 0
    for b in bets:
        st = settled.get(b['id'])
        if st is None:
            if b.get('date') and b['date'] < (now - timedelta(days=2)).strftime('%Y%m%d'):
                alerts.append(f'{b["date"]} {b.get("venue", "")}{b.get("rn", "")}R の記録が2日以上「結果待ち」のまま（精算できていない）')
            continue
        if b.get('date') in rec_dates and (b['race_id'], b['bt'], b['key']) not in rec_keys:
            off += 1
        payoff = (st['payout'] / b['stake'] * 100) if st.get('result') != 'void' and b['stake'] else 0
        if st.get('result') != 'void':
            mine_rows.append(dict(date=b['date'], race_id=b['race_id'], stake=b['stake'], payoff=payoff))
    mine = dict(all=summarize(mine_rows), week=summarize([r for r in mine_rows if r['date'] in recent]),
                yesterday=summarize([r for r in mine_rows if r['date'] == last]), off_recs=off)
    if off:
        notes.append(f'推奨にない買い目の記録が {off}点あります（感情の買いになっていないか）')

    # 確率の当たり方
    cal_all, cal_week = calib_check(calib, dates), calib_check(calib, recent)
    for scope, cal in (('累計', cal_all), ('直近7日', cal_week)):
        for k, (n, ep, hit, z) in cal.items():
            if abs(z) >= Z_ALERT:
                alerts.append(f'確率のずれ（{scope}・{k}）: 予想 {ep:.0f}回 に対して実際 {hit}回（z={z:+.1f}）'
                              + (' → 予想が高すぎる' if z < 0 else ' → 予想が低すぎる'))
    ll = dict(all=ll_check(calib, dates), week=ll_check(calib, recent))
    if ll['week']['z'] is not None and ll['week']['z'] >= Z_ALERT:
        alerts.append(f'直近7日、融合した確率がオッズだけより当たっていない（z={ll["week"]["z"]:+.1f}）→ モデルの見直しが必要')

    # 記録漏れ
    rc = race_counts(data)
    for d in recent:
        if rc.get(d) and days.get(d, 0) < 0.7 * rc[d]:
            alerts.append(f'{d}: 判定できたレースが {days.get(d, 0)} / {rc[d]}（記録漏れ）')

    var = dict(last=variants(data, [last]) if last else [], week=variants(data, recent), all=variants(data, dates))
    return dict(generated=now.strftime('%Y-%m-%d %H:%M'), days=len(dates), first=dates[0] if dates else None, last=last, variants=var,
                yesterday=yesterday, strategies=strat, mine=mine, calib=dict(all=cal_all, week=cal_week), ll=ll,
                alerts=alerts, notes=notes,
                rule='1日の結果ではロジックを直さない（運の影響が大きい）。⚠ が出た時だけ原因を調べて直す。月1回、十分な点数で学習し直す')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--strategies', default=os.path.join(HERE, 'strategies.json'))
    a = ap.parse_args(argv)
    data = Path(a.data)
    strategies = json.load(open(a.strategies, encoding='utf-8'))['strategies']
    rep = build(data, strategies)
    out = data / 'ledger' / 'daily_report.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'検証レポート: {rep["days"]}日分 / ⚠ {len(rep["alerts"])}件')


if __name__ == '__main__':
    main()
