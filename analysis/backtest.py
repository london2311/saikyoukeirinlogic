#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
競輪ロジック 本格バックテスト（オッズ×結果がそろった全レース）

  python3 analysis/backtest.py --data /path/to/収集リポジトリ [--jobs 4]

手順（未来の情報を使わない「ウォークフォワード」方式）:
  1. テスト月 m ごとに、m より前の月だけで着順モデルを学習
  2. 直前の月で、市場（3連単オッズ）とモデルの融合重みを最尤推定
       f(着順) ∝ q^β · p^α    q = 3連単オッズの逆数を正規化, p = モデル確率
     （β>1 は市場の本命-大穴バイアス補正、α>0 はモデルが市場にない情報を持つことを意味する）
  3. テスト月の全券種について 融合確率 × 実オッズ = 融合EV を計算
  4. 買い方は「選定期間」（前半のテスト月）だけで選び、
     「検証期間」（後半のテスト月、一度も選定に使っていない）で成績を確認する

注意: オッズは結果ページに残る最終オッズ。実際に買う時点のオッズとはズレるため、
     実戦の回収率は本バックテストよりやや下がる可能性がある。
"""
import argparse
import json
import math
import os
import pickle
import sys
from collections import defaultdict
from multiprocessing import Pool

import numpy as np
import pandas as pd
from scipy.optimize import minimize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import train_model as T  # noqa: E402

EV_TYPES = ['3連単', '3連複', '2車単', '2車複']
ALL_TYPES = EV_TYPES + ['ワイド']

G = {}  # fork した子プロセスと共有するデータ


# ------------------------------------------------------------ データ
def load(data_root):
    D = T.load_all(data_root)
    T.configure(T.ext_fill_rate(D['cards']) >= 0.9)
    print(f'[features] 拡張特徴量: {"使用" if T.USE_EXT else "なし"} / 特徴量 {len(T.RIDER_FEATURES)}個')
    races = T.build_races(D)
    odds = D['odds']
    if 'absent' in odds:
        odds = odds[pd.to_numeric(odds['absent'], errors='coerce').fillna(0) == 0]
    odds = odds[odds['bet_type'].isin(EV_TYPES)]
    by = defaultdict(lambda: defaultdict(dict))
    for rid, bt, cmb, od in zip(odds['race_id'], odds['bet_type'], odds['combination'], odds['odds']):
        if od == od and od >= 1.0:
            by[rid][bt][T.norm_combo(bt, cmb)] = float(od)
    # 的中組は払戻/100（=確定オッズ）で上書き（旧形式では odds 列に払戻金額が入っている）
    for R in races:
        for bt in EV_TYPES:
            for cmb, pay, _ in R['payoffs'].get(bt, []):
                by[R['race_id']][bt][T.norm_combo(bt, cmb)] = pay / 100.0
    races = [R for R in races if len(by[R['race_id']].get('3連単', {})) > 0]
    for R in races:
        R['odds'] = {bt: dict(v) for bt, v in by[R['race_id']].items()}
        R['month'] = R['date'] // 100
    print(f'[data] オッズ×結果がそろったレース: {len(races)}')
    return races


def market_q(R, keys):
    o = R['odds']['3連単']
    w = np.array([1.0 / o[k] if k in o else np.nan for k in keys])
    mn = np.nanmin(w) if np.isfinite(w).any() else 1e-4
    w = np.where(np.isnan(w), mn * 0.5, w)
    return w / w.sum()


def race_arrays(model, R):
    d = T.predict_dist(model, R)
    keys = [f'{a}-{b}-{c}' for (a, b, c) in d]
    p = np.array(list(d.values()))
    q = market_q(R, keys)
    win = keys.index('-'.join(map(str, R['top3'])))
    return keys, p / p.sum(), q, win


# ------------------------------------------------------------ 融合重みの推定
def fit_fusion(rows, use_model=True):
    """rows: [(p, q, win)] → (α, β) を対数尤度最大化で推定"""
    def nll(w):
        al, be = (w[0], w[1]) if use_model else (0.0, w[0])
        f, g = 0.0, np.zeros(len(w))
        for p, q, win in rows:
            lp, lq = np.log(np.maximum(p, 1e-12)), np.log(np.maximum(q, 1e-12))
            u = al * lp + be * lq
            mx = u.max()
            e = np.exp(u - mx)
            z = e.sum()
            pr = e / z
            f -= u[win] - mx - math.log(z)
            if use_model:
                g -= np.array([lp[win] - pr @ lp, lq[win] - pr @ lq])
            else:
                g -= np.array([lq[win] - pr @ lq])
        return f, g
    w0 = np.array([0.3, 1.0]) if use_model else np.array([1.0])
    r = minimize(nll, w0, jac=True, method='L-BFGS-B')
    return (float(r.x[0]), float(r.x[1])) if use_model else (0.0, float(r.x[0]))


def fuse(p, q, al, be):
    u = al * np.log(np.maximum(p, 1e-12)) + be * np.log(np.maximum(q, 1e-12))
    e = np.exp(u - u.max())
    return e / e.sum()


# ------------------------------------------------------------ 1か月分のウォークフォワード
def run_month(m):
    races, months = G['races'], G['months']
    i = months.index(m)
    prev = months[i - 1]
    train_a = [R for R in races if R['month'] < prev]
    calib = [R for R in races if R['month'] == prev]
    mA = T.fit_model(train_a)
    rows = [race_arrays(mA, R)[1:] for R in calib]
    al, be = fit_fusion(rows)
    _, be_m = fit_fusion(rows, use_model=False)
    train_b = [R for R in races if R['month'] < m]
    mB = T.fit_model(train_b)
    out = []
    for R in races:
        if R['month'] != m:
            continue
        keys, p, q, win = race_arrays(mB, R)
        out.append(dict(race_id=R['race_id'], date=R['date'], keys=keys, p=p, q=q, win=win,
                        f=fuse(p, q, al, be), fm=fuse(p, q, 0.0, be_m)))
    print(f'  {m}: 学習 {len(train_b)} / 融合推定 {prev}({len(calib)}) α={al:.3f} β={be:.3f} 市場のみβ={be_m:.3f} / テスト {len(out)}', flush=True)
    return m, dict(alpha=al, beta=be, beta_market=be_m, n_train=len(train_b)), out


# ------------------------------------------------------------ 買い目の確率・EV
def tickets(rec, R, dist_key='f'):
    """券種ごとの {組: (確率, オッズ, 的中払戻)}"""
    f = rec[dist_key]
    acc = {bt: defaultdict(float) for bt in ALL_TYPES}
    for k, pr in zip(rec['keys'], f):
        a, b, c = k.split('-')
        acc['3連単'][k] += pr
        acc['3連複'][T.norm_combo('3連複', k)] += pr
        acc['2車単'][f'{a}-{b}'] += pr
        acc['2車複'][T.norm_combo('2車複', f'{a}-{b}')] += pr
        for x, y in ((a, b), (a, c), (b, c)):
            acc['ワイド'][T.norm_combo('ワイド', f'{x}-{y}')] += pr
    pays = {bt: {T.norm_combo(bt, c): pay for c, pay, _ in R['payoffs'].get(bt, [])} for bt in ALL_TYPES}
    out = {}
    for bt in ALL_TYPES:
        od = R['odds'].get(bt, {})
        out[bt] = {k: (pr, od.get(k), pays[bt].get(k, 0.0)) for k, pr in acc[bt].items()}
    return out


def summarize(bets):
    """bets: [(race_id, date, 賭け金, 払戻)] → 成績"""
    if not bets:
        return dict(bets=0, races=0, hits=0, stake=0, ret=0, roi=float('nan'), hit_rate=float('nan'), max_losing=0, ci=(float('nan'), float('nan')))
    bets = sorted(bets, key=lambda x: (x[1], x[0]))
    stake = sum(b[2] for b in bets)
    ret = sum(b[3] for b in bets)
    by_race = defaultdict(lambda: [0.0, 0.0])
    for rid, _, s, r in bets:
        by_race[rid][0] += s
        by_race[rid][1] += r
    # 最大連敗（レース単位、日付順）
    streak = mx = 0
    race_order = []
    seen = set()
    for b in bets:
        if b[0] not in seen:
            seen.add(b[0])
            race_order.append(b[0])
    for rid in race_order:
        if by_race[rid][1] > 0:
            streak = 0
        else:
            streak += 1
            mx = max(mx, streak)
    # レース単位ブートストラップで回収率の90%区間
    arr = np.array(list(by_race.values()))
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(arr), size=(1000, len(arr)))
    rois = arr[idx, 1].sum(axis=1) / arr[idx, 0].sum(axis=1)
    hits = sum(1 for b in bets if b[3] > 0)
    return dict(bets=len(bets), races=len(by_race), hits=hits, stake=stake, ret=ret, roi=ret / stake,
                hit_rate=hits / len(bets), race_hit_rate=float((arr[:, 1] > 0).mean()), max_losing=mx,
                ci=(float(np.percentile(rois, 5)), float(np.percentile(rois, 95))))


# ------------------------------------------------------------ メイン
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True, help='収集データのあるリポジトリのパス')
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--cache', default=os.path.join(T.OUT_DIR, 'backtest_cache.pkl'))
    ap.add_argument('--select-months', type=int, default=3, help='前半の何か月を買い方の選定に使うか')
    args = ap.parse_args()

    if os.path.exists(args.cache):
        with open(args.cache, 'rb') as f:
            C = pickle.load(f)
        T.configure(C['use_ext'])
        print(f'[cache] {args.cache} を使用')
    else:
        races = load(args.data)
        months = sorted({R['month'] for R in races})
        G['races'], G['months'] = races, months
        test_months = months[2:]
        print(f'[walk-forward] 月: {months} / テスト: {test_months}')
        with Pool(min(args.jobs, len(test_months))) as pool:
            res = pool.map(run_month, test_months)
        recs = {}
        fusion = {}
        for m, fu, out in res:
            fusion[m] = fu
            for r in out:
                recs[r['race_id']] = r
        slim = {R['race_id']: dict(race_id=R['race_id'], date=R['date'], month=R['month'], odds=R['odds'],
                                   payoffs=R['payoffs'], top3=R['top3'], grade=R['grade'], klass=R['klass'],
                                   race_type=R['race_type'], venue=R['venue'], n=len(R['riders']))
                for R in races if R['race_id'] in recs}
        C = dict(use_ext=T.USE_EXT, months=months, test_months=test_months, fusion=fusion, recs=recs, races=slim)
        with open(args.cache, 'wb') as f:
            pickle.dump(C, f)

    analyze(C, args.select_months)


def analyze(C, n_select):
    test_months = C['test_months']
    sel_months, hold_months = test_months[:n_select], test_months[n_select:]
    recs, races = C['recs'], C['races']
    print(f'[期間] 選定: {sel_months} / 検証: {hold_months}')

    # ---- 予測精度（実際の3連単に付けた確率の対数損失）
    ll = defaultdict(list)
    for rid, r in recs.items():
        n = races[rid]['n']
        ll['一様'].append(-math.log(n * (n - 1) * (n - 2)))
        ll['モデル単独'].append(math.log(max(r['p'][r['win']], 1e-12)))
        ll['市場（オッズ）'].append(math.log(max(r['q'][r['win']], 1e-12)))
        ll['市場（較正）'].append(math.log(max(r['fm'][r['win']], 1e-12)))
        ll['融合'].append(math.log(max(r['f'][r['win']], 1e-12)))
    acc = {k: -float(np.mean(v)) for k, v in ll.items()}

    # ---- 全買い目を一度だけ展開
    flat = []  # (month, race_id, date, bt, key, prob, odds, payoff, ev, prob_mkt)
    for rid, r in recs.items():
        R = races[rid]
        tk = tickets(r, R)
        tkm = tickets(r, R, 'fm')
        for bt in ALL_TYPES:
            for k, (pr, od, pay) in tk[bt].items():
                ev = pr * od if od else None
                flat.append((R['month'], rid, R['date'], bt, k, pr, od, pay, ev, tkm[bt][k][0]))
    df = pd.DataFrame(flat, columns=['month', 'race_id', 'date', 'bt', 'key', 'prob', 'odds', 'payoff', 'ev', 'prob_mkt'])
    df['hold'] = df['month'].isin(hold_months)
    df['sel'] = df['month'].isin(sel_months)
    df['ev_mkt'] = df['prob_mkt'] * df['odds']

    def bets_of(sub):
        return list(zip(sub['race_id'], sub['date'], [100] * len(sub), sub['payoff']))

    # ---- 回収率重視: EV戦略のグリッド（選定期間で選び、検証期間で確認）
    bands = [(1, 10), (10, 30), (30, 100), (100, 300), (300, 1e9), (1, 1e9)]
    grid = []
    evd = df[df['bt'].isin(EV_TYPES) & df['ev'].notna()]
    for bt in EV_TYPES:
        g_bt = evd[evd['bt'] == bt]
        for t in (1.0, 1.1, 1.2, 1.3, 1.5, 2.0):
            g_t = g_bt[g_bt['ev'] >= t]
            for lo, hi in bands:
                g = g_t[(g_t['odds'] >= lo) & (g_t['odds'] < hi)]
                for mode in ('all', 'top1'):
                    gg = g.sort_values('ev', ascending=False).drop_duplicates('race_id') if mode == 'top1' else g
                    s_sel = summarize(bets_of(gg[gg['sel']]))
                    s_hold = summarize(bets_of(gg[gg['hold']]))
                    grid.append(dict(bt=bt, ev_min=t, band=f'{lo:g}〜{hi:g}倍' if hi < 1e9 else f'{lo:g}倍〜',
                                     mode='EV最大1点' if mode == 'top1' else '条件を満たす全点', sel=s_sel, hold=s_hold))
    chosen = [g for g in grid if g['sel']['bets'] >= 300 and g['sel']['roi'] >= 1.0]
    chosen.sort(key=lambda g: -g['sel']['roi'])

    # 市場のみ（モデルなし）で同じことをした場合の参考
    mkt = []
    for bt in EV_TYPES:
        g = df[(df['bt'] == bt) & (df['ev_mkt'] >= 1.1)]
        mkt.append((bt, summarize(bets_of(g[g['sel']])), summarize(bets_of(g[g['hold']]))))

    # ---- 的中率重視: 各レースで融合確率が最も高い買い目を1点（＋自信度で絞り込み）
    hit = []
    for bt in ['ワイド', '2車複', '3連複', '2車単', '3連単']:
        top = df[df['bt'] == bt].sort_values('prob', ascending=False).drop_duplicates('race_id')
        for q_cut in (0.0, 0.5, 0.75, 0.9):
            thr = top[top['sel']]['prob'].quantile(q_cut) if q_cut > 0 else 0.0
            g = top[top['prob'] >= thr]
            hit.append(dict(bt=bt, cut=q_cut, thr=thr, sel=summarize(bets_of(g[g['sel']])), hold=summarize(bets_of(g[g['hold']]))))
        # 的中率重視だが EV≥1.0 のレースだけ（オッズがある券種のみ）
        if bt in EV_TYPES:
            g = top[top['ev'] >= 1.0]
            hit.append(dict(bt=bt, cut='EV≥1.0', thr=None, sel=summarize(bets_of(g[g['sel']])), hold=summarize(bets_of(g[g['hold']]))))

    # ---- 2点・3点買い（的中率重視の現実的な買い方）
    multi = []
    for bt, k in (('ワイド', 2), ('ワイド', 3), ('2車複', 2), ('2車複', 3), ('3連複', 3), ('3連複', 5)):
        top = df[df['bt'] == bt].sort_values('prob', ascending=False).groupby('race_id').head(k)
        multi.append(dict(bt=bt, k=k, sel=summarize(bets_of(top[top['sel']])), hold=summarize(bets_of(top[top['hold']]))))

    write_report(C, sel_months, hold_months, acc, grid, chosen, mkt, hit, multi, df)


def pct(x):
    return '-' if x != x else f'{x * 100:.1f}%'


def write_report(C, sel_months, hold_months, acc, grid, chosen, mkt, hit, multi, df):
    L = ['# 本格バックテスト レポート\n']
    n_sel = df[df['sel']]['race_id'].nunique()
    n_hold = df[df['hold']]['race_id'].nunique()
    L.append(f'- 対象: オッズ×結果がそろったレース（テスト月 {C["test_months"][0]}〜{C["test_months"][-1]}）')
    L.append(f'- 選定期間 {sel_months}: {n_sel}レース / **検証期間 {hold_months}: {n_hold}レース（選定に一度も使っていない）**')
    L.append(f'- モデル特徴量: {"基本＋S/B・決まり手（v105）" if C["use_ext"] else "基本（v104）"}')
    L.append('- オッズは結果ページの最終オッズ。実際に買う時点のオッズとはズレるため、実戦はやや下振れしうる。\n')

    L.append('## 1. 予測精度（3連単の実際の着順に付けた確率の対数損失。小さいほど良い）\n')
    L.append('| 予測 | 対数損失 |')
    L.append('|---|---:|')
    for k, v in acc.items():
        L.append(f'| {k} | {v:.4f} |')
    L.append('\n### 月ごとの融合重み（直前の月で推定）\n')
    L.append('| テスト月 | 学習レース | α（モデル） | β（市場） | 市場のみβ |')
    L.append('|---|---:|---:|---:|---:|')
    for m, fu in sorted(C['fusion'].items()):
        L.append(f'| {m} | {fu["n_train"]} | {fu["alpha"]:.3f} | {fu["beta"]:.3f} | {fu["beta_market"]:.3f} |')

    def row(s):
        lo, hi = s['ci']
        return (f'{s["bets"]} | {s["hits"]} | {pct(s["hit_rate"])} | **{pct(s["roi"])}** | '
                f'{pct(lo)}〜{pct(hi)} | {int(s["ret"] - s["stake"]):+,}円 | {s["max_losing"]}')

    L.append('\n## 2. 回収率重視（融合EVで選ぶ買い方）\n')
    L.append('選定期間で回収率100%以上・300点以上だった買い方を、検証期間でそのまま試した結果（上位20件）。\n')
    L.append('| 券種 | EV下限 | オッズ帯 | 買い方 | 選定:点数 | 選定:回収率 | 検証:点数 | 検証:的中 | 検証:的中率 | 検証:回収率 | 検証:90%区間 | 検証:収支 | 検証:最大連敗(R) |')
    L.append('|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|')
    for g in chosen[:20]:
        L.append(f'| {g["bt"]} | {g["ev_min"]} | {g["band"]} | {g["mode"]} | {g["sel"]["bets"]} | {pct(g["sel"]["roi"])} | {row(g["hold"])} |')
    if not chosen:
        L.append('| （選定期間で条件を満たす買い方なし） |||||||||||||')
    n_pos_hold = sum(1 for g in chosen if g['hold']['roi'] >= 1.0)
    L.append(f'\n選定で残った {len(chosen)} 件のうち、検証期間でも回収率100%以上: **{n_pos_hold} 件**')

    L.append('\n### 参考: 全期間・券種別（融合EV≥1.1の全点買い）\n')
    L.append('| 券種 | 選定:点数 | 選定:回収率 | 検証:点数 | 検証:的中 | 検証:的中率 | 検証:回収率 | 検証:90%区間 | 検証:収支 | 検証:最大連敗(R) |')
    L.append('|---|---:|---:|---:|---:|---:|---:|---|---:|---:|')
    for g in grid:
        if g['ev_min'] == 1.1 and g['band'] == '1倍〜' and g['mode'] == '条件を満たす全点':
            L.append(f'| {g["bt"]} | {g["sel"]["bets"]} | {pct(g["sel"]["roi"])} | {row(g["hold"])} |')
    L.append('\n### 参考: 市場だけ（モデルなし・較正のみ）で EV≥1.1 を買った場合\n')
    L.append('| 券種 | 選定:点数 | 選定:回収率 | 検証:点数 | 検証:回収率 |')
    L.append('|---|---:|---:|---:|---:|')
    for bt, s1, s2 in mkt:
        L.append(f'| {bt} | {s1["bets"]} | {pct(s1["roi"])} | {s2["bets"]} | {pct(s2["roi"])} |')

    L.append('\n## 3. 的中率重視（各レースで融合確率が最も高い1点）\n')
    L.append('| 券種 | 絞り込み | 選定:的中率 | 選定:回収率 | 検証:点数 | 検証:的中 | 検証:的中率 | 検証:回収率 | 検証:90%区間 | 検証:収支 | 検証:最大連敗(R) |')
    L.append('|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|')
    for h in hit:
        cut = h['cut'] if isinstance(h['cut'], str) else ('全レース' if h['cut'] == 0 else f'本命確率 上位{int((1 - h["cut"]) * 100)}%（≥{h["thr"] * 100:.0f}%）')
        L.append(f'| {h["bt"]} | {cut} | {pct(h["sel"]["hit_rate"])} | {pct(h["sel"]["roi"])} | {row(h["hold"])} |')
    L.append('\n### 複数点買い（融合確率の上位k点）\n')
    L.append('| 券種 | 点数/R | 選定:レース的中率 | 選定:回収率 | 検証:レース的中率 | 検証:回収率 | 検証:90%区間 |')
    L.append('|---|---:|---:|---:|---:|---:|---|')
    for mrow in multi:
        s1, s2 = mrow['sel'], mrow['hold']
        L.append(f'| {mrow["bt"]} | {mrow["k"]} | {pct(s1.get("race_hit_rate", float("nan")))} | {pct(s1["roi"])} | '
                 f'{pct(s2.get("race_hit_rate", float("nan")))} | {pct(s2["roi"])} | {pct(s2["ci"][0])}〜{pct(s2["ci"][1])} |')

    path = os.path.join(T.OUT_DIR, 'backtest_report.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print('\n'.join(L))
    # ブラウザ版の推奨設定に使う要約
    summary = dict(fusion=C['fusion'], accuracy=acc,
                   chosen=[dict(bt=g['bt'], ev_min=g['ev_min'], band=g['band'], mode=g['mode'],
                                sel_roi=g['sel']['roi'], hold_roi=g['hold']['roi'], hold_bets=g['hold']['bets'])
                           for g in chosen[:20]])
    with open(os.path.join(T.OUT_DIR, 'backtest_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, default=float)


if __name__ == '__main__':
    main()
