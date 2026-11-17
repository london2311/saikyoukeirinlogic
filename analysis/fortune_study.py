#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
選手の運気（六星占術・四柱推命）と成績の関係を調べる（検証用。期待値の計算には使っていない）

  * 強さの物差しは「市場の1着確率」（確定オッズの3連単から、その選手が1着の組の 1/オッズ を足して正規化）
  * 1着確率の帯ごとの実際の割合を基準（期待）にして、運気の区分ごとに 実際 − 期待 と z を出す
    （オッズが付けた強さを差し引いた後に、運気で説明できる差が残るか。星人は生まれた日の干支で決まり、実力とはほぼ無関係）
  * 見るもの: 1着・3着内・本命の取りこぼし（1着確率45%以上が1着を逃す）・大穴の1着（5%以下が1着）・落車/失格/中止
  * 判定（事前に決めた基準）: 主に見るのは「大殺界の年」「運気点」「天中殺の日」の3つ。
    z が ±3 以上、かつ 前半（2〜6月）と後半（7月〜）の両方で同じ向き（|z| ≥ 1.5）の時だけ「傾向あり（要確認）」
    （区分が多いので、偶然でも ±2 くらいは出る）
  * 運気はその日の暦から毎回計算できるので、レースごとに保存はしない（生年月日だけ集めればよい）
