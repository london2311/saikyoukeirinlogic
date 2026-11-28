#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
選手ごとの細かいクセ（ニュアンス）を学習し、オッズ（市場）より当たりの予想が良くなるかを確かめる（私的利用のみ・集計値だけ）

  python3 analysis/rider_nuance.py --data <データ用リポジトリの data> [--out 結果.json] [--profiles 選手ごとの値.json]

選手ごとに、その日より前のレースだけから次を積み上げる（未来の情報は使わない）:
  * 有言実行率: コメントで「自力」系を宣言した時に、本当に自力を出したか（S・B を取った／逃げ・捲りで2着以内）
  * 番手の仕事: 番手を回った時に、先頭の選手と2人とも2着以内に入った回数が、市場（確定オッズ）の見込みより多いか少ないか
  * 単騎の強さ: 単騎の時の3着以内が、市場の見込みより多いか少ないか
  * 市場の読み違い: すべての出走で、3着以内が市場の見込みより多いか少ないか
  * 最近の調子: 直近5走の上がりタイム（同じレースの真ん中との差）が、それまでより速いか
  市場の見込み = 確定オッズの3連単から計算した確率。点数が少ない選手は 0（平均）に寄せる。

確かめ方: 2〜7月で係数を決め、8月以降で「市場の確率だけ」と「市場＋クセ」の当たり方（対数尤度）を比べる。
  差が z ≥ 3 で、前半・後半とも同じ向きの時だけ「効果あり」（期待値に入れる候補）。それ以外は選手メモの参考表示だけ。
