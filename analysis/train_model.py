#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
競輪 着順モデル（v104）学習・検証スクリプト

モデル: 段階型 条件付きロジット（Plackett-Luce を拡張し、ライン関係を2着・3着の段階に入れたもの）
  P(1着=i)          = softmax_i( w1·x_i )
  P(2着=j | 1着=a)   = softmax_j( w2·x_j + c2·pair(a,j) )
  P(3着=k | a,b)     = softmax_k( w3·x_k + c3·pair3(a,b,k) )
  x_i はブラウザ版ロジック（index.html）でも同じものを計算できる特徴量だけで構成する。

検証: 日付順のローリング（各テスト日は、その前日までのデータだけで学習して予測）。
比較対象: 一様分布 / 得点だけのモデル / 市場（的中組の払戻から逆算した確率）

使い方:
  python3 analysis/train_model.py            # リポジトリ直下・data/ 配下のCSVを自動検出
  python3 analysis/train_model.py --embed    # 学習後、index.html の KEIRIN_V104_MODEL も自動で書き換える
  → analysis/model_v104.json（ブラウザ版に埋め込む係数）
  → analysis/report_v104.md（検証レポート）

CSVはファイル名ではなく「ヘッダー」で種類を判定する（アップロード時の名前の取り違えに強くするため）。
"""
import glob
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.optimize import minimize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, 'analysis')

VENUE_PREFECTURE = {
    '函館': '北海道', '青森': '青森', 'いわき平': '福島', '宇都宮': '栃木', '取手': '茨城', '前橋': '群馬',
    '大宮': '埼玉', '西武園': '埼玉', '京王閣': '東京', '松戸': '千葉', '千葉': '千葉',
    '立川': '東京', '川崎': '神奈川', '平塚': '神奈川', '小田原': '神奈川', '伊東': '静岡',
    '弥彦': '新潟', '富山': '富山', '福井': '福井', '岐阜': '岐阜', '大垣': '岐阜', '豊橋': '愛知',
    '名古屋': '愛知', '四日市': '三重', '松阪': '三重', '静岡': '静岡', '向日町': '京都', '奈良': '奈良',
    '岸和田': '大阪', '和歌山': '和歌山', '玉野': '岡山', '広島': '広島', '防府': '山口', '小松島': '徳島',
    '高松': '香川', '高知': '高知', '松山': '愛媛', '別府': '大分', '小倉': '福岡', '久留米': '福岡',
    '武雄': '佐賀', '佐世保': '長崎', '熊本': '熊本',
}
TAKEOUT = 0.75

# ------------------------------------------------------------ データ読み込み
# 判定順に注意: 新形式のオッズCSVは payoff 列も持つため、payoffs より先に odds を判定する
SIGNATURES = {
    'races': {'venue_name', 'entries_number', 'start_at'},
    'cards': {'race_point', 'line_position', 'style'},
    'results': {'order', 'car_number', 'factor'},
    'odds': {'combination', 'odds', 'bet_type'},
    'payoffs': {'bet_type', 'combination', 'payoff', 'popularity'},
}
# 同じレースが複数ファイルに入っていても1件にまとめるためのキー
DEDUP_KEYS = {
    'races': ['race_id'],
    'cards': ['race_id', 'car_number'],
    'results': ['race_id', 'car_number'],
    'odds': ['race_id', 'bet_type', 'combination'],
    'payoffs': ['race_id', 'bet_type', 'combination'],
}


def load_all(root):
    paths = [p for p in glob.glob(os.path.join(root, '*')) + glob.glob(os.path.join(root, 'data', '**', '*'), recursive=True)
             if os.path.isfile(p) and os.path.getsize(p) > 0 and not p.endswith(('.py', '.html', '.md', '.json', '.txt'))]
    found = defaultdict(list)
    # data/ 配下（収集スクリプトの正式な保存先）を後ろに並べ、重複時はそちらを優先する
    paths.sort(key=lambda p: (os.sep + 'data' + os.sep in p, p))
    for p in paths:
        try:
            with open(p, encoding='utf-8-sig') as f:
                header = set(f.readline().strip().split(','))
        except (UnicodeDecodeError, OSError):
            continue
        for kind, sig in SIGNATURES.items():
            if sig <= header:
                found[kind].append(p)
                break
    out = {}
    for kind, ps in found.items():
        frames = [pd.read_csv(p, encoding='utf-8-sig', dtype={'race_id': str, 'player_id': str, 'schedule_id': str}) for p in ps]
        df = pd.concat(frames, ignore_index=True)
        if kind == 'odds':
            df['odds'] = pd.to_numeric(df['odds'], errors='coerce')
        out[kind] = df.drop_duplicates(subset=DEDUP_KEYS[kind], keep='last')
        names = [os.path.relpath(p, root) for p in ps]
        print(f'[load] {kind}: {len(out[kind])}行 ← {", ".join(names[:3])}{" ほか%d件" % (len(names) - 3) if len(names) > 3 else ""}')
    return out


# ------------------------------------------------------------ 特徴量（ブラウザ版と同一定義）
RIDER_FEATURES = [
    'pt',          # (競走得点 − レース平均)/10
    'pt_top',      # レース最高得点
    'win',         # 勝率 − レース平均（0〜1）
    'quin',        # 2連対率 − レース平均
    'trio',        # 3連対率 − レース平均
    'nige',        # 脚質: 逃
    'ryou',        # 脚質: 両（追が基準）
    'ban',         # ライン2番手
    'pos3',        # ライン3番手以降
    'single',      # 単騎
    'line_pt',     # 所属ラインの平均得点 − レース平均（/10）
    'head_pt_ban', # 番手のみ: 自ライン先頭の得点 − レース平均（/10）
    'nige_comp',   # 逃 × (他の逃の数)
    'home',        # 地元（選手の府県 = 開催地の府県）
]
PAIR2_FEATURES = ['follow', 'lead_back', 'same_line_other', 'follow_x_ban_quin']
PAIR3_FEATURES = ['behind_b', 'behind_a', 'same_line_ab', 'ahead_of_b']
BASE_RIDER_FEATURES = list(RIDER_FEATURES)
BASE_PAIR2_FEATURES = list(PAIR2_FEATURES)
# v105: 出走表の拡張列（直近4か月の S/B 回数・決まり手回数）がある場合に追加する特徴量。
# いずれも「回数 ÷ 出走数(1着+2着+3着+着外)」をレース内で中心化した値。
EXT_RIDER_FEATURES = ['s_rate', 'b_rate', 'k_nige', 'k_maku', 'k_sashi', 'k_mark']
EXT_PAIR2_FEATURES = ['follow_x_lead_b']  # 先頭が1着の時の番手流れ込み × 先頭のB率
USE_EXT = False


def configure(use_ext):
    """拡張特徴量を使うかどうかを切り替える（データに拡張列が9割以上ある時に有効化）"""
    global USE_EXT, RIDER_FEATURES, PAIR2_FEATURES
    USE_EXT = bool(use_ext)
    RIDER_FEATURES = BASE_RIDER_FEATURES + (EXT_RIDER_FEATURES if USE_EXT else [])
    PAIR2_FEATURES = BASE_PAIR2_FEATURES + (EXT_PAIR2_FEATURES if USE_EXT else [])


def b_index():
    return RIDER_FEATURES.index('b_rate') if USE_EXT else None


def rider_matrix(riders, venue_pref):
    """riders: list of dict(num, point, first, second, third, style, line_id, line_pos, line_size, pref)"""
    pts = np.array([r['point'] for r in riders], float)
    valid = pts > 0
    mean_pt = pts[valid].mean() if valid.any() else 80.0
    pts = np.where(valid, pts, mean_pt - 10.0)  # 得点0（新人等）は平均−10で補完
    rates = np.array([[r['first'], r['second'], r['third']] for r in riders], float) / 100.0
    rates = rates - rates.mean(axis=0)
    line_pts = defaultdict(list)
    for r, p in zip(riders, pts):
        line_pts[r['line_id']].append(p)
    head_pt = {}
    for r, p in zip(riders, pts):
        if r['line_pos'] == 1:
            head_pt[r['line_id']] = p
    n_nige = sum(1 for r in riders if r['style'] == '逃')
    ext = None
    if USE_EXT:
        keys = ['s', 'b', 'k_nige', 'k_maku', 'k_sashi', 'k_mark']
        raw = np.array([[(r.get(k, 0.0) / r['n4m']) if r.get('n4m', 0) > 0 else np.nan for k in keys] for r in riders], float)
        col_mean = np.nanmean(np.where(np.isnan(raw), np.nan, raw), axis=0) if np.isfinite(raw).any() else np.zeros(len(keys))
        col_mean = np.where(np.isnan(col_mean), 0.0, col_mean)
        raw = np.where(np.isnan(raw), col_mean, raw)
        ext = raw - raw.mean(axis=0)
    X = np.zeros((len(riders), len(RIDER_FEATURES)))
    for i, r in enumerate(riders):
        single = r['line_size'] <= 1
        row = [
            (pts[i] - pts.mean()) / 10.0,
            1.0 if pts[i] >= pts.max() else 0.0,
            rates[i, 0], rates[i, 1], rates[i, 2],
            1.0 if r['style'] == '逃' else 0.0,
            1.0 if r['style'] == '両' else 0.0,
            1.0 if (not single and r['line_pos'] == 2) else 0.0,
            1.0 if (not single and r['line_pos'] >= 3) else 0.0,
            1.0 if single else 0.0,
            (np.mean(line_pts[r['line_id']]) - pts.mean()) / 10.0,
            ((head_pt.get(r['line_id'], pts.mean()) - pts.mean()) / 10.0) if (not single and r['line_pos'] == 2) else 0.0,
            (n_nige - 1.0) if r['style'] == '逃' else 0.0,
            1.0 if (venue_pref and r['pref'] == venue_pref) else 0.0,
        ]
        if USE_EXT:
            row += list(ext[i])
        X[i] = row
    return X


def pair2(ra, rj, quin_j, lead_b=0.0):
    same = ra['line_size'] > 1 and ra['line_id'] == rj['line_id']
    follow = same and rj['line_pos'] == ra['line_pos'] + 1
    lead_back = same and rj['line_pos'] == ra['line_pos'] - 1
    v = [1.0 if follow else 0.0, 1.0 if lead_back else 0.0,
         1.0 if (same and not follow and not lead_back) else 0.0,
         quin_j if (follow and ra['line_pos'] == 1) else 0.0]
    if USE_EXT:
        v.append(lead_b if (follow and ra['line_pos'] == 1) else 0.0)
    return v


def pair3(ra, rb, rk):
    sb = rb['line_size'] > 1 and rb['line_id'] == rk['line_id']
    sa = ra['line_size'] > 1 and ra['line_id'] == rk['line_id']
    behind_b = sb and rk['line_pos'] == rb['line_pos'] + 1
    behind_a = sa and rk['line_pos'] == ra['line_pos'] + 1
    ahead_b = sb and rk['line_pos'] == rb['line_pos'] - 1
    return [1.0 if behind_b else 0.0, 1.0 if (behind_a and not behind_b) else 0.0,
            1.0 if ((sa or sb) and not behind_b and not behind_a and not ahead_b) else 0.0,
            1.0 if ahead_b else 0.0]


# ------------------------------------------------------------ レース単位に整形
def ext_fill_rate(cards):
    if 'back_count' not in cards:
        return 0.0
    return float(pd.to_numeric(cards['back_count'], errors='coerce').notna().mean())


def build_races(D):
    races = D['races']
    races = races[races.get('cancel', 0).fillna(0).astype(int) == 0]
    cards = D['cards']
    cards = cards[cards['absent'].fillna(0).astype(int) == 0]
    res = D['results']
    pay = D.get('payoffs')
    meta = races.set_index('race_id')
    top3 = {}
    for rid, g in res[res['order'].between(1, 3)].groupby('race_id'):
        g = g.sort_values('order')
        if list(g['order']) == [1, 2, 3]:
            top3[rid] = [int(x) for x in g['car_number']]
    payoffs = defaultdict(dict)
    if pay is not None:
        for row in pay.itertuples(index=False):
            payoffs[row.race_id].setdefault(row.bet_type, []).append((str(row.combination), float(row.payoff), row.popularity))
    out = []
    for rid, g in cards.groupby('race_id'):
        if rid not in meta.index or rid not in top3:
            continue
        m = meta.loc[rid]
        venue_pref = VENUE_PREFECTURE.get(str(m['venue_name']))
        riders = []
        for r in g.sort_values('car_number').itertuples(index=False):
            single = int(r.is_single) == 1 if not pd.isna(r.is_single) else True
            rd = dict(
                num=int(r.car_number), point=float(r.race_point or 0), first=float(r.first_rate or 0),
                second=float(r.second_rate or 0), third=float(r.third_rate or 0), style=str(r.style),
                line_id=('s%d' % r.car_number) if single else str(r.line_order),
                line_pos=1 if single else int(r.line_position), line_size=1 if single else int(r.line_size),
                pref=str(r.prefecture))
            if USE_EXT:
                f = lambda k: float(getattr(r, k)) if (hasattr(r, k) and not pd.isna(getattr(r, k))) else 0.0
                rd.update(s=f('standing_count'), b=f('back_count'), k_nige=f('escape_count'), k_maku=f('makuri_count'),
                          k_sashi=f('sashi_count'), k_mark=f('mark_count'),
                          n4m=f('first_count') + f('second_count') + f('third_count') + f('out_count'))
            riders.append(rd)
        nums = [r['num'] for r in riders]
        if len(riders) < 4 or any(n not in nums for n in top3[rid]):
            continue
        out.append(dict(race_id=rid, date=int(m['date']), venue=str(m['venue_name']), grade=int(m['grade']),
                        klass=str(m['class']), race_type=str(m['race_type']), riders=riders,
                        X=rider_matrix(riders, venue_pref), top3=top3[rid], payoffs=payoffs.get(rid, {})))
    return out


# ------------------------------------------------------------ 条件付きロジット
def softmax(u):
    u = u - u.max()
    e = np.exp(u)
    return e / e.sum()


def fit_cl(sets, dim, l2=1.0, w0=None):
    """sets: list of (Z [m×dim], chosen_index). L2正則化付き最尤推定。"""
    def nll(w):
        f, g = 0.0, np.zeros(dim)
        for Z, c in sets:
            u = Z @ w
            mx = u.max()
            lse = mx + math.log(np.exp(u - mx).sum())
            p = np.exp(u - lse)
            f -= u[c] - lse
            g -= Z[c] - p @ Z
        f += 0.5 * l2 * w @ w
        g += l2 * w
        return f, g
    r = minimize(nll, np.zeros(dim) if w0 is None else w0, jac=True, method='L-BFGS-B')
    return r.x


def stage_sets(race_list, stage, feat_idx=None):
    sets = []
    for R in race_list:
        X, riders, (a, b, c) = R['X'], R['riders'], R['top3']
        idx = {r['num']: i for i, r in enumerate(riders)}
        Xs = X if feat_idx is None else X[:, feat_idx]
        if stage == 1:
            sets.append((Xs, idx[a]))
        elif stage == 2:
            cand = [i for i in range(len(riders)) if i != idx[a]]
            lb = X[idx[a], b_index()] if USE_EXT else 0.0
            Z = np.array([np.concatenate([Xs[j], pair2(riders[idx[a]], riders[j], X[j, 3], lb)]) for j in cand])
            sets.append((Z, cand.index(idx[b])))
        else:
            cand = [i for i in range(len(riders)) if i not in (idx[a], idx[b])]
            Z = np.array([np.concatenate([Xs[k], pair3(riders[idx[a]], riders[idx[b]], riders[k])]) for k in cand])
            sets.append((Z, cand.index(idx[c])))
    return sets


def fit_model(train, feat_idx=None, l2=3.0):
    d = len(RIDER_FEATURES) if feat_idx is None else len(feat_idx)
    w1 = fit_cl(stage_sets(train, 1, feat_idx), d, l2)
    w2 = fit_cl(stage_sets(train, 2, feat_idx), d + len(PAIR2_FEATURES), l2)
    w3 = fit_cl(stage_sets(train, 3, feat_idx), d + len(PAIR3_FEATURES), l2)
    return dict(w1=w1, w2=w2, w3=w3, feat_idx=feat_idx)


def predict_dist(model, R):
    """全3連単の確率 {(a,b,c): p}"""
    X, riders = R['X'], R['riders']
    fi = model['feat_idx']
    Xs = X if fi is None else X[:, fi]
    n = len(riders)
    d = Xs.shape[1]
    w1, w2, w3 = model['w1'], model['w2'], model['w3']
    p1 = softmax(Xs @ w1)
    base2 = Xs @ w2[:d]
    base3 = Xs @ w3[:d]
    dist = {}
    for a in range(n):
        cand2 = [j for j in range(n) if j != a]
        lb = X[a, b_index()] if USE_EXT else 0.0
        u2 = np.array([base2[j] + np.dot(w2[d:], pair2(riders[a], riders[j], X[j, 3], lb)) for j in cand2])
        p2 = softmax(u2)
        for jj, b in enumerate(cand2):
            cand3 = [k for k in range(n) if k not in (a, b)]
            u3 = np.array([base3[k] + np.dot(w3[d:], pair3(riders[a], riders[b], riders[k])) for k in cand3])
            p3 = softmax(u3)
            for kk, c in enumerate(cand3):
                dist[(riders[a]['num'], riders[b]['num'], riders[c]['num'])] = p1[a] * p2[jj] * p3[kk]
    return dist


# ------------------------------------------------------------ 券種別の確率と的中判定
def ticket_probs(dist):
    t = {'3連単': {}, '3連複': defaultdict(float), '2車単': defaultdict(float), '2車複': defaultdict(float), 'ワイド': defaultdict(float)}
    for (a, b, c), p in dist.items():
        t['3連単'][f'{a}-{b}-{c}'] = p
        t['3連複']['-'.join(map(str, sorted((a, b, c))))] += p
        t['2車単'][f'{a}-{b}'] += p
        t['2車複']['-'.join(map(str, sorted((a, b))))] += p
        for x, y in ((a, b), (a, c), (b, c)):
            t['ワイド']['-'.join(map(str, sorted((x, y))))] += p
    return t


def norm_combo(bet_type, combo):
    parts = [int(x) for x in str(combo).replace('=', '-').split('-') if x.strip().isdigit()]
    if bet_type in ('3連複', '2車複', 'ワイド'):
        parts = sorted(parts)
    return '-'.join(map(str, parts))


def realized_payoff(R, bet_type, key):
    """その買い目が当たっていれば払戻(円/100円)、外れなら0"""
    for combo, payoff, pop in R['payoffs'].get(bet_type, []):
        if norm_combo(bet_type, combo) == key:
            return payoff
    return 0.0


def market_prob_realized(R):
    """的中3連単の払戻から市場の含意確率を逆算（控除率25%）"""
    a, b, c = R['top3']
    pay = realized_payoff(R, '3連単', f'{a}-{b}-{c}')
    return TAKEOUT / (pay / 100.0) if pay > 0 else None


# ------------------------------------------------------------ 市場融合 α の推定（オッズと結果が重なるレースがある場合）
def load_lambda_table():
    """index.html に埋め込まれた v102 実測λ（3連単）を読む"""
    import re
    with open(os.path.join(ROOT, 'index.html'), encoding='utf-8') as f:
        m = re.search(r'const KEIRIN_V101_ODDS_CAL = (\{.*?\});\n', f.read())
    return json.loads(m.group(1))['lambda']['3連単'] if m else None


def lam(tbl, odds):
    if not tbl:
        return 1.0
    x = math.log(max(1.01, odds))
    if x <= math.log(tbl[0][0]):
        return tbl[0][1]
    for (o0, l0), (o1, l1) in zip(tbl, tbl[1:]):
        if x <= math.log(o1):
            return l0 + (l1 - l0) * (x - math.log(o0)) / (math.log(o1) - math.log(o0))
    return tbl[-1][1]


def market_dist(R, odds_rows, tbl):
    """3連単オッズ（λ較正・正規化）→ 全着順の市場確率。的中行の odds=払戻金額 は /100 に補正。"""
    a, b, c = R['top3']
    hit_key = f'{a}-{b}-{c}'
    hit_pay = realized_payoff(R, '3連単', hit_key)
    o = {}
    for combo, od in odds_rows:
        if od is None or not (od >= 1.0):
            continue
        key = norm_combo('3連単', combo)
        if key == hit_key and hit_pay > 0 and abs(od - hit_pay) < 1e-6:
            od = hit_pay / 100.0
        o[key] = od
    nums = [r['num'] for r in R['riders']]
    keys = [f'{x}-{y}-{z}' for x in nums for y in nums for z in nums if len({x, y, z}) == 3]
    if sum(k in o for k in keys) < 0.6 * len(keys):
        return None
    w = {k: TAKEOUT / o[k] * lam(tbl, o[k]) for k in keys if k in o}
    mn = min(w.values())
    s = 0.0
    for k in keys:
        w.setdefault(k, mn * 0.5)
        s += w[k]
    return {tuple(map(int, k.split('-'))): v / s for k, v in w.items()}


def staged_pool(q, p, al):
    """ブラウザ版と同じ段階型の対数プーリング（1着 → 2着|1着 → 3着|1・2着）"""
    def marg(d, n):
        m = defaultdict(float)
        for k, v in d.items():
            m[k[:n]] += v
        return m
    q1, p1, q2, p2 = marg(q, 1), marg(p, 1), marg(q, 2), marg(p, 2)
    lp = lambda x, y: (1 - al) * math.log(max(x, 1e-15)) + al * math.log(max(y, 1e-15))

    def pool(keys, group, logf):
        out, groups = {}, defaultdict(list)
        for k in keys:
            groups[group(k)].append(k)
        for g in groups.values():
            lw = [logf(k) for k in g]
            mx = max(lw)
            e = [math.exp(v - mx) for v in lw]
            s = sum(e)
            for k, v in zip(g, e):
                out[k] = v / s
        return out
    f1 = pool(list(q1), lambda k: 0, lambda k: lp(q1[k], p1.get(k, 0)))
    f2 = pool(list(q2), lambda k: k[:1], lambda k: lp(q2[k] / max(q1[k[:1]], 1e-15), p2.get(k, 0) / max(p1.get(k[:1], 0), 1e-15)))
    f3 = pool(list(q), lambda k: k[:2], lambda k: lp(q[k] / max(q2[k[:2]], 1e-15), p.get(k, 0) / max(p2.get(k[:2], 0), 1e-15)))
    return {k: f1[k[:1]] * f2[k[:2]] * f3[k] for k in q}


def fit_fusion(races, odds_df, model_fn):
    """段階型 log-pool（β=1−α）の α を、実際の着順の対数尤度最大化で推定（ローリング予測の p を使用）"""
    if odds_df is None:
        return None
    tbl = load_lambda_table()
    o3 = odds_df[(odds_df['bet_type'] == '3連単') & (odds_df['absent'].fillna(0).astype(int) == 0)]
    rows = defaultdict(list)
    for r in o3.itertuples(index=False):
        rows[r.race_id].append((r.combination, None if pd.isna(r.odds) else float(r.odds)))
    pairs = []
    for R in races:
        if R['race_id'] not in rows:
            continue
        # λ較正テーブルは過去データ由来で検証期間と重なりうるため、比較は全方式とも
        # 「3連単オッズの逆数を正規化した確率」を基準にそろえる（公平な比較のため）
        q = market_dist(R, rows[R['race_id']], None)
        p = model_fn(R)
        if q and p:
            pairs.append((q, p, tuple(R['top3'])))
    if len(pairs) < 30:
        return {'races': len(pairs), 'alpha': None}
    # Benter型（着順全体の対数プール、α・βとも自由）: f ∝ q_raw^β · p^α を最尤推定
    #   q_raw = 3連単オッズの逆数を正規化（λ較正なし）。本格バックテストで最も良かった方式。
    raw_pairs = []
    for R in races:
        if R['race_id'] in rows:
            qr = market_dist(R, rows[R['race_id']], None)
            pr = model_fn(R)
            if qr and pr:
                keys = list(qr)
                raw_pairs.append((np.log(np.maximum([pr.get(k, 0.0) for k in keys], 1e-12)),
                                  np.log(np.maximum([qr[k] for k in keys], 1e-12)), keys.index(tuple(R['top3']))))

    def nll(w):
        f, g = 0.0, np.zeros(2)
        for lp, lq, win in raw_pairs:
            u = w[0] * lp + w[1] * lq
            mx = u.max()
            e = np.exp(u - mx)
            z = e.sum()
            pr = e / z
            f -= u[win] - mx - math.log(z)
            g -= np.array([lp[win] - pr @ lp, lq[win] - pr @ lq])
        return f, g
    rb = minimize(nll, np.array([0.3, 1.0]), jac=True, method='L-BFGS-B')
    benter = {'alpha': float(rb.x[0]), 'beta': float(rb.x[1]), 'll': -float(rb.fun) / len(raw_pairs)}
    if len(pairs) > 2000:  # 段階型/線形の格子探索は重いので標本で比較
        pairs = [pairs[i] for i in np.random.default_rng(0).choice(len(pairs), 2000, replace=False)]
    grid = [i / 10 for i in range(0, 11)]
    best = None
    curve = []
    for al in grid:
        row = {}
        for ftype in ('linear', 'log'):
            tot = 0.0
            for q, p, key in pairs:
                f = staged_pool(q, p, al) if ftype == 'log' else {key: (1 - al) * q[key] + al * p.get(key, 0.0)}
                tot += math.log(max(f[key], 1e-300))
            row[ftype] = tot / len(pairs)
            if best is None or row[ftype] > best[2]:
                best = (ftype, al, row[ftype])
        curve.append((al, row['linear'], row['log']))
    # 比較は同じ標本の上で行う（Benter型の推定値を標本で評価し直す）
    tot = 0.0
    for q, p, key in pairs:
        keys = list(q)
        u = np.array([benter['alpha'] * math.log(max(p.get(k, 0.0), 1e-12)) + benter['beta'] * math.log(max(q[k], 1e-12)) for k in keys])
        mx = u.max()
        tot += u[keys.index(key)] - mx - math.log(np.exp(u - mx).sum())
    benter['ll_sample'] = tot / len(pairs)
    out = {'races': len(raw_pairs), 'type': best[0], 'alpha': best[1], 'beta': 1 - best[1], 'curve': curve, 'benter': benter}
    if benter['ll_sample'] >= best[2]:
        out.update(type='benter', alpha=benter['alpha'], beta=benter['beta'])
    return out


# ------------------------------------------------------------ メイン
def main():
    data_root = ROOT
    if '--data' in sys.argv:
        data_root = os.path.abspath(sys.argv[sys.argv.index('--data') + 1])
    D = load_all(data_root)
    for k in ('races', 'cards', 'results'):
        if k not in D:
            sys.exit(f'必要なCSVが見つかりません: {k}')
    configure(ext_fill_rate(D['cards']) >= 0.9)
    print(f'[features] 拡張特徴量(S/B・決まり手): {"使用" if USE_EXT else "なし"}')
    races = build_races(D)
    dates = sorted({R['date'] for R in races})
    print(f'[data] 学習可能レース {len(races)} / 期間 {dates[0]}〜{dates[-1]}（{len(dates)}日）')

    # ---- ローリング検証: 期間が短ければ後半の日を1日ずつ、長ければ直近3か月を1か月ずつテスト
    #      （いずれも、テスト単位より前のデータだけで学習）
    span = (pd.Timestamp(str(dates[-1])) - pd.Timestamp(str(dates[0]))).days
    unit = (lambda d: d // 100) if span > 60 else (lambda d: d)
    units = sorted({unit(d) for d in dates})
    test_days = units[-3:] if span > 60 else units[-max(1, len(units) // 2):]
    pt_idx = [RIDER_FEATURES.index('pt')]
    pred = []  # (race, dist_full, dist_pt)
    for day in test_days:
        train = [R for R in races if unit(R['date']) < day]
        test = [R for R in races if unit(R['date']) == day]
        m_full = fit_model(train)
        m_pt = fit_model(train, pt_idx)
        for R in test:
            pred.append((R, predict_dist(m_full, R), predict_dist(m_pt, R)))
        print(f'  test {day}: train={len(train)} test={len(test)}')

    # ---- 対数尤度の比較（3連単の実現着順）
    rows = []
    ll = defaultdict(list)
    for R, dfull, dpt in pred:
        key = tuple(R['top3'])
        n = len(R['riders'])
        q = market_prob_realized(R)
        if q is None:
            continue
        ll['uniform'].append(-math.log(n * (n - 1) * (n - 2)))
        ll['pt'].append(math.log(dpt[key]))
        ll['full'].append(math.log(dfull[key]))
        ll['market'].append(math.log(min(q, 0.999)))
    n_eval = len(ll['full'])
    LL = {k: float(np.mean(v)) for k, v in ll.items()}

    # ---- 1着確率の較正（予測帯 × 実際の1着率）
    calib = defaultdict(lambda: [0, 0.0, 0])
    for R, dfull, _ in pred:
        win = defaultdict(float)
        for (a, b, c), p in dfull.items():
            win[a] += p
        for num, p in win.items():
            bkt = min(int(p * 10), 7)
            calib[bkt][0] += 1
            calib[bkt][1] += p
            calib[bkt][2] += int(num == R['top3'][0])

    # ---- 1点買い戦略（毎レース100円）: モデル本命 vs 市場1番人気
    strategies = {}
    types = ['3連単', '3連複', '2車単', '2車複', 'ワイド']
    for bt in types:
        bets = hits = 0
        ret = 0.0
        mk_hits = 0
        mk_ret = 0.0
        for R, dfull, _ in pred:
            if bt not in R['payoffs']:
                continue
            tp = ticket_probs(dfull)[bt]
            best = max(tp.items(), key=lambda kv: kv[1])[0]
            pay = realized_payoff(R, bt, best)
            bets += 1
            hits += pay > 0
            ret += pay
            # 市場1番人気の組が的中したか（払戻の人気=1）
            for combo, payoff, pop in R['payoffs'][bt]:
                if pop == 1:
                    mk_hits += 1
                    mk_ret += payoff
                    break
        strategies[bt] = dict(bets=bets, model_hits=hits, model_roi=ret / (100 * bets) if bets else 0,
                              market_hits=mk_hits, market_roi=mk_ret / (100 * bets) if bets else 0)

    # ---- 自信度フィルター（本命確率が高いレースだけ買う）
    conf = {}
    for bt in ['2車単', '2車複', '3連複', 'ワイド']:
        rows_bt = []
        for R, dfull, _ in pred:
            if bt not in R['payoffs']:
                continue
            tp = ticket_probs(dfull)[bt]
            best, pb = max(tp.items(), key=lambda kv: kv[1])
            rows_bt.append((pb, realized_payoff(R, bt, best)))
        rows_bt.sort(key=lambda x: -x[0])
        for frac in (0.1, 0.25, 0.5):
            sub = rows_bt[:max(1, int(len(rows_bt) * frac))]
            conf[(bt, frac)] = dict(bets=len(sub), hits=sum(1 for _, p in sub if p > 0),
                                    roi=sum(p for _, p in sub) / (100 * len(sub)), pmin=sub[-1][0])

    # ---- 市場融合 α（オッズと結果が重なるレースが30以上ある場合のみ）
    pred_by_id = {R['race_id']: dfull for R, dfull, _ in pred}
    fusion = fit_fusion(races, D.get('odds'), lambda R: pred_by_id.get(R['race_id']))

    # ---- 全データで最終学習 → ブラウザ版用の係数
    final = fit_model(races)
    model_json = {
        'version': 'v105' if USE_EXT else 'v104',
        'trained_on': {'races': len(races), 'date_min': dates[0], 'date_max': dates[-1]},
        'rider_features': RIDER_FEATURES, 'pair2_features': PAIR2_FEATURES, 'pair3_features': PAIR3_FEATURES,
        'w1': [round(float(x), 5) for x in final['w1']],
        'w2': [round(float(x), 5) for x in final['w2']],
        'w3': [round(float(x), 5) for x in final['w3']],
        'holdout': {'races': n_eval, 'logloss_3t': {k: round(-v, 4) for k, v in LL.items()}},
        'fusion_alpha': fusion['alpha'] if fusion else None,
        'fusion_beta': fusion.get('beta') if fusion else None,
        'fusion_type': fusion.get('type') if fusion else None,
        'fusion_races': fusion['races'] if fusion else 0,
    }
    # 安全装置: 今のモデルより少ないレース数で学習した結果では上書きしない（--force で強制）
    model_path = os.path.join(OUT_DIR, 'model_v104.json')
    if os.path.exists(model_path) and '--force' not in sys.argv:
        with open(model_path, encoding='utf-8') as f:
            prev_n = json.load(f).get('trained_on', {}).get('races', 0)
        if prev_n > len(races):
            print(f'[skip] 既存モデル（{prev_n}レース）より学習レースが少ない（{len(races)}）ため、モデルとレポートを更新しません。'
                  '収集リポジトリを --data で指定してください（強制する場合は --force）。')
            sys.exit(0)
    with open(model_path, 'w', encoding='utf-8') as f:
        json.dump(model_json, f, ensure_ascii=False, indent=1)

    # ---- 参照用: 1レース分の予測（ブラウザ版との一致確認用）
    R0 = races[-1]
    d0 = predict_dist(final, R0)
    top = sorted(d0.items(), key=lambda kv: -kv[1])[:10]
    with open(os.path.join(OUT_DIR, 'reference_race.json'), 'w', encoding='utf-8') as f:
        json.dump({'race_id': R0['race_id'], 'venue': R0['venue'], 'riders': R0['riders'],
                   'top10': [['-'.join(map(str, k)), v] for k, v in top]}, f, ensure_ascii=False, indent=1)

    # ---- レポート
    def fmt_pct(x):
        return f'{x * 100:.1f}%'
    L = []
    L.append('# v104 統計モデル 検証レポート\n')
    L.append(f'- 学習データ: {len(races)}レース（{dates[0]}〜{dates[-1]}、{len(dates)}日）')
    L.append(f'- 検証方式: ローリング（{test_days[0]}〜{test_days[-1]} の各{"月" if span > 60 else "日"}を、それより前のデータだけで学習して予測）')
    L.append(f'- 検証レース数: {n_eval}\n')
    L.append('## 予測精度（3連単の実際の着順に付けた確率の対数損失。小さいほど良い）\n')
    L.append('| モデル | 対数損失 | 一様比の改善 | 的中着順に付けた平均確率(幾何平均) |')
    L.append('|---|---:|---:|---:|')
    names = {'uniform': '一様（全組同確率）', 'pt': '得点だけのモデル', 'full': '**v104 統計モデル**', 'market': '市場（払戻から逆算）'}
    for k in ('uniform', 'pt', 'full', 'market'):
        L.append(f'| {names[k]} | {-LL[k]:.3f} | {LL[k] - LL["uniform"]:+.3f} | {fmt_pct(math.exp(LL[k]))} |')
    L.append('\n※ 市場の値は的中組の払戻×控除率25%から逆算した近似値。')
    L.append('\n## 1着確率の較正（予測した1着率 vs 実際の1着率）\n')
    L.append('| 予測帯 | 件数 | 予測平均 | 実際 |')
    L.append('|---|---:|---:|---:|')
    for b in sorted(calib):
        n, ps, h = calib[b]
        L.append(f'| {b * 10}〜{(b + 1) * 10 if b < 7 else 100}% | {n} | {fmt_pct(ps / n)} | {fmt_pct(h / n)} |')
    L.append('\n## 毎レース1点買い（100円）: モデル本命 vs 市場1番人気\n')
    L.append('| 券種 | レース数 | モデル的中率 | モデル回収率 | 1番人気的中率 | 1番人気回収率 |')
    L.append('|---|---:|---:|---:|---:|---:|')
    for bt, s in strategies.items():
        L.append(f'| {bt} | {s["bets"]} | {fmt_pct(s["model_hits"] / s["bets"])} | {fmt_pct(s["model_roi"])} | '
                 f'{fmt_pct(s["market_hits"] / s["bets"])} | {fmt_pct(s["market_roi"])} |')
    L.append('\n## 自信度フィルター（モデル本命の確率が高い上位のレースだけ買う）\n')
    L.append('| 券種 | 上位 | レース数 | 的中率 | 回収率 | 本命確率の下限 |')
    L.append('|---|---:|---:|---:|---:|---:|')
    for (bt, frac), s in conf.items():
        L.append(f'| {bt} | {int(frac * 100)}% | {s["bets"]} | {fmt_pct(s["hits"] / s["bets"])} | {fmt_pct(s["roi"])} | {fmt_pct(s["pmin"])} |')
    L.append('\n## 市場融合の重み α\n')
    if fusion and fusion['alpha'] is not None:
        L.append(f'オッズと結果が揃った {fusion["races"]} レースで推定: **{fusion["type"]} 型, α = {fusion["alpha"]:.3f}, β = {fusion["beta"]:.3f}**（ブラウザ版の既定値に反映）\n')
        bt = fusion.get('benter')
        if bt:
            L.append(f'- Benter型（f ∝ 市場^β × モデル^α）: α = {bt["alpha"]:.3f}, β = {bt["beta"]:.3f}, 平均対数尤度 {bt["ll"]:.4f}\n')
        L.append('| α | 線形プール 平均対数尤度 | 対数プール 平均対数尤度 |')
        L.append('|---:|---:|---:|')
        for al, v1, v2 in fusion['curve']:
            L.append(f'| {al:.2f} | {v1:.4f} | {v2:.4f} |')
    else:
        n_ov = fusion['races'] if fusion else 0
        L.append(f'オッズと結果が両方そろったレースが {n_ov} 件しかないため未推定（30件以上で自動推定）。'
                 'ブラウザ版は暫定値（線形プール, α=0.25）を使用。収集を続けて再実行すると自動で推定される。')
    L.append('\n## 係数（全データで学習した最終モデル）\n')
    L.append('| 特徴量 | 1着 | 2着 | 3着 |')
    L.append('|---|---:|---:|---:|')
    for i, f in enumerate(RIDER_FEATURES):
        L.append(f'| {f} | {final["w1"][i]:+.3f} | {final["w2"][i]:+.3f} | {final["w3"][i]:+.3f} |')
    d = len(RIDER_FEATURES)
    for i, f in enumerate(PAIR2_FEATURES):
        L.append(f'| 2着: {f} | | {final["w2"][d + i]:+.3f} | |')
    for i, f in enumerate(PAIR3_FEATURES):
        L.append(f'| 3着: {f} | | | {final["w3"][d + i]:+.3f} |')
    with open(os.path.join(OUT_DIR, 'report_v104.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print('\n'.join(L))


def embed_model(model_json):
    """index.html の const KEIRIN_V104_MODEL = {...}; を学習結果で置き換える"""
    import re
    path = os.path.join(ROOT, 'index.html')
    with open(path, encoding='utf-8') as f:
        html = f.read()
    new = 'const KEIRIN_V104_MODEL = ' + json.dumps(model_json, ensure_ascii=False, separators=(',', ':')) + ';\n'
    html2, n = re.subn(r'const KEIRIN_V104_MODEL = \{.*?\};\n', lambda m: new, html, count=1, flags=re.S)
    if n != 1:
        sys.exit('index.html に KEIRIN_V104_MODEL が見つかりません')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html2)
    print('[embed] index.html のモデル係数を更新しました')


if __name__ == '__main__':
    main()
    if '--embed' in sys.argv:
        with open(os.path.join(OUT_DIR, 'model_v104.json'), encoding='utf-8') as f:
            embed_model(json.load(f))