"""
import argparse
import csv
import glob
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import fortune as F  # noqa: E402

ACCIDENTS = ('落車', '失格', '中止', '故障')
PRIMARY = ['大殺界の年', '運気点', '天中殺の日']
CATS = PRIMARY + ['六星・年', '六星・月', '六星・日', '六星・今週', '四柱・今日の通変星']
BINS = [0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.45, 0.6, 0.8, 1.01]
OUTCOMES = [
    ('win', '1着', lambda s: True, lambda s: s['order'] == 1),
    ('top3', '3着内', lambda s: True, lambda s: 1 <= s['order'] <= 3),
    ('fav_lose', '本命の取りこぼし（市場の1着確率45%以上が1着を逃す）', lambda s: s['p'] >= 0.45, lambda s: s['order'] != 1),
    ('long_win', '大穴の1着（市場の1着確率5%以下）', lambda s: s['p'] <= 0.05, lambda s: s['order'] == 1),
    ('accident', '落車・失格・中止', lambda s: True, lambda s: s['accident'] in ACCIDENTS),
]


def _rows(paths):
    for p in paths:
        try:
            with open(p, newline='', encoding='utf-8-sig') as f:
                yield from csv.DictReader(f)
        except OSError:
            continue


def market_win(data: Path):
    """race_id → {車番: 市場の1着確率}（確定オッズの3連単から）"""
    inv = defaultdict(lambda: defaultdict(float))
    paths = sorted(glob.glob(str(data / '*' / 'odds_*.csv'))) + sorted(glob.glob(str(data / '*' / 'odds' / 'odds_*.csv')))
    for r in _rows(paths):
        if r.get('bet_type') != '3連単':
            continue
        try:
            o = float(r['odds'])
        except (TypeError, ValueError, KeyError):
            continue
        if o > 0:
            inv[r['race_id']][int(str(r['combination']).split('-')[0])] += 1 / o
    out = {}
    for rid, d in inv.items():
        t = sum(d.values())
        if t > 0:
            out[rid] = {c: v / t for c, v in d.items()}
    return out


def score_val(sc):
    return '+3以上' if sc >= 3 else '+1〜2' if sc >= 1 else '0' if sc == 0 else '-1〜2' if sc >= -2 else '-3以下'


def cats_of(f):
    return {
        '大殺界の年': 'はい' if f['grade_year'] == '✕' else 'いいえ',
        '運気点': score_val(F.score(f)),
        '天中殺の日': 'はい' if f['tcs_day'] else 'いいえ',
        '六星・年': f['grade_year'] + f['year'],
        '六星・月': f['grade_month'] + f['month'],
        '六星・日': f['grade_day'] + f['day'],
        '六星・今週': f['week']['grade'],
        '四柱・今日の通変星': f['tsuhen_day'],
    }


def _bin(p):
    return next(i for i, b in enumerate(BINS) if p < b)


def starts_of(data: Path, birth, mkt):
    """1出走 = dict(p, order, accident, date, cats)。市場の1着確率がないレースは除く"""
    res = {}
    for r in _rows([data / 'results.csv'] + sorted(data.glob('*/results.csv'))):
        try:
            res[(r['race_id'], int(r['car_number']))] = (int(r['order'] or 0), r.get('accident', ''))
        except (TypeError, ValueError, KeyError):
            continue
    out = []
    for r in _rows([data / 'racecards.csv'] + sorted(data.glob('*/racecards.csv'))):
        if str(r.get('absent', 0)) in ('1', 'True', 'true'):
            continue
        m = mkt.get(r.get('race_id'))
        try:
            car = int(r['car_number'])
        except (TypeError, ValueError, KeyError):
            continue
        x = res.get((r.get('race_id'), car))
        if not m or x is None or x[1] == '欠車':
            continue
        d = str(r.get('schedule_id', ''))[:8]
        s = dict(p=m.get(car, 0.0), order=x[0], accident=x[1], date=d, cats=None)
        bd = birth.get(str(r.get('player_id', '')).zfill(6))
        on = F.parse_date(f'{d[:4]}-{d[4:6]}-{d[6:8]}') if len(d) == 8 else None
        if bd and on:
            s['cats'] = cats_of(F.fortune(bd, on))
        out.append(s)
    return out


def study(data: Path, birthdays=None, mkt=None, split='20260701'):
    data = Path(data)
    if birthdays is None:
        import rider_profile
        birthdays = rider_profile.load(data)
    birth = {k: F.parse_date(v.get('birth')) for k, v in birthdays.items() if v.get('birth')}
    birth = {k: v for k, v in birth.items() if v}
    if not birth:
        return None
    mkt = market_win(data) if mkt is None else mkt
    starts = starts_of(data, birth, mkt)
    # 基準: 市場の1着確率の帯ごとの、全出走での実際の割合（運気の有無にかかわらず）
    base = {}
    for key, _, cond, hit in OUTCOMES:
        cnt = defaultdict(lambda: [0, 0])
        for s in starts:
            if cond(s):
                c = cnt[_bin(s['p'])]
                c[0] += 1
                c[1] += hit(s)
        base[key] = {b: v[1] / v[0] for b, v in cnt.items() if v[0]}
    with_luck = [s for s in starts if s['cats']]
    sections = {}
    for key, label, cond, hit in OUTCOMES:
        acc = defaultdict(lambda: [0.0] * 9)  # n, 実際, 期待, 分散, （前半）実際・期待・分散, （後半）…  の簡略: 下で使う
        for s in with_luck:
            if not cond(s):
                continue
            e = base[key][_bin(s['p'])]
            h = 1.0 if hit(s) else 0.0
            half = 0 if s['date'] < split else 1
            for cat, val in s['cats'].items():
                a = acc[(cat, val)]
                a[0] += 1
                a[1] += h
                a[2] += e
                a[3] += e * (1 - e)
                a[4 + half * 2] += h - e
                a[5 + half * 2] += e * (1 - e)
        tab = {}
        for (cat, val), a in acc.items():
            z = (a[1] - a[2]) / math.sqrt(a[3]) if a[3] > 0 else 0.0
            z1 = a[4] / math.sqrt(a[5]) if a[5] > 0 else 0.0
            z2 = a[6] / math.sqrt(a[7]) if a[7] > 0 else 0.0
            tab.setdefault(cat, []).append([val, int(a[0]), round(a[1] / a[0], 4), round(a[2] / a[0], 4), round(z, 2), round(z1, 1), round(z2, 1)])
        sections[key] = dict(label=label, table={c: sorted(tab[c]) for c in CATS if c in tab})
    found = []
    for key, sec in sections.items():
        for cat in PRIMARY:
            for v in sec['table'].get(cat, []):
                if abs(v[4]) >= 3 and abs(v[5]) >= 1.5 and abs(v[6]) >= 1.5 and v[5] * v[6] > 0:
                    found.append(f'{sec["label"]}・{cat} {v[0]}: 実際 {v[2] * 100:.1f}% / 期待 {v[3] * 100:.1f}%（z {v[4]:+.1f}、前半 {v[5]:+.1f}・後半 {v[6]:+.1f}）')
    return dict(riders=len(birth), starts=len(with_luck), all_starts=len(starts), sections=sections, found=found, primary=PRIMARY,
                note='期待＝市場の1着確率（確定オッズ）が同じ帯の選手の実際の割合。z が ±3 以上で、前半（〜6月）・後半（7月〜）の両方で同じ向きの時だけ「傾向あり」')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', default='', help='結果を JSON で保存（非公開のデータ用リポジトリへ）')
    ap.add_argument('--print', action='store_true', help='表を画面に出す（ローカル用。Actions のログでは使わない）')
    a = ap.parse_args(argv)
    r = study(Path(a.data))
    if not r:
        print('生年月日のデータがまだありません')
        return
    if a.out:
        Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'選手 {r["riders"]}人・出走 {r["starts"]}回 / 傾向あり {len(r["found"])}件')
    if a.print:
        for f in r['found']:
            print('★', f)
        for key, sec in r['sections'].items():
            print('■', sec['label'])
            for cat, rows in sec['table'].items():
                print(' ', cat)
                for v in rows:
                    print(f'    {v[0]:<8} {v[1]:>6}回  実際 {v[2] * 100:5.1f}%  期待 {v[3] * 100:5.1f}%  z {v[4]:+5.1f}（前半 {v[5]:+.1f}・後半 {v[6]:+.1f}）')


if __name__ == '__main__':
    main()