"""
import argparse
import csv
import glob
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

SPLIT = '20260801'   # これより前で学習、以降で確かめる
PRIOR = {'jiriki': 10, 'banete': 20, 'single': 15, 'all': 40}
FORM_N = 5


def _rows(paths):
    for p in paths:
        try:
            with open(p, newline='', encoding='utf-8-sig') as f:
                yield from csv.DictReader(f)
        except OSError:
            continue


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def market(data: Path):
    """race_id → dict(win={車: p}, top2={車: p}, top3={車: p}, pair={(a,b): 2人とも2着以内の p})（確定オッズの3連単から）"""
    acc = {}
    paths = sorted(glob.glob(str(data / '*' / 'odds_*.csv'))) + sorted(glob.glob(str(data / '*' / 'odds' / 'odds_*.csv')))
    for r in _rows(paths):
        if r.get('bet_type') != '3連単':
            continue
        o = _f(r.get('odds'))
        if not o or o <= 0:
            continue
        try:
            a, b, c = (int(x) for x in str(r['combination']).split('-'))
        except ValueError:
            continue
        m = acc.setdefault(r['race_id'], dict(t=0.0, win=defaultdict(float), top2=defaultdict(float), top3=defaultdict(float),
                                              pair=defaultdict(float), seen=set()))
        if (a, b, c) in m['seen']:
            continue
        m['seen'].add((a, b, c))
        w = 1.0 / o
        m['t'] += w
        m['win'][a] += w
        for x in (a, b):
            m['top2'][x] += w
        for x in (a, b, c):
            m['top3'][x] += w
        m['pair'][tuple(sorted((a, b)))] += w
    out = {}
    for rid, m in acc.items():
        t = m['t']
        if t <= 0:
            continue
        out[rid] = {k: {x: v / t for x, v in m[k].items()} for k in ('win', 'top2', 'top3', 'pair')}
    return out


def intent(comment):
    import reasons as RS
    c = comment or ''
    if '単騎' in c or '一人' in c:
        return 'single'
    if RS.JIRIKI.search(c):
        return 'jiriki'
    return ''


def races(data: Path):
    """日付順の [(日付, race_id, [選手 dict])]。結果のあるレースだけ"""
    res = {}
    for r in _rows(sorted(glob.glob(str(data / '*' / 'results.csv')))):
        try:
            res[(r['race_id'], int(r['car_number']))] = r
        except (KeyError, ValueError):
            continue
    by = defaultdict(list)
    for r in _rows(sorted(glob.glob(str(data / '*' / 'racecards.csv')))):
        if str(r.get('absent', '0')) in ('1', 'True', 'true'):
            continue
        try:
            car = int(r['car_number'])
        except (KeyError, ValueError):
            continue
        x = res.get((r['race_id'], car))
        if x is None or x.get('accident') == '欠車':
            continue
        order = int(_f(x.get('order'), 0) or 0)
        by[r['race_id']].append(dict(
            car=car, pid=str(r.get('player_id', '')).zfill(6), comment=r.get('comment', ''),
            line=r.get('line', ''), pos=int(_f(r.get('line_position'), 0) or 0), size=int(_f(r.get('line_size'), 0) or 0),
            single=str(r.get('is_single', '')) in ('1', '1.0', 'True'), order=order,
            sb=(x.get('standing') == '1' or x.get('back') == '1'),
            run=(order in (1, 2) and x.get('factor') in ('逃', '捲')),
            half=_f(x.get('final_half')), date=str(r.get('schedule_id', ''))[:8]))
    out = [(rs[0]['date'], rid, rs) for rid, rs in by.items() if rs and rs[0]['date']]
    return sorted(out)


class Stats:
    """選手ごとの積み上げ（その日より前のレースだけ）"""

    def __init__(self):
        self.s = defaultdict(lambda: defaultdict(float))
        self.half = defaultdict(list)

    def feats(self, pid):
        s = self.s[pid]
        f = {}
        f['jiriki'] = (s['j_done'] - s['j_n'] * 0.6) / (s['j_n'] + PRIOR['jiriki'])  # 0.6 ≒ 全体の有言実行率
        f['jiriki_rate'] = s['j_done'] / s['j_n'] if s['j_n'] else None
        f['jiriki_n'] = s['j_n']
        f['banete'] = s['b_res'] / (s['b_n'] + PRIOR['banete'])
        f['banete_n'] = s['b_n']
        f['single'] = s['s_res'] / (s['s_n'] + PRIOR['single'])
        f['single_n'] = s['s_n']
        f['all'] = s['a_res'] / (s['a_n'] + PRIOR['all'])
        f['all_n'] = s['a_n']
        h = self.half[pid]
        if len(h) >= FORM_N + 5:
            f['form'] = sum(h[:-FORM_N]) / (len(h) - FORM_N) - sum(h[-FORM_N:]) / FORM_N  # 速くなっていればプラス
        else:
            f['form'] = 0.0
        return f

    def update(self, rs, m):
        heads = {r['line']: r for r in rs if r['pos'] == 1 and r['size'] >= 2}
        for r in rs:
            s = self.s[r['pid']]
            q3 = m['top3'].get(r['car'], 0.0)
            hit3 = 1.0 if 1 <= r['order'] <= 3 else 0.0
            s['a_n'] += 1
            s['a_res'] += hit3 - q3
            it = intent(r['comment'])
            if it == 'jiriki':
                s['j_n'] += 1
                s['j_done'] += 1.0 if (r['sb'] or r['run']) else 0.0
            if r['single'] or it == 'single':
                s['s_n'] += 1
                s['s_res'] += hit3 - q3
            h = heads.get(r['line'])
            if r['pos'] == 2 and h is not None:
                qp = m['pair'].get(tuple(sorted((h['car'], r['car']))), 0.0)
                both = 1.0 if (1 <= h['order'] <= 2 and 1 <= r['order'] <= 2) else 0.0
                s['b_n'] += 1
                s['b_res'] += both - qp
        hs = sorted(r['half'] for r in rs if r['half'] and 9.0 < r['half'] < 15.0)
        med = hs[len(hs) // 2] if len(hs) >= 4 else None
        for r in rs:
            # 上がりタイムは、同じレースの選手の真ん中との差（バンクの長さ・風で全員が速い／遅いのを消す）
            if med is not None and r['half'] and 9.0 < r['half'] < 15.0:
                self.half[r['pid']].append(r['half'] - med)
                del self.half[r['pid']][:-30]


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def build(data: Path, mkt=None):
    """学習・確かめ用の行（オッズと結果）と、最後の時点の選手ごとの値"""
    mkt = market(data) if mkt is None else mkt
    st = Stats()
    top3_rows, pair_rows = [], []
    for date, rid, rs in races(data):
        m = mkt.get(rid)
        if not m:
            continue
        fs = {r['pid']: st.feats(r['pid']) for r in rs}
        heads = {r['line']: r for r in rs if r['pos'] == 1 and r['size'] >= 2}
        for r in rs:
            f = fs[r['pid']]
            it = intent(r['comment'])
            # 3着以内: 市場の確率 ＋ 市場の読み違い・単騎の強さ（単騎の時だけ）・最近の調子・有言実行（自力宣言の時だけ）
            x = [_logit(m['top3'].get(r['car'], 0.0)), f['all'], f['single'] if (r['single'] or it == 'single') else 0.0,
                 f['form'], f['jiriki'] if it == 'jiriki' else 0.0]
            top3_rows.append((date, x, 1.0 if 1 <= r['order'] <= 3 else 0.0))
            h = heads.get(r['line'])
            if r['pos'] == 2 and h is not None:
                qp = m['pair'].get(tuple(sorted((h['car'], r['car']))), 0.0)
                fh = fs[h['pid']]
                xh = fh['jiriki'] if intent(h['comment']) == 'jiriki' else 0.0
                x2 = [_logit(qp), f['banete'], xh]
                pair_rows.append((date, x2, 1.0 if (1 <= h['order'] <= 2 and 1 <= r['order'] <= 2) else 0.0))
        st.update(rs, m)
    return top3_rows, pair_rows, st


def fit_logit(X, y, iters=25, l2=1e-3):
    import numpy as np
    X = np.column_stack([np.ones(len(X)), np.array(X)])
    y = np.array(y)
    w = np.zeros(X.shape[1])
    w[1] = 1.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w)))
        g = X.T @ (y - p) - l2 * w
        H = (X * (p * (1 - p))[:, None]).T @ X + l2 * np.eye(len(w))
        w += np.linalg.solve(H, g)
    return w


def loglik(w, X, y):
    import numpy as np
    X = np.column_stack([np.ones(len(X)), np.array(X)])
    p = 1 / (1 + np.exp(-(X @ w)))
    p = np.clip(p, 1e-9, 1 - 1e-9)
    y = np.array(y)
    return y * np.log(p) + (1 - y) * np.log(1 - p)


def compare(rows, names, split=SPLIT):
    """市場だけ vs 市場＋クセ。後半で確かめる（対数尤度の差・z、さらに後半を2つに分けた z）"""
    import numpy as np
    tr = [(x, y) for d, x, y in rows if d < split]
    te = [(d, x, y) for d, x, y in rows if d >= split]
    if len(tr) < 1000 or len(te) < 1000:
        return None
    Xtr, ytr = [x for x, _ in tr], [y for _, y in tr]
    w0 = fit_logit([[x[0]] for x in Xtr], ytr)
    w1 = fit_logit(Xtr, ytr)
    Xte, yte, dte = [x for _, x, _ in te], [y for _, _, y in te], [d for d, _, _ in te]
    l0 = loglik(w0, [[x[0]] for x in Xte], yte)
    l1 = loglik(w1, Xte, yte)
    diff = l1 - l0
    mid = sorted(dte)[len(dte) // 2]

    def z(mask):
        dd = diff[mask]
        return float(dd.sum() / (dd.std() * math.sqrt(len(dd)))) if len(dd) > 1 and dd.std() > 0 else 0.0
    dte = np.array(dte)
    out = dict(n_train=len(tr), n_test=len(te), coef={n: round(float(c), 4) for n, c in zip(['定数'] + names, w1)},
               ll_gain=round(float(diff.sum()), 2), z=round(z(np.ones(len(diff), bool)), 2),
               z_first=round(z(dte < mid), 2), z_second=round(z(dte >= mid), 2))
    # 1つずつ外した時の差（どのクセが効いているか）
    each = {}
    for k in range(1, len(names)):
        keep = [0] + [j for j in range(1, len(names)) if j != k]
        wk = fit_logit([[x[j] for j in keep] for x in Xtr], ytr)
        lk = loglik(wk, [[x[j] for j in keep] for x in Xte], yte)
        each[names[k]] = round(float((l1 - lk).sum()), 2)
    out['each'] = each
    out['useful'] = bool(out['z'] >= 3 and out['z_first'] >= 1.5 and out['z_second'] >= 1.5)
    return out


def profiles(st: Stats, names=None):
    """選手ごとの値（期待値ボードの選手メモ用）。点数の少ない値は出さない"""
    out = {}
    for pid in list(st.s):
        f = st.feats(pid)
        p = {}
        if f['jiriki_n'] >= 8:
            p['jr'] = [round(f['jiriki_rate'], 2), int(f['jiriki_n'])]
        if f['banete_n'] >= 10:
            p['bt'] = [round(f['banete'], 3), int(f['banete_n'])]
        if f['single_n'] >= 8:
            p['sg'] = [round(f['single'], 3), int(f['single_n'])]
        if f['all_n'] >= 20:
            p['al'] = [round(f['all'], 3), int(f['all_n'])]
        if f['form']:
            p['fm'] = round(f['form'], 2)
        if p:
            out[pid] = p
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', default='')
    ap.add_argument('--profiles', default='')
    a = ap.parse_args(argv)
    data = Path(a.data)
    top3_rows, pair_rows, st = build(data)
    r3 = compare(top3_rows, ['市場の3着以内', '市場の読み違い', '単騎の強さ', '最近の調子', '有言実行'])
    rp = compare(pair_rows, ['市場の番手ワンツー', '番手の仕事', '先頭の有言実行'])
    rep = dict(top3=r3, pair=rp, riders=len(st.s), split=SPLIT)
    print(f'選手 {len(st.s)}人・3着以内の行 {len(top3_rows)}・番手の行 {len(pair_rows)}')
    for k, r in (('3着以内', r3), ('番手ワンツー', rp)):
        if r:
            print(f'  {k}: 後半の対数尤度の差 {r["ll_gain"]:+.1f}（z {r["z"]:+.2f}・前半 {r["z_first"]:+.2f}・後半 {r["z_second"]:+.2f}）'
                  f' → {"効果あり" if r["useful"] else "効果なし"}')
            print(f'     係数 {r["coef"]}')
            print(f'     1つずつ外した時の差 {r["each"]}')
    if a.out:
        Path(a.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')
    if a.profiles:
        Path(a.profiles).parent.mkdir(parents=True, exist_ok=True)
        Path(a.profiles).write_text(json.dumps(profiles(st), ensure_ascii=False, separators=(',', ':')), encoding='utf-8')


if __name__ == '__main__':
    main()
