#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新しい特徴量の効き目を調べる:  python3 analysis/feature_study.py --data <収集フォルダ> [--data <別フォルダ>] [--start 202605]

手元の収集データ（出走表・着順）から選手ごとの出走履歴を作り、そのレースより前の情報だけで
  form      … 近況: 直近5走の 3着内率・1着率 と 4か月の率との差（調子の上向き/下向き）
  agari     … 近況: 直近5走の上がりタイムのレース内順位（速いほど +）
  gap       … 間隔: 前走からの日数（log）、35日以上あいたか
  fall      … 落車・失格明け（前走で 落車/失格/故障）
  bank      … バンク相性: 同じ周長（333/400/500）での 3着内率 − その選手の全体の 3着内率
  chem      … 連携実績: ライン内で直前の選手と過去に組んだ回数と、その時の「2人とも3着内」率
を作り、v105 のモデルに1グループずつ足して、月ごとのローリング予測（各月より前だけで学習）で
  ① モデル単独の対数損失  ② オッズと融合した後の対数損失（α・β は各モデルの予測で最尤推定）
を比べる。② が下がる＝市場が見落としている情報。出力は analysis/out/（公開リポジトリには入れない）。
"""
import argparse
import math
import os
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.optimize import minimize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import train_model as T  # noqa: E402

GROUPS = {
    'form': ['form_top3', 'form_win'],
    'agari': ['agari'],
    'gap': ['gap_log', 'gap_long'],
    'fall': ['after_fall'],
    'bank': ['bank_aff'],
    'chem': ['chem_rate', 'chem_n'],
    # 選手コメント（本人の作戦の宣言）と、ライン内の先輩・後輩・同県の関係
    'cmt': ['cmt_jiriki', 'cmt_jizai', 'cmt_kimezu', 'cmt_hojuu', 'cmt_follow'],
    'rel': ['lead_senior_behind', 'ban_same_pref', 'ban_junior_lead'],
}
import re as _re
JIRIKI = _re.compile(r'自力(?!自在)|自分で|前々|一人で|先行|逃げ')
JIZAI = _re.compile(r'自在|取れた位置|流れ見て|位置から|臨機応変')
KIMEZU = _re.compile(r'決めず')
FOLLOW = _re.compile(r'君|さん|番手|付い|追走|任せ')
KEYS = {'races': ['race_id'], 'cards': ['race_id', 'car_number'], 'results': ['race_id', 'car_number'],
        'payoffs': ['race_id', 'bet_type', 'combination'], 'odds': ['race_id', 'bet_type', 'combination']}


# ------------------------------------------------------------ 読み込み（複数フォルダを結合）
def load(roots):
    D = {}
    for r in roots:
        d = T.load_all(r)
        for k, v in d.items():
            D[k] = v if k not in D else pd.concat([D[k], v], ignore_index=True)
    for k, keys in KEYS.items():
        if k in D:
            D[k] = D[k].drop_duplicates(subset=[c for c in keys if c in D[k]], keep='last')
    return D


# ------------------------------------------------------------ 出走履歴 → 特徴量
def bank_of_venue(races):
    lap = pd.to_numeric(races['lap'], errors='coerce')
    mode = lap.groupby(races['venue_name']).agg(lambda s: s.mode().iloc[0] if s.notna().any() else 4)
    return mode.map({3: 500, 4: 400, 5: 333}).fillna(400).to_dict()


def starts_table(D):
    races = D['races'].copy()
    races['date'] = pd.to_numeric(races['date'], errors='coerce')
    bank = bank_of_venue(races)
    races['bank'] = races['venue_name'].map(bank)
    c = D['cards'].copy()
    c = c[pd.to_numeric(c['absent'], errors='coerce').fillna(0) == 0]
    res = D['results'][['race_id', 'car_number', 'order', 'accident', 'final_half']].copy()
    s = c[['race_id', 'car_number', 'player_id', 'third_rate', 'first_rate', 'is_single', 'line_order', 'line_position']].merge(
        res, on=['race_id', 'car_number'], how='left').merge(races[['race_id', 'date', 'bank']], on='race_id', how='inner')
    s['order'] = pd.to_numeric(s['order'], errors='coerce')
    s['third_rate'] = pd.to_numeric(s['third_rate'], errors='coerce')
    s['first_rate'] = pd.to_numeric(s['first_rate'], errors='coerce')
    s['car_number'] = pd.to_numeric(s['car_number'], errors='coerce')
    s['top3'] = s['order'].between(1, 3).astype(float)
    s['win'] = (s['order'] == 1).astype(float)
    s['fall'] = s['accident'].isin(['落車', '失格', '故障']).astype(float)
    fh = pd.to_numeric(s['final_half'], errors='coerce')
    s['agari_pct'] = fh.groupby(s['race_id']).rank(pct=True, ascending=False)  # 速いほど 1 に近い
    s['player_id'] = s['player_id'].astype(str)
    # ライン内で直前の選手
    s['line_position'] = pd.to_numeric(s['line_position'], errors='coerce')
    single = pd.to_numeric(s['is_single'], errors='coerce').fillna(1) == 1
    lead = s[~single][['race_id', 'line_order', 'line_position', 'player_id', 'top3']].copy()
    lead['line_position'] = lead['line_position'] + 1
    lead = lead.rename(columns={'player_id': 'prev_pid', 'top3': 'prev_top3'})
    s = s.merge(lead, on=['race_id', 'line_order', 'line_position'], how='left')
    s.loc[single, 'prev_pid'] = np.nan
    return s.sort_values(['date', 'race_id']).reset_index(drop=True)


def history_features(s):
    """各出走について、その日より前の出走だけから特徴量を作る（同じ日の他レースも使わない）"""
    feats = {}
    g_top3 = s['top3'].mean()
    by_p = defaultdict(list)   # pid → [(date, top3, win, agari_pct, fall, bank)]
    pair = defaultdict(lambda: [0, 0.0])  # (前の選手, 自分) → [回数, 2人とも3着内の回数]
    bank_hist = defaultdict(lambda: [0, 0.0])  # (pid, bank) → [回数, 3着内]
    pend_p, pend_pair, pend_bank = [], [], []
    cur = None
    for row in s.itertuples(index=False):
        if row.date != cur:  # 日付が変わったら、前の日の結果を履歴に反映
            for k, v in pend_p:
                by_p[k].append(v)
            for k, both in pend_pair:
                pair[k][0] += 1
                pair[k][1] += both
            for k, t3 in pend_bank:
                bank_hist[k][0] += 1
                bank_hist[k][1] += t3
            pend_p, pend_pair, pend_bank = [], [], []
            cur = row.date
        h = by_p.get(row.player_id, [])
        last5 = h[-5:]
        base3 = (float(row.third_rate) / 100.0) if row.third_rate == row.third_rate else g_top3
        base1 = (float(row.first_rate) / 100.0) if row.first_rate == row.first_rate else g_top3 / 3
        n5 = len(last5)
        form_top3 = (sum(x[1] for x in last5) + 2 * base3) / (n5 + 2) - base3
        form_win = (sum(x[2] for x in last5) + 2 * base1) / (n5 + 2) - base1
        ag = [x[3] for x in last5 if x[3] == x[3]]
        agari = (sum(ag) + 2 * 0.5) / (len(ag) + 2) - 0.5
        if h:
            gap = (pd.Timestamp(str(int(row.date))) - pd.Timestamp(str(int(h[-1][0])))).days
            gap_log, gap_long, after_fall = math.log1p(gap) - math.log1p(10), float(gap >= 35), h[-1][4]
        else:
            gap_log, gap_long, after_fall = 0.0, 0.0, 0.0
        bh = bank_hist.get((row.player_id, row.bank), [0, 0.0])
        all_n = len(h)
        all_r = (sum(x[1] for x in h) + 5 * base3) / (all_n + 5)
        bank_aff = (bh[1] + 5 * all_r) / (bh[0] + 5) - all_r
        if isinstance(row.prev_pid, str):
            pc = pair.get((row.prev_pid, row.player_id), [0, 0.0])
            chem_rate = (pc[1] + 3 * 0.25) / (pc[0] + 3) - 0.25
            chem_n = math.log1p(pc[0])
        else:
            chem_rate = chem_n = 0.0
        feats[(row.race_id, int(row.car_number))] = dict(
            form_top3=form_top3, form_win=form_win, agari=agari, gap_log=gap_log, gap_long=gap_long,
            after_fall=after_fall, bank_aff=bank_aff, chem_rate=chem_rate, chem_n=chem_n)
        pend_p.append((row.player_id, (row.date, row.top3, row.win, row.agari_pct, row.fall, row.bank)))
        pend_bank.append(((row.player_id, row.bank), row.top3))
        if isinstance(row.prev_pid, str):
            pend_pair.append(((row.prev_pid, row.player_id), float(row.top3 == 1 and row.prev_top3 == 1)))
    return feats


def card_features(D):
    """出走表から、コメントとライン内の関係の特徴量 {(race_id, 車番): {...}}"""
    c = D['cards'].copy()
    c = c[pd.to_numeric(c['absent'], errors='coerce').fillna(0) == 0]
    c['car'] = pd.to_numeric(c['car_number'], errors='coerce')
    c['pos'] = pd.to_numeric(c['line_position'], errors='coerce')
    c['term_n'] = pd.to_numeric(c['term'], errors='coerce')
    single = pd.to_numeric(c['is_single'], errors='coerce').fillna(1) == 1
    out = {}
    lines = {}
    for r in c[~single].itertuples(index=False):
        lines.setdefault((r.race_id, r.line_order), {})[r.pos] = r
    for r in c.itertuples(index=False):
        cm = str(r.comment) if isinstance(r.comment, str) else ''
        f = dict(cmt_jiriki=float(bool(JIRIKI.search(cm))), cmt_jizai=float(bool(JIZAI.search(cm)) and not JIRIKI.search(cm)),
                 cmt_kimezu=float(bool(KIMEZU.search(cm))), cmt_hojuu=float('補充' in cm), cmt_follow=float(bool(FOLLOW.search(cm))),
                 lead_senior_behind=0.0, ban_same_pref=0.0, ban_junior_lead=0.0)
        L = lines.get((r.race_id, r.line_order)) if isinstance(r.line_order, str) else None
        if L and r.pos == 1 and 2 in L:  # 先頭: 番手が先輩（期が小さい）
            b = L[2]
            if b.term_n == b.term_n and r.term_n == r.term_n and b.term_n < r.term_n:
                f['lead_senior_behind'] = 1.0
        if L and r.pos == 2 and 1 in L:  # 番手: 先頭と同県 / 先頭が後輩
            a = L[1]
            f['ban_same_pref'] = float(a.prefecture == r.prefecture)
            if a.term_n == a.term_n and r.term_n == r.term_n and a.term_n > r.term_n:
                f['ban_junior_lead'] = 1.0
        out[(r.race_id, int(r.car))] = f
    return out


def augment(races, feats, names):
    """R['X'] の右に特徴量を足した新しいレース一覧（中心化はレース内平均を引く）"""
    out = []
    for R in races:
        extra = np.array([[feats.get((R['race_id'], r['num']), {}).get(n, 0.0) for n in names] for r in R['riders']], float)
        extra = extra - extra.mean(axis=0) if len(names) else extra
        out.append(dict(R, X=np.hstack([R['X'], extra]) if len(names) else R['X']))
    return out


# ------------------------------------------------------------ 評価
def odds_q(D, race_ids):
    o = D['odds']
    o = o[(o['bet_type'] == '3連単') & o['race_id'].isin(race_ids)]
    if 'absent' in o:
        o = o[pd.to_numeric(o['absent'], errors='coerce').fillna(0) == 0]
    pay = D['payoffs']
    pay = pay[(pay['bet_type'] == '3連単') & pay['race_id'].isin(race_ids)]
    win = {(r, T.norm_combo('3連単', c)): float(p) / 100 for r, c, p in zip(pay['race_id'], pay['combination'], pay['payoff'])}
    out = defaultdict(dict)
    for r, c, od in zip(o['race_id'], o['combination'], pd.to_numeric(o['odds'], errors='coerce')):
        k = T.norm_combo('3連単', c)
        v = win.get((r, k), od)
        if v == v and v >= 1.0:
            out[r][k] = v
    return out


def evaluate(preds, oq):
    """preds: [(R, dist)] → モデル単独の対数損失、融合（α・β最尤）後の対数損失"""
    ll_m, rows = [], []
    for R, d in preds:
        key = tuple(R['top3'])
        ll_m.append(-math.log(max(d[key], 1e-12)))
        o3 = oq.get(R['race_id'])
        if not o3:
            continue
        keys = list(d)
        ks = ['-'.join(map(str, k)) for k in keys]
        if sum(k in o3 for k in ks) < 0.6 * len(ks):
            continue
        w = np.array([1.0 / o3[k] if k in o3 else np.nan for k in ks])
        w = np.where(np.isnan(w), np.nanmin(w) * 0.5, w)
        q = w / w.sum()
        p = np.array([d[k] for k in keys])
        p = p / p.sum()
        rows.append((np.log(np.maximum(p, 1e-12)), np.log(np.maximum(q, 1e-12)), keys.index(key)))

    def nll(ab):
        a, b = ab
        tot = 0.0
        for lp, lq, i in rows:
            u = a * lp + b * lq
            m = u.max()
            tot -= u[i] - m - math.log(np.exp(u - m).sum())
        return tot / len(rows)
    r = minimize(nll, np.array([0.05, 1.0]), method='Nelder-Mead', options=dict(xatol=1e-4, fatol=1e-6))
    mk = nll(np.array([0.0, 1.0]))
    return dict(model=float(np.mean(ll_m)), fused=float(r.fun), market=float(mk), alpha=float(r.x[0]), beta=float(r.x[1]),
                n=len(ll_m), n_fused=len(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', action='append', required=True)
    ap.add_argument('--start', type=int, default=202605)
    ap.add_argument('--groups', default='form,agari,gap,fall,bank,chem,cmt,rel')
    ap.add_argument('--out', default=os.path.join(HERE, 'out'))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    D = load(a.data)
    T.configure(T.ext_fill_rate(D['cards']) >= 0.9)
    base_features = list(T.RIDER_FEATURES)
    races = T.build_races(D)
    print(f'[data] レース {len(races)}（{min(R["date"] for R in races)}〜{max(R["date"] for R in races)}） {time.time() - t0:.0f}秒', flush=True)
    s = starts_table(D)
    feats = history_features(s)
    for k, v in card_features(D).items():
        feats.setdefault(k, {}).update(v)
    print(f'[hist] 出走 {len(s)} → 特徴量 {len(feats)} {time.time() - t0:.0f}秒', flush=True)
    oq = odds_q(D, {R['race_id'] for R in races})
    months = sorted({R['date'] // 100 for R in races if R['date'] // 100 >= a.start})

    variants = {'基準（v105）': []}
    for g in a.groups.split(','):
        variants[f'+{g}'] = GROUPS[g]
    variants['+全部'] = [n for g in a.groups.split(',') for n in GROUPS[g]]
    results = {}
    for name, names in variants.items():
        T.RIDER_FEATURES = base_features + names
        rs = augment(races, feats, names)
        preds = []
        for m in months:
            train = [R for R in rs if R['date'] // 100 < m]
            test = [R for R in rs if R['date'] // 100 == m]
            model = T.fit_model(train)
            preds += [(R, T.predict_dist(model, R)) for R in test]
            if name == '+全部' and m == months[-1]:
                coef = dict(zip(T.RIDER_FEATURES, model['w1']))
        results[name] = evaluate(preds, oq)
        r = results[name]
        print(f'  {name}: モデル {r["model"]:.4f} / 融合 {r["fused"]:.5f}（α={r["alpha"]:.3f}） / 市場 {r["market"]:.5f} '
              f'[{r["n"]}R, 融合 {r["n_fused"]}R] {time.time() - t0:.0f}秒', flush=True)
    T.RIDER_FEATURES = base_features

    b = results['基準（v105）']
    L = ['# 新しい特徴量の効き目（月ごとのローリング予測）\n']
    L.append(f'- 予測した月: {months[0]}〜{months[-1]}（各月より前のデータだけで学習）。評価 {b["n"]}R（融合は3連単オッズがそろう {b["n_fused"]}R）')
    L.append('- 値は3連単の実際の着順に付けた確率の対数損失（小さいほど良い）。融合は各モデルの予測とオッズを α・β 最尤で組み合わせた後の値')
    L.append('- **「融合の改善」がプラス＝市場（オッズ）が見落としている情報**。0.001 で的中着順の確率がおよそ0.1%上がる程度\n')
    L.append('| 追加した特徴量 | モデル単独 | 改善 | 融合後 | **融合の改善** | 融合の重み α |')
    L.append('|---|---:|---:|---:|---:|---:|')
    for name, r in results.items():
        L.append(f'| {name} | {r["model"]:.4f} | {b["model"] - r["model"]:+.4f} | {r["fused"]:.5f} | **{b["fused"] - r["fused"]:+.5f}** | {r["alpha"]:.3f} |')
    L.append(f'\n参考: 市場（オッズ）だけの対数損失 {b["market"]:.5f}\n')
    L.append('## 全部入りモデルの1着の係数（最終月の学習）\n')
    for k, v in coef.items():
        if k in variants['+全部']:
            L.append(f'- {k}: {v:+.3f}')
    path = os.path.join(a.out, 'feature_study.md')
    open(path, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
