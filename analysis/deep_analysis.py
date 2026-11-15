#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
競輪 多角分析: 要素 × 買い方 × 回収率（発見期間で探し、検証期間で確かめる）

  python3 analysis/deep_analysis.py --data <収集データのフォルダ> [--data <別フォルダ> ...] [--split 20260901] [--out <出力先>]

1. 基礎統計: 券種別・人気別の回収率（市場の「本命-大穴バイアス」）
2. 選手要素の市場評価のズレ: 要素ごとに「実際の着内率 ÷ オッズが示す着内率」
   （場・得点順位・年齢・期・クラス・脚質・ライン位置・同県/同地区ライン・地元・S/B・決まり手・ギア・時間帯・開催日・天候・風…）
3. 買い方 × 条件の回収率: 人気順・ライン決着・得点順 × 3連単/3連複/2車単/2車複/ワイド × 条件（単独＋主要な2条件）
   発見期間で回収率100%以上・十分な点数のものを、検証期間でそのまま確認（90%信頼区間つき）
4. まとめ: 検証期間でも回収率100%以上だった買い方

払戻は実際の払戻データ（100円あたり）。オッズは結果ページの最終オッズ（人気順と市場確率に使用）。
"""
import argparse
import glob
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import train_model as T  # noqa: E402

TYPES = ['3連単', '3連複', '2車単', '2車複', 'ワイド']
# 地区（競輪の一般的な区分の目安）
REGION = {}
for reg, prefs in {
    '北日本': '北海道 青森 岩手 宮城 秋田 山形 福島',
    '関東': '茨城 栃木 群馬 埼玉 新潟 長野',
    '南関東': '千葉 東京 神奈川 静岡 山梨',
    '中部': '富山 石川 岐阜 愛知 三重',
    '近畿': '福井 滋賀 京都 大阪 兵庫 奈良 和歌山',
    '中国': '鳥取 島根 岡山 広島 山口',
    '四国': '徳島 香川 愛媛 高知',
    '九州': '福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 沖縄',
}.items():
    for p in prefs.split():
        REGION[p] = reg


# ------------------------------------------------------------ 読み込み
def read_kind(roots, kind):
    """収集フォルダ群から指定種類のCSVを集めて結合（後に並べたフォルダを優先）"""
    pats = {'races': 'races.csv', 'cards': 'racecards.csv', 'results': 'results.csv', 'payoffs': 'payoffs.csv'}
    keys = {'races': ['race_id'], 'cards': ['race_id', 'car_number'], 'results': ['race_id', 'car_number'],
            'payoffs': ['race_id', 'bet_type', 'combination']}
    frames = []
    for r in roots:
        for p in sorted(glob.glob(os.path.join(r, 'data', '20??', pats[kind]))):
            frames.append(pd.read_csv(p, encoding='utf-8-sig', dtype={'race_id': str, 'player_id': str, 'schedule_id': str}))
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates(subset=keys[kind], keep='last')


def read_odds(roots, race_ids):
    frames = []
    for r in roots:
        files = sorted(glob.glob(os.path.join(r, 'data', '20??', 'odds_*.csv'))) + \
            sorted(glob.glob(os.path.join(r, 'data', '20??', 'odds', 'odds_*.csv')))
        for p in files:
            df = pd.read_csv(p, encoding='utf-8-sig', dtype={'race_id': str, 'combination': str},
                             usecols=lambda c: c in ('race_id', 'bet_type', 'combination', 'odds', 'popularity', 'absent'))
            frames.append(df[df['race_id'].isin(race_ids) & df['bet_type'].isin(TYPES)])
    o = pd.concat(frames, ignore_index=True)
    o['odds'] = pd.to_numeric(o['odds'], errors='coerce')
    o['popularity'] = pd.to_numeric(o['popularity'], errors='coerce')
    if 'absent' in o:
        o = o[pd.to_numeric(o['absent'], errors='coerce').fillna(0) == 0]
    o['key'] = [T.norm_combo(b, c) for b, c in zip(o['bet_type'], o['combination'])]
    return o.drop_duplicates(subset=['race_id', 'bet_type', 'key'], keep='last')


def fix_odds(odds, payt):
    """旧形式の収集データは、的中組のオッズ欄に払戻金額（例 10350）が入っている（＝結果を知らないと分からない値）。
    的中組のオッズは 払戻÷100（確定オッズ）にそろえる。ワイドで外れた組の倍率がないレース（旧形式）は、
    ワイドのオッズを使わない（人気順は使う）。"""
    o = odds.merge(payt, on=['race_id', 'bet_type', 'key'], how='left')
    hit = o['payoff'].notna()
    nw = o['bet_type'] != 'ワイド'
    o.loc[hit & nw, 'odds'] = o.loc[hit & nw, 'payoff'] / 100.0
    w = o['bet_type'] == 'ワイド'
    cover = o[w & ~hit].groupby('race_id')['odds'].apply(lambda x: x.notna().mean())
    bad = set(cover[cover < 0.5].index) | (set(o.loc[w, 'race_id']) - set(cover.index))
    o.loc[w & o['race_id'].isin(bad), 'odds'] = np.nan
    # 新形式でも念のため: ワイド的中組のオッズが払戻金額そのものなら倍率に直す
    w_hit = w & hit & (o['odds'] >= 100) & ((o['odds'] - o['payoff']).abs() < 1e-6)
    o.loc[w_hit, 'odds'] = o.loc[w_hit, 'payoff'] / 100.0
    return o.drop(columns=['payoff'])


def num(s):
    return pd.to_numeric(s, errors='coerce')


def build(roots, split):
    races = read_kind(roots, 'races')
    races = races[num(races['cancel']).fillna(0) == 0].copy()
    cards = read_kind(roots, 'cards')
    cards = cards[num(cards['absent']).fillna(0) == 0].copy()
    res = read_kind(roots, 'results')
    pay = read_kind(roots, 'payoffs')

    # 着順（1〜3着がそろうレースのみ）
    res['order'] = num(res['order'])
    top = res[res['order'].between(1, 3)].sort_values(['race_id', 'order'])
    t3 = top.groupby('race_id')['car_number'].apply(lambda s: [int(x) for x in s])
    t3 = t3[t3.apply(len) == 3]
    races = races[races['race_id'].isin(t3.index)].copy()
    races['top3'] = races['race_id'].map(t3)
    races['date'] = num(races['date']).astype(int)
    races['month'] = races['date'] // 100
    races['period'] = np.where(races['date'] < split, '発見', '検証')

    # ---- レースの条件
    races['venue_pref'] = races['venue_name'].map(T.VENUE_PREFECTURE)
    races['venue_region'] = races['venue_pref'].map(REGION)
    hour = pd.to_datetime(races['start_at'], errors='coerce')
    hm = hour.dt.hour + hour.dt.minute / 60
    races['時間帯'] = pd.cut(hm, [0, 12, 16, 20.5, 24], labels=['モーニング', 'デイ', 'ナイター', 'ミッドナイト'], right=False).astype(str)
    last_day = races.groupby('cup_id')['day_index'].transform('max')
    races['開催日'] = np.where(races['day_index'] == 1, '初日', np.where(races['day_index'] == last_day, '最終日', '中日'))
    ws = num(races['wind_speed'])
    races['風'] = pd.cut(ws, [-1, 2, 4, 99], labels=['風弱(〜2m)', '風中(2〜4m)', '風強(4m〜)']).astype(str)
    races['天候'] = races['weather'].fillna('不明').astype(str)
    races['頭数'] = num(races['entries_number']).astype('Int64').astype(str) + '車'
    races['グレード'] = races['grade'].map({1: 'F2', 2: 'F1', 3: 'G3', 4: 'G2', 5: 'G1', 6: 'GP'}).fillna(races['grade'].astype(str))
    races['クラス'] = races['class'].astype(str)
    races['種別'] = races['race_type'].astype(str)
    races['line_type'] = races['race_id'].map(cards.groupby('race_id')['line_type'].first()).fillna('不明').astype(str)

    # ---- 選手
    c = cards.copy()
    for k in ('race_point', 'first_rate', 'second_rate', 'third_rate', 'age', 'term', 'gear_ratio', 'standing_count', 'back_count',
              'escape_count', 'makuri_count', 'sashi_count', 'mark_count', 'first_count', 'second_count', 'third_count', 'out_count',
              'line_position', 'line_size', 'is_single', 'car_number'):
        c[k] = num(c.get(k))
    c = c[c['race_id'].isin(races['race_id'])]
    c['single'] = (c['is_single'] == 1) | (c['line_size'].fillna(1) <= 1)
    c['pt_rank'] = c.groupby('race_id')['race_point'].rank(ascending=False, method='first')
    c['n4m'] = c[['first_count', 'second_count', 'third_count', 'out_count']].sum(axis=1)
    meta = races.set_index('race_id')
    c['venue_pref'] = c['race_id'].map(meta['venue_pref'])
    c['home'] = c['prefecture'] == c['venue_pref']
    c['region'] = c['prefecture'].map(REGION)
    # ライン内の前の選手（番手なら先頭）との同県・同地区
    lead = c[~c['single']][['race_id', 'line_order', 'line_position', 'prefecture', 'region', 'race_point', 'style', 'car_number']]
    prev = lead.copy()
    prev['line_position'] = prev['line_position'] + 1
    prev = prev.rename(columns={'prefecture': 'prev_pref', 'region': 'prev_region', 'race_point': 'prev_pt', 'style': 'prev_style', 'car_number': 'prev_car'})
    c = c.merge(prev, on=['race_id', 'line_order', 'line_position'], how='left')
    c['same_pref_prev'] = c['prev_pref'].notna() & (c['prev_pref'] == c['prefecture'])
    c['same_region_prev'] = c['prev_region'].notna() & (c['prev_region'] == c['region'])
    # ライン単位: 平均得点の順位（最強ライン=1）
    line_pt = c[~c['single']].groupby(['race_id', 'line_order'])['race_point'].mean().rename('line_pt').reset_index()
    line_pt['line_rank'] = line_pt.groupby('race_id')['line_pt'].rank(ascending=False, method='first')
    c = c.merge(line_pt, on=['race_id', 'line_order'], how='left')
    # 着順
    fin = res[['race_id', 'car_number', 'order']].copy()
    fin['car_number'] = num(fin['car_number'])
    c = c.merge(fin, on=['race_id', 'car_number'], how='left')
    c['win'] = (c['order'] == 1).astype(int)
    c['top2'] = c['order'].between(1, 2).astype(int)
    c['top3'] = c['order'].between(1, 3).astype(int)
    return races, c, pay


# ------------------------------------------------------------ 市場確率（3連単オッズ → 選手ごとの1着・2着内・3着内）
def market_rider(odds, pay, races):
    o = odds[odds['bet_type'] == '3連単'][['race_id', 'key', 'odds']].dropna()
    p3 = pay[pay['bet_type'] == '3連単'].copy()
    p3['key'] = [T.norm_combo('3連単', x) for x in p3['combination']]
    o = o.merge(p3[['race_id', 'key', 'payoff']], on=['race_id', 'key'], how='left')
    o['odds'] = np.where(o['payoff'].notna(), o['payoff'] / 100.0, o['odds'])  # 的中組は確定オッズ
    o = o[o['odds'] >= 1.0]
    o['inv'] = 1.0 / o['odds']
    o['q'] = o['inv'] / o.groupby('race_id')['inv'].transform('sum')
    parts = o['key'].str.split('-', expand=True).astype(int)
    rows = []
    for pos in range(3):
        d = pd.DataFrame({'race_id': o['race_id'].values, 'car_number': parts[pos].values, 'q': o['q'].values})
        d['pos'] = pos + 1
        rows.append(d)
    long = pd.concat(rows)
    m = long.pivot_table(index=['race_id', 'car_number'], columns='pos', values='q', aggfunc='sum', fill_value=0.0)
    out = pd.DataFrame({'q_win': m[1], 'q_top2': m[1] + m[2], 'q_top3': m[1] + m[2] + m[3]}).reset_index()
    return out


# ------------------------------------------------------------ 共通: 回収率の集計
def roi_stats(stake, ret, race_ids=None, n_boot=400, seed=0):
    """点数・的中・回収率・90%区間（レース単位のブートストラップ）"""
    n = len(stake)
    if n == 0:
        return dict(n=0, hits=0, roi=np.nan, lo=np.nan, hi=np.nan)
    stake = np.asarray(stake, float)
    ret = np.asarray(ret, float)
    roi = ret.sum() / stake.sum()
    if race_ids is not None:
        df = pd.DataFrame({'r': race_ids, 's': stake, 'p': ret}).groupby('r')[['s', 'p']].sum().values
    else:
        df = np.c_[stake, ret]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(df), size=(n_boot, len(df)))
    b = df[idx, 1].sum(axis=1) / df[idx, 0].sum(axis=1)
    return dict(n=n, hits=int((ret > 0).sum()), roi=roi, lo=float(np.percentile(b, 5)), hi=float(np.percentile(b, 95)))


def pct(x, d=1):
    return '-' if x is None or x != x else f'{x * 100:.{d}f}%'


# ------------------------------------------------------------ 買い目の作成
def payoff_table(pay):
    p = pay[pay['bet_type'].isin(TYPES)].copy()
    p['key'] = [T.norm_combo(b, c) for b, c in zip(p['bet_type'], p['combination'])]
    p['payoff'] = num(p['payoff'])
    return p.groupby(['race_id', 'bet_type', 'key'])['payoff'].max().reset_index()


def ticket_keys(bt, a, b, c=None):
    if bt == '3連単':
        return f'{a}-{b}-{c}'
    if bt == '2車単':
        return f'{a}-{b}'
    if bt in ('3連複',):
        return '-'.join(map(str, sorted((a, b, c))))
    return '-'.join(map(str, sorted((a, b))))


def make_tickets(races, c, odds, q3):
    """戦略ごとの買い目（1レース1点）を作る。列: race_id, 戦略, 券種, key, 属性..."""
    rows = []
    # (a) 人気順 1〜3番人気
    for bt in TYPES:
        o = odds[(odds['bet_type'] == bt) & odds['popularity'].between(1, 3)]
        o = o.sort_values('popularity').drop_duplicates(['race_id', 'popularity'])
        for k in (1, 2, 3):
            s = o[o['popularity'] == k]
            rows.append(pd.DataFrame({'race_id': s['race_id'], '戦略': f'{k}番人気', '券種': bt, 'key': s['key']}))
    # (b) ライン決着（最強ライン・2番目のライン）
    recs = []
    ln = c[~c['single'] & c['line_rank'].isin([1, 2])]
    piv = ln.pivot_table(index=['race_id', 'line_rank'], columns='line_position', values='car_number', aggfunc='first')
    attr = ln[ln['line_position'] == 2].set_index(['race_id', 'line_rank'])[['same_pref_prev', 'same_region_prev']]
    lead = ln[ln['line_position'] == 1].set_index(['race_id', 'line_rank'])[['style', 'age', 'term', 'pt_rank']]
    piv = piv.join(attr).join(lead).reset_index()
    for r in piv.to_dict('records'):
        a, b = r.get(1), r.get(2)
        if a != a or b != b or a is None or b is None:
            continue
        a, b = int(a), int(b)
        nm = '最強ライン' if r['line_rank'] == 1 else '2番手ライン'
        base = dict(race_id=r['race_id'], 同県ライン='同県' if r['same_pref_prev'] is True else '別県',
                    同地区ライン='同地区' if r['same_region_prev'] is True else '別地区', 先頭脚質=str(r['style']),
                    先頭年齢=age_band(r['age']), 先頭期=term_band(r['term']),
                    先頭得点順位=f'得点{int(r["pt_rank"])}位' if r['pt_rank'] == r['pt_rank'] else '不明')
        for bt, strat, key in (('2車単', f'{nm} 先頭→番手', ticket_keys('2車単', a, b)), ('2車単', f'{nm} 番手→先頭', ticket_keys('2車単', b, a)),
                               ('2車複', f'{nm} 先頭=番手', ticket_keys('2車複', a, b)), ('ワイド', f'{nm} 先頭=番手', ticket_keys('ワイド', a, b))):
            recs.append(dict(base, 戦略=strat, 券種=bt, key=key))
        t = r.get(3)
        if t is not None and t == t:
            t = int(t)
            recs.append(dict(base, 戦略=f'{nm} 先頭-番手-3番手', 券種='3連単', key=ticket_keys('3連単', a, b, t)))
            recs.append(dict(base, 戦略=f'{nm} 3人', 券種='3連複', key=ticket_keys('3連複', a, b, t)))
    # (c) 得点順
    cc = c[c['pt_rank'] <= 3].pivot_table(index='race_id', columns='pt_rank', values='car_number', aggfunc='first').dropna()
    for rid, a, b, t in zip(cc.index, cc[1.0].astype(int), cc[2.0].astype(int), cc[3.0].astype(int)):
        recs += [dict(race_id=rid, 戦略='得点1位→2位', 券種='2車単', key=ticket_keys('2車単', a, b)),
                 dict(race_id=rid, 戦略='得点1位=2位', 券種='2車複', key=ticket_keys('2車複', a, b)),
                 dict(race_id=rid, 戦略='得点1位=2位', 券種='ワイド', key=ticket_keys('ワイド', a, b)),
                 dict(race_id=rid, 戦略='得点1-2-3位', 券種='3連単', key=ticket_keys('3連単', a, b, t)),
                 dict(race_id=rid, 戦略='得点1=2=3位', 券種='3連複', key=ticket_keys('3連複', a, b, t))]
    rows.append(pd.DataFrame(recs))
    tk = pd.concat(rows, ignore_index=True)
    # (d) 3連単基準の割安（モデルなし）: 3連単から見た確率 × その券種のオッズ ≥ 1.10 の組をすべて
    mk = []
    for bt in ('2車単', '2車複', '3連複', 'ワイド'):
        pr = q3[bt]
        o = odds[odds['bet_type'] == bt][['race_id', 'key', 'odds']].merge(pr, on=['race_id', 'key'])
        o = o[o['odds'].notna()]
        o = o[o['q'] * o['odds'] >= 1.10]
        mk.append(pd.DataFrame({'race_id': o['race_id'], '戦略': '3連単基準の割安(EV1.1+)', '券種': bt, 'key': o['key']}))
    tk = pd.concat([tk] + mk, ignore_index=True)
    return tk


def age_band(a):
    if a != a:
        return '不明'
    return '〜29歳' if a < 30 else '30代' if a < 40 else '40代' if a < 50 else '50歳〜'


def term_band(t):
    if t != t:
        return '不明'
    return '〜89期' if t < 90 else '90〜99期' if t < 100 else '100〜109期' if t < 110 else '110期〜'


def q_by_type(odds, pay):
    """3連単の市場確率 q を各券種の組に集計"""
    o = odds[odds['bet_type'] == '3連単'][['race_id', 'key', 'odds']].dropna()
    p3 = pay[pay['bet_type'] == '3連単'][['race_id', 'key', 'payoff']]
    o = o.merge(p3, on=['race_id', 'key'], how='left')
    o['odds'] = np.where(o['payoff'].notna(), o['payoff'] / 100.0, o['odds'])
    o = o[o['odds'] >= 1.0]
    o['q'] = (1 / o['odds']) / (1 / o['odds']).groupby(o['race_id']).transform('sum')
    parts = o['key'].str.split('-', expand=True)
    a, b, cc = parts[0], parts[1], parts[2]
    out = {}
    srt = lambda x, y: np.where(x.astype(int) < y.astype(int), x + '-' + y, y + '-' + x)
    out['2車単'] = o.assign(key=a + '-' + b).groupby(['race_id', 'key'])['q'].sum().reset_index()
    out['2車複'] = o.assign(key=srt(a, b)).groupby(['race_id', 'key'])['q'].sum().reset_index()
    s3 = pd.DataFrame(np.sort(parts.astype(int).values, axis=1).astype(str))
    out['3連複'] = o.assign(key=(s3[0] + '-' + s3[1] + '-' + s3[2]).values).groupby(['race_id', 'key'])['q'].sum().reset_index()
    w = pd.concat([o.assign(key=srt(a, b)), o.assign(key=srt(a, cc)), o.assign(key=srt(b, cc))])
    out['ワイド'] = w.groupby(['race_id', 'key'])['q'].sum().reset_index()
    return out


# ------------------------------------------------------------ 分析本体
RACE_CONDS = ['venue_name', 'venue_region', 'グレード', 'クラス', '種別', '頭数', 'line_type', '時間帯', '開催日', '天候', '風']
COND_LABEL = {'venue_name': '場', 'venue_region': '場の地区', 'line_type': 'ライン構成'}
TICKET_CONDS = ['オッズ帯', '同県ライン', '同地区ライン', '先頭脚質', '先頭年齢', '先頭期', '先頭得点順位']
PAIR_CONDS = ['時間帯', 'グレード', '頭数', 'line_type', 'オッズ帯', '開催日', 'クラス', '同県ライン']


def odds_band(o):
    return pd.cut(o, [0, 5, 10, 20, 50, 100, 300, 1e9], labels=['〜5倍', '5〜10倍', '10〜20倍', '20〜50倍', '50〜100倍', '100〜300倍', '300倍〜'], right=False).astype(str)


def popularity_curve(odds, payt, races):
    o = odds.merge(payt, on=['race_id', 'bet_type', 'key'], how='left')
    o['ret'] = o['payoff'].fillna(0.0)
    o = o.merge(races[['race_id', 'period']], on='race_id')
    o = o[o['popularity'].notna()]
    o['人気帯'] = pd.cut(o['popularity'], [0, 1, 2, 3, 5, 10, 20, 50, 100, 1e9], labels=['1番人気', '2番人気', '3番人気', '4〜5', '6〜10', '11〜20', '21〜50', '51〜100', '101〜'], right=True).astype(str)
    out = o.groupby(['bet_type', '人気帯', 'period'], observed=True).agg(n=('ret', 'size'), hits=('ret', lambda x: (x > 0).sum()), ret=('ret', 'sum')).reset_index()
    out['roi'] = out['ret'] / (100 * out['n'])
    allr = o.groupby(['bet_type', 'period']).agg(n=('ret', 'size'), ret=('ret', 'sum')).reset_index()
    allr['roi'] = allr['ret'] / (100 * allr['n'])
    return out, allr


def rider_edges(c, races, mr):
    d = c.merge(mr, on=['race_id', 'car_number'], how='inner')
    meta = races.set_index('race_id')
    d['period'] = d['race_id'].map(meta['period'])
    for col in RACE_CONDS:
        d[col] = d['race_id'].map(meta[col])
    d['得点順位'] = 'レース内' + d['pt_rank'].astype(int).astype(str) + '位'
    d['年齢'] = d['age'].map(age_band)
    d['期'] = d['term'].map(term_band)
    d['級班'] = d['class'].map({1: 'S', 2: 'A', 3: 'L', 4: 'L'}).fillna('?') + d['group'].astype('Int64').astype(str) + '班'
    d['脚質'] = d['style'].astype(str)
    d['ライン位置'] = np.where(d['single'], '単騎', np.where(d['line_position'] == 1, '先頭', np.where(d['line_position'] == 2, '番手', '3番手以降')))
    d['ライン人数'] = np.where(d['single'], '単騎', d['line_size'].astype('Int64').astype(str) + '人ライン')
    d['ラインの強さ'] = np.where(d['single'], '単騎', d['line_rank'].map({1.0: '最強ライン', 2.0: '2番手ライン'}).fillna('3番手以下のライン'))
    d['前の選手と同県'] = np.where(d['line_position'] >= 2, np.where(d['same_pref_prev'], '同県の後ろ', '別県の後ろ'), '対象外')
    d['前の選手と同地区'] = np.where(d['line_position'] >= 2, np.where(d['same_region_prev'], '同地区の後ろ', '別地区の後ろ'), '対象外')
    d['地元'] = np.where(d['home'], '地元', '地元以外')
    rate = lambda x: x / d['n4m'].replace(0, np.nan)
    d['S回数率'] = pd.qcut(rate(d['standing_count']).rank(method='first'), 4, labels=['S少', 'S中下', 'S中上', 'S多']).astype(str)
    d['B回数率'] = pd.qcut(rate(d['back_count']).rank(method='first'), 4, labels=['B少', 'B中下', 'B中上', 'B多']).astype(str)
    d['逃げ決まり手率'] = pd.qcut(rate(d['escape_count']).rank(method='first'), 4, labels=['逃少', '逃中下', '逃中上', '逃多']).astype(str)
    d['ギア'] = np.where(d['gear_ratio'] >= 3.93, '3.93以上', np.where(d['gear_ratio'] >= 3.92, '3.92', '3.92未満'))
    d['市場の人気順'] = '市場' + d.groupby('race_id')['q_win'].rank(ascending=False, method='first').astype(int).clip(upper=7).astype(str) + '番手'
    factors = ['得点順位', '市場の人気順', '年齢', '期', '級班', '脚質', 'ライン位置', 'ライン人数', 'ラインの強さ', '前の選手と同県', '前の選手と同地区',
               '地元', 'S回数率', 'B回数率', '逃げ決まり手率', 'ギア'] + RACE_CONDS
    rows = []
    for f in factors:
        g = d.groupby([f, 'period']).agg(n=('top3', 'size'), w=('win', 'sum'), qw=('q_win', 'sum'), t3=('top3', 'sum'), q3=('q_top3', 'sum')).reset_index()
        for lv, gg in g.groupby(f):
            r = {'要素': COND_LABEL.get(f, f), '水準': lv}
            for _, x in gg.iterrows():
                p = x['period']
                r[f'{p}_n'] = int(x['n'])
                r[f'{p}_1着比'] = x['w'] / x['qw'] if x['qw'] > 0 else np.nan
                r[f'{p}_3着内比'] = x['t3'] / x['q3'] if x['q3'] > 0 else np.nan
                r[f'{p}_3着内z'] = (x['t3'] - x['q3']) / np.sqrt(max(x['q3'], 1e-9))
            rows.append(r)
    return pd.DataFrame(rows)


def strategy_table(tk, races, odds, payt):
    t = tk.merge(odds[['race_id', 'bet_type', 'key', 'odds']].rename(columns={'bet_type': '券種'}), on=['race_id', '券種', 'key'], how='left')
    t = t.merge(payt.rename(columns={'bet_type': '券種'}), on=['race_id', '券種', 'key'], how='left')
    t['ret'] = t['payoff'].fillna(0.0)
    meta = races.set_index('race_id')
    for col in RACE_CONDS + ['period', 'date']:
        t[col] = t['race_id'].map(meta[col])
    t['オッズ帯'] = odds_band(t['odds'])
    t['買い方'] = t['券種'] + '｜' + t['戦略']
    return t


def condition_scan(t, min_n=200):
    res = []
    conds = [('全体', None)] + [(COND_LABEL.get(cn, cn), cn) for cn in RACE_CONDS + TICKET_CONDS]
    t = t.assign(全体='全レース')
    for label, cn in conds:
        col = cn or '全体'
        if col not in t:
            continue
        g = t.dropna(subset=[col]).groupby(['買い方', col, 'period']).agg(n=('ret', 'size'), ret=('ret', 'sum'), hits=('ret', lambda x: (x > 0).sum())).reset_index()
        g['条件'] = label + '=' + g[col].astype(str)
        res.append(g.drop(columns=[col]))
    for i, c1 in enumerate(PAIR_CONDS):
        for c2 in PAIR_CONDS[i + 1:]:
            g = t.dropna(subset=[c1, c2]).groupby(['買い方', c1, c2, 'period']).agg(n=('ret', 'size'), ret=('ret', 'sum'), hits=('ret', lambda x: (x > 0).sum())).reset_index()
            g['条件'] = COND_LABEL.get(c1, c1) + '=' + g[c1].astype(str) + ' × ' + COND_LABEL.get(c2, c2) + '=' + g[c2].astype(str)
            res.append(g.drop(columns=[c1, c2]))
    a = pd.concat(res, ignore_index=True)
    w = a.pivot_table(index=['買い方', '条件'], columns='period', values=['n', 'ret', 'hits'], fill_value=0)
    w.columns = [f'{p}_{v}' for v, p in w.columns]
    w = w.reset_index()
    for p in ('発見', '検証'):
        w[f'{p}_roi'] = w[f'{p}_ret'] / (100 * w[f'{p}_n'].replace(0, np.nan))
    w = w[~w['条件'].str.contains('=nan|=不明|=None', regex=True)]
    return w


def filter_by_condition(t, kaikata, cond):
    inv = {v: k for k, v in COND_LABEL.items()}
    s = t[t['買い方'] == kaikata]
    for part in cond.split(' × '):
        lab, val = part.split('=', 1)
        col = inv.get(lab, lab)
        if col == '全体':
            continue
        s = s[s[col].astype(str) == val]
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', action='append', required=True, help='収集データのフォルダ（複数可。後ろを優先）')
    ap.add_argument('--split', type=int, default=20260901, help='この日付以降を検証期間にする')
    ap.add_argument('--out', default=os.path.join(HERE, 'out'))
    ap.add_argument('--min-n', type=int, default=200)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    races, c, pay = build(args.data, args.split)
    odds = read_odds(args.data, set(races['race_id']))
    payt = payoff_table(pay)
    odds = fix_odds(odds, payt)
    print(f'[data] レース {len(races)}（発見 {(races.period == "発見").sum()} / 検証 {(races.period == "検証").sum()}）', flush=True)
    q3 = q_by_type(odds, payt)
    mr = market_rider(odds, pay, races)
    pc, allr = popularity_curve(odds, payt, races)
    edges = rider_edges(c, races, mr)
    tk = make_tickets(races, c, odds, q3)
    t = strategy_table(tk, races, odds, payt)
    scan = condition_scan(t, args.min_n)
    edges.to_csv(os.path.join(args.out, 'rider_edges.csv'), index=False, encoding='utf-8-sig')
    scan.to_csv(os.path.join(args.out, 'strategy_scan.csv'), index=False, encoding='utf-8-sig')
    print('[scan] 買い方×条件 の組み合わせ数', len(scan), flush=True)

    d0, d1 = races['date'].min(), races['date'].max()
    L = ['# 競輪 多角分析レポート\n']
    L.append(f'- 対象: {d0}〜{d1} の {len(races):,}レース（出走のべ {len(c):,}人）')
    L.append(f'- **発見期間**（〜{args.split - 1}）: {(races.period == "発見").sum():,}R で傾向を探し、**検証期間**（{args.split}〜）: {(races.period == "検証").sum():,}R でそのまま確かめる')
    L.append('- 払戻は実際の払戻金（100円あたり）。人気・オッズは結果ページの最終オッズ。1点100円で計算。')
    L.append('- 条件を細かく分けるほど「偶然の高回収率」が増えます。**検証期間でも残ったものだけ**を信頼してください。\n')

    # 1. 基礎
    L.append('## 1. 基礎：券種ごとの回収率と「人気別」の回収率\n')
    L.append('全組み合わせを100円ずつ買った場合（＝その券種の実質的な払戻率）と、人気順ごとの回収率です。\n')
    L.append('| 券種 | 全組み合わせ | ' + ' | '.join(['1番人気', '2番人気', '3番人気', '4〜5', '6〜10', '11〜20', '21〜50', '51〜100', '101〜']) + ' |')
    L.append('|---|---:|' + '---:|' * 9)
    for bt in TYPES:
        a = allr[allr['bet_type'] == bt]
        tot = a['ret'].sum() / (100 * a['n'].sum())
        cells = []
        for b in ['1番人気', '2番人気', '3番人気', '4〜5', '6〜10', '11〜20', '21〜50', '51〜100', '101〜']:
            x = pc[(pc['bet_type'] == bt) & (pc['人気帯'] == b)]
            cells.append(pct(x['ret'].sum() / (100 * x['n'].sum())) if x['n'].sum() else '-')
        L.append(f'| {bt} | {pct(tot)} | ' + ' | '.join(cells) + ' |')
    L.append('\n※ ワイドは外れた組のオッズが10月以前は保存されていないため、全組み合わせの値は参考です。\n')

    # 2. 選手要素のズレ
    L.append('## 2. 選手の要素ごとの「市場評価とのズレ」\n')
    L.append('「3着内比」= 実際に3着以内に入った数 ÷ 3連単オッズから計算した3着以内の期待数。**1.00より大きいほど市場が過小評価**（人気より走る）。')
    e = edges.copy()
    e = e[e.get('発見_n', 0) >= 300]
    n_sig = int((e['発見_3着内z'].abs() >= 3).sum())
    L.append(f'発見期間で統計的に明確（|z|≥3）だったのは **{n_sig}件 / {len(e)}件**。'
             'ズレの大きい順に、検証期間の値と並べています（zは偶然では出にくさの目安。|z|≥2で「ややはっきり」）。\n')
    for title, asc in (('過小評価（人気より走る）', False), ('過大評価（人気ほど走らない）', True)):
        L.append(f'### {title}\n')
        L.append('| 要素 | 水準 | 発見:人数 | 発見:3着内比 | 発見:1着比 | 検証:人数 | **検証:3着内比** | 検証:1着比 |')
        L.append('|---|---|---:|---:|---:|---:|---:|---:|')
        sub = e[e['発見_3着内比'] > 1] if not asc else e[e['発見_3着内比'] < 1]
        for _, r in sub.sort_values('発見_3着内z', ascending=asc).head(15).iterrows():
            L.append(f'| {r["要素"]} | {r["水準"]} | {int(r["発見_n"]):,} | {r["発見_3着内比"]:.3f}（z={r["発見_3着内z"]:+.1f}） | {r["発見_1着比"]:.3f} | '
                     f'{int(r.get("検証_n", 0) or 0):,} | **{r.get("検証_3着内比", np.nan):.3f}** | {r.get("検証_1着比", np.nan):.3f} |')
        L.append('')

    # 3. 買い方ごとの全体成績
    L.append('## 3. 買い方ごとの成績（条件なし・全レース）\n')
    L.append('| 買い方 | 発見:点数 | 発見:的中率 | 発見:回収率 | 検証:点数 | 検証:的中率 | 検証:回収率 |')
    L.append('|---|---:|---:|---:|---:|---:|---:|')
    base = scan[scan['条件'] == '全体=全レース'].sort_values('発見_roi', ascending=False)
    for _, r in base.iterrows():
        L.append(f'| {r["買い方"]} | {int(r["発見_n"]):,} | {pct(r["発見_hits"] / max(r["発見_n"], 1))} | {pct(r["発見_roi"])} | '
                 f'{int(r["検証_n"]):,} | {pct(r["検証_hits"] / max(r["検証_n"], 1))} | **{pct(r["検証_roi"])}** |')

    # 4. 条件を絞った買い方（発見→検証）
    cand = scan[(scan['発見_n'] >= args.min_n) & (scan['発見_roi'] >= 1.0)].copy()
    n_tests = int((scan['発見_n'] >= args.min_n).sum())
    cand['ok'] = (cand['検証_n'] >= 50) & (cand['検証_roi'] >= 1.0)
    L.append('\n## 4. 条件を絞った買い方：発見期間で回収率100%以上 → 検証期間では？\n')
    L.append(f'- 調べた「買い方 × 条件」: {n_tests:,}通り（発見期間で{args.min_n}点以上あるもの）')
    L.append(f'- 発見期間で回収率100%以上: **{len(cand):,}通り** → うち検証期間（50点以上）でも100%以上: **{int(cand["ok"].sum()):,}通り**')
    L.append('- 仮に本当の実力がどれも80%前後でも、これだけ多く調べると偶然で100%を超えるものが必ず出ます。'
             '検証でも残り、かつ信頼区間の下限が高いものほど信頼できます。\n')
    # 予測力チェック: 発見期間の回収率の高さは、検証期間の回収率をどこまで予測するか
    v = scan[(scan['発見_n'] >= args.min_n) & (scan['検証_n'] >= 50)].copy()
    v['bin'] = pd.cut(v['発見_roi'], [0, 0.8, 0.9, 1.0, 1.1, 1.2, 1.5, 99], labels=['〜80%', '80〜90%', '90〜100%', '100〜110%', '110〜120%', '120〜150%', '150%〜'])
    g = v.groupby('bin', observed=True).agg(k=('検証_roi', 'size'), pass_=('検証_roi', lambda x: (x >= 1).mean()), ret=('検証_ret', 'sum'), n=('検証_n', 'sum'))
    L.append('### 予測力チェック：発見期間の回収率は、検証期間をどこまで当てるか\n')
    L.append('| 発見期間の回収率 | 組み合わせ数 | 検証期間で100%以上の割合 | **検証期間の合算回収率** |')
    L.append('|---|---:|---:|---:|')
    for b, r in g.iterrows():
        L.append(f'| {b} | {int(r["k"]):,} | {pct(r["pass_"])} | **{pct(r["ret"] / (100 * r["n"]))}** |')
    L.append('\n発見期間で選ばれなかった組み合わせ（90〜100%）でも、検証期間で100%を超えるものが一定の割合で出ます。'
             '下の一覧の多くは偶然の可能性が高く、**同じ傾向が複数の券種・条件で繰り返し出ているもの**を優先して見てください。\n')
    ok = cand[cand['ok']].copy()
    rows = []
    for _, r in ok.iterrows():
        s = filter_by_condition(t[t['period'] == '検証'], r['買い方'], r['条件'])
        st = roi_stats(np.full(len(s), 100.0), s['ret'].values, s['race_id'].values)
        rows.append(dict(r, 検証_lo=st['lo'], 検証_hi=st['hi']))
    ok = pd.DataFrame(rows) if rows else ok.assign(検証_lo=np.nan, 検証_hi=np.nan)
    L.append('### 検証期間でも回収率100%以上だった買い方（信頼区間の下限が高い順）\n')
    L.append('| 買い方 | 条件 | 発見:点数 | 発見:回収率 | 検証:点数 | 検証:的中 | **検証:回収率** | 検証:90%区間 |')
    L.append('|---|---|---:|---:|---:|---:|---:|---|')
    for _, r in ok.sort_values('検証_lo', ascending=False).head(40).iterrows():
        L.append(f'| {r["買い方"]} | {r["条件"]} | {int(r["発見_n"]):,} | {pct(r["発見_roi"])} | {int(r["検証_n"]):,} | {int(r["検証_hits"])} | '
                 f'**{pct(r["検証_roi"])}** | {pct(r["検証_lo"])}〜{pct(r["検証_hi"])} |')
    L.append('\n### 参考：発見期間の回収率が特に高かったもの（検証で崩れた例を含む）\n')
    L.append('| 買い方 | 条件 | 発見:点数 | 発見:回収率 | 検証:点数 | 検証:回収率 |')
    L.append('|---|---|---:|---:|---:|---:|')
    for _, r in cand.sort_values('発見_roi', ascending=False).head(25).iterrows():
        L.append(f'| {r["買い方"]} | {r["条件"]} | {int(r["発見_n"]):,} | {pct(r["発見_roi"])} | {int(r["検証_n"]):,} | {pct(r["検証_roi"])} |')

    path = os.path.join(args.out, 'deep_report.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print(f'[done] {path}')


if __name__ == '__main__':
    main()
