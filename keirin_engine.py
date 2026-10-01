#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keirin_engine.py — 競輪ロジック v105 の計算エンジン（Python版）

ブラウザ版（index.html の統合エンジン）と同じ計算を行う。通信はしない。
  1. 着順モデル（analysis/model_v104.json の係数）で全3連単の確率 p
  2. 3連単オッズの逆数を正規化して市場確率 q
  3. Benter型融合 f ∝ q^β · p^α（α・βは13,292レースで最尤推定した値）
  4. 全券種の確率を f から集計 → 融合EV = 確率 × オッズ
  5. 2車複 × 融合EV1.10以上（検証済み）を候補に、一括ケリーで賭け金を計算
  6. 的中率重視の買い目（券種ごとに当たる確率の上位3点）

入力は収集装置（keirin_collector.py）の parse_racecard / parse_odds の行形式。
"""
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'analysis'))
import train_model as T  # noqa: E402

MODEL_PATH = os.path.join(HERE, 'analysis', 'model_v104.json')

DEFAULTS = dict(
    ev_min=1.10,        # 候補にする融合EVの下限
    p_min=0.005,        # 候補にする確率の下限
    odds_max=300.0,     # 候補にするオッズの上限
    bet_types='validated',  # validated=2車複のみ（検証済み） / all=全券種
    kelly_frac=0.25,    # 1/4ケリー
    max_exposure=0.05,  # 1レースの最大投資比率
    bankroll=30000,     # 資金（円）
    unit=100,           # 購入単位（円）
    max_bets=6,         # 最大点数
)
EV_TYPES = ['3連単', '3連複', '2車単', '2車複', 'ワイド']
HIT_TYPES = ['ワイド', '2車複', '3連複', '2車単']


# ------------------------------------------------------------ モデル
def load_model(path=MODEL_PATH):
    with open(path, encoding='utf-8') as f:
        m = json.load(f)
    T.configure('b_rate' in m['rider_features'])
    if T.RIDER_FEATURES != m['rider_features'] or T.PAIR2_FEATURES != m.get('pair2_features', T.PAIR2_FEATURES):
        raise ValueError('モデルの特徴量定義が train_model.py と一致しません')
    return dict(json=m, w1=np.array(m['w1']), w2=np.array(m['w2']), w3=np.array(m['w3']), feat_idx=None,
                alpha=m.get('fusion_alpha') if m.get('fusion_type') == 'benter' else 0.03,
                beta=m.get('fusion_beta') if m.get('fusion_type') == 'benter' else 1.03)


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if x == x else default
    except (TypeError, ValueError):
        return default


def riders_from_cards(rows):
    """parse_racecard の行 → 学習時と同じ形の選手データ（欠車は除外）"""
    riders = []
    for r in sorted(rows, key=lambda x: int(x['car_number'])):
        if str(r.get('absent', 0)) in ('1', 'True', 'true'):
            continue
        single = str(r.get('is_single', '')) in ('1', '') or _f(r.get('line_size'), 1) <= 1
        rd = dict(num=int(r['car_number']), point=_f(r.get('race_point')), first=_f(r.get('first_rate')),
                  second=_f(r.get('second_rate')), third=_f(r.get('third_rate')), style=str(r.get('style') or ''),
                  line_id=('s%d' % int(r['car_number'])) if single else str(r.get('line_order')),
                  line_pos=1 if single else int(_f(r.get('line_position'), 1)),
                  line_size=1 if single else int(_f(r.get('line_size'), 1)),
                  pref=str(r.get('prefecture') or ''))
        rd.update(s=_f(r.get('standing_count')), b=_f(r.get('back_count')), k_nige=_f(r.get('escape_count')),
                  k_maku=_f(r.get('makuri_count')), k_sashi=_f(r.get('sashi_count')), k_mark=_f(r.get('mark_count')),
                  n4m=_f(r.get('first_count')) + _f(r.get('second_count')) + _f(r.get('third_count')) + _f(r.get('out_count')))
        riders.append(rd)
    return riders


def model_dist(model, riders, venue_name):
    R = dict(riders=riders, X=T.rider_matrix(riders, T.VENUE_PREFECTURE.get(venue_name)))
    d = T.predict_dist(model, R)
    return {f'{a}-{b}-{c}': p for (a, b, c), p in d.items()}


# ------------------------------------------------------------ オッズ
def odds_index(odds_rows):
    """parse_odds の行 → {券種: {組(正規化): 倍率}}（欠車・倍率なしは除外。ワイドは最低倍率）"""
    idx = {t: {} for t in EV_TYPES}
    for o in odds_rows:
        bt = o.get('bet_type')
        if bt not in idx or str(o.get('absent', 0)) in ('1', 'True', 'true'):
            continue
        od = _f(o.get('odds'), None)
        if od is None or od < 1.0:
            continue
        idx[bt].setdefault(T.norm_combo(bt, o['combination']), od)
    return idx


# ------------------------------------------------------------ 一括ケリー（ブラウザ版と同じ射影勾配法）
def multi_kelly(win_sets, odds_eff, f):
    B, N = len(win_sets), len(f)
    if not B:
        return []
    hit_by = [[] for _ in range(N)]
    for b, ws in enumerate(win_sets):
        for i in ws:
            hit_by[i].append(b)
    oe = np.array(odds_eff, float)

    def wealth(s):
        W = np.full(N, 1.0 - s.sum())
        for i in range(N):
            for b in hit_by[i]:
                W[i] += s[b] * oe[b]
        return W

    def growth(s):
        W = wealth(s)
        m = f > 0
        if (W[m] <= 0).any():
            return -math.inf
        return float((f[m] * np.log(W[m])).sum())

    s = np.zeros(B)
    g = growth(s)
    lr = 0.05
    for _ in range(3000):
        if lr <= 1e-7:
            break
        W = wealth(s)
        grad = np.zeros(B)
        for i in range(N):
            if f[i] <= 0:
                continue
            inv = f[i] / W[i]
            grad -= inv
            for b in hit_by[i]:
                grad[b] += inv * oe[b]
        nxt = np.maximum(0.0, s + lr * grad)
        S = nxt.sum()
        if S > 0.95:
            nxt *= 0.95 / S
        g2 = growth(nxt)
        if g2 >= g - 1e-15:
            moved = np.abs(nxt - s).sum()
            s, g = nxt, g2
            lr *= 1.2
            if moved < 1e-10:
                break
        else:
            lr *= 0.5
    return list(s)


# ------------------------------------------------------------ 解析本体
def _ticket_keys(o):
    a, b, c = o
    return {
        '3連単': [f'{a}-{b}-{c}'],
        '3連複': [T.norm_combo('3連複', f'{a}-{b}-{c}')],
        '2車単': [f'{a}-{b}'],
        '2車複': [T.norm_combo('2車複', f'{a}-{b}')],
        'ワイド': [T.norm_combo('ワイド', f'{x}-{y}') for x, y in ((a, b), (a, c), (b, c))],
    }


def analyze(model, cards, odds_rows, venue_name, settings=None):
    opt = dict(DEFAULTS, **(settings or {}))
    riders = riders_from_cards(cards)
    if len(riders) < 3:
        return dict(ok=False, reason='出走が3車未満です')
    pdist = model_dist(model, riders, venue_name)
    keys = list(pdist)
    outs = [tuple(int(x) for x in k.split('-')) for k in keys]
    p = np.array([pdist[k] for k in keys])
    p = p / p.sum()
    oi = odds_index(odds_rows)

    # 市場確率（3連単オッズの逆数を正規化）
    t3 = oi['3連単']
    cover = sum(1 for k in keys if k in t3) / len(keys)
    warnings = []
    if cover >= 0.6:
        w = np.array([1.0 / t3[k] if k in t3 else np.nan for k in keys])
        w = np.where(np.isnan(w), np.nanmin(w) * 0.5, w)
        q = w / w.sum()
        u = model['alpha'] * np.log(np.maximum(p, 1e-12)) + model['beta'] * np.log(np.maximum(q, 1e-12))
        f = np.exp(u - u.max())
        f = f / f.sum()
        market_ok = True
    else:
        q, f, market_ok = None, p, False
        warnings.append(f'3連単オッズが不足しています（{cover * 100:.0f}%）。市場との融合・EV計算は行わず、モデルのみで確率を出します。')

    # 券種ごとの確率
    probs = {t: {} for t in EV_TYPES}
    probs_q = {t: {} for t in EV_TYPES}
    win_of = {t: {} for t in EV_TYPES}
    for i, o in enumerate(outs):
        for t, ks in _ticket_keys(o).items():
            for k in ks:
                probs[t][k] = probs[t].get(k, 0.0) + f[i]
                if q is not None:
                    probs_q[t][k] = probs_q[t].get(k, 0.0) + q[i]
                win_of[t].setdefault(k, []).append(i)

    tickets = []
    if market_ok:
        for t in EV_TYPES:
            for k, od in oi[t].items():
                if k in probs[t]:
                    pr = probs[t][k]
                    tickets.append(dict(type=t, key=k, odds=od, prob=pr, prob_market=probs_q[t].get(k), ev=pr * od))

    # 候補 → 一括ケリー
    cands = [x for x in tickets if x['ev'] >= opt['ev_min'] and x['prob'] >= opt['p_min'] and x['odds'] <= opt['odds_max']
             and (opt['bet_types'] != 'validated' or x['type'] == '2車複')]
    cands = sorted(cands, key=lambda x: -x['ev'])[:15]
    s = multi_kelly([win_of[c['type']][c['key']] for c in cands], [c['odds'] for c in cands], f)
    bets = [dict(c, kelly=k) for c, k in zip(cands, s) if k > 1e-6]
    bets = sorted(bets, key=lambda x: -x['kelly'])[:opt['max_bets']]
    total = sum(b['kelly'] * opt['kelly_frac'] for b in bets)
    scale = opt['max_exposure'] / total if total > opt['max_exposure'] else 1.0
    for b in bets:
        b['stake'] = int(round(b['kelly'] * opt['kelly_frac'] * scale * opt['bankroll'] / opt['unit']) * opt['unit'])
    if bets and all(b['stake'] == 0 for b in bets):
        bets[0]['stake'] = opt['unit']
    bets = [b for b in bets if b['stake'] > 0]
    stake = sum(b['stake'] for b in bets)
    pay = np.zeros(len(outs))
    for b in bets:
        for i in win_of[b['type']][b['key']]:
            pay[i] += b['stake'] * b['odds']
    hit_prob = float(f[pay > 0].sum()) if bets else 0.0
    exp_ret = float((f * pay).sum())

    # 的中率重視
    hit_picks = {}
    for t in HIT_TYPES:
        top = sorted(probs[t].items(), key=lambda kv: -kv[1])[:3]
        hit_picks[t] = [dict(key=k, prob=pr, odds=oi[t].get(k), ev=(pr * oi[t][k]) if k in oi[t] else None) for k, pr in top]

    # 各車
    rider_rows = []
    for r in riders:
        n = r['num']
        idx_win = [i for i, o in enumerate(outs) if o[0] == n]
        idx_top3 = [i for i, o in enumerate(outs) if n in o]
        rider_rows.append(dict(num=n, win_model=float(p[idx_win].sum()), win_market=float(q[idx_win].sum()) if q is not None else None,
                               win=float(f[idx_win].sum()), top3=float(f[idx_top3].sum())))

    return dict(ok=True, market_ok=market_ok, coverage=cover, warnings=warnings, settings=opt,
                alpha=model['alpha'], beta=model['beta'], ken=not bets, bets=bets,
                portfolio=dict(stake=stake, exp_return=exp_ret, roi=exp_ret / stake if stake else 0.0, hit_prob=hit_prob,
                               max_pay=float(pay.max()) if bets else 0.0),
                hit_picks=hit_picks, riders=rider_rows,
                top_outcomes=[dict(key=keys[i], prob=float(f[i])) for i in np.argsort(-f)[:5]])


def format_text(res, title=''):
    """人が読む用の出力"""
    L = []
    if title:
        L.append(f'■ {title}')
    if not res.get('ok'):
        L.append(f'  計算できません: {res.get("reason")}')
        return '\n'.join(L)
    for w in res['warnings']:
        L.append(f'  ⚠ {w}')
    st = res['settings']
    if res['market_ok']:
        if res['ken']:
            L.append(f'  🛑 見送り（融合EV {st["ev_min"]:.2f} 以上の{"2車複" if st["bet_types"] == "validated" else "買い目"}なし）')
        else:
            P = res['portfolio']
            L.append(f'  ✅ 勝負レース  {len(res["bets"])}点 / 合計 ¥{P["stake"]:,}  （いずれか的中 {P["hit_prob"] * 100:.1f}% / 期待回収率 {P["roi"] * 100:.0f}%）')
            for b in res['bets']:
                L.append(f'     {b["type"]} {b["key"].replace("-", "=") if b["type"] != "2車単" and b["type"] != "3連単" else b["key"]:<7} '
                         f'{b["odds"]:>6.1f}倍  確率 {b["prob"] * 100:5.1f}%  融合EV {b["ev"]:.2f}  → ¥{b["stake"]:,}')
    L.append('  🎯 的中率重視（当たる確率の高い順）')
    for t, picks in res['hit_picks'].items():
        sep = '-' if t == '2車単' else '='
        items = [f'{h["key"].replace("-", sep)} {h["prob"] * 100:.1f}%' + (f'（{h["odds"]:.1f}倍）' if h['odds'] else '') for h in picks]
        L.append(f'     {t}: ' + ' / '.join(items))
    top = sorted(res['riders'], key=lambda r: -r['win'])
    L.append('  各車の1着率: ' + ' '.join(f'{r["num"]}番 {r["win"] * 100:.0f}%' for r in top))
    return '\n'.join(L)
