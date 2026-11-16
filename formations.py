#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
formations.py — 複数点の買い目（流し・フォーメーション・BOX・ライン決着）を作り、合成オッズと期待値で評価する

  groups = build_groups(probs, odds, lines=[[1,2,3],[4,5,6],[7]])
    probs: {券種: {組: 当たる確率}}（3連単の確率から集計したもの。合計1）
    odds : {券種: {組: 今のオッズ}}（ワイドは最低倍率）

評価（3連単・3連複・2車単・2車複 は1レースで当たりが1組だけなので、点どうしは排反）:
  * 的中率 P = 各点の確率の合計
  * 合成オッズ O = 1 ÷ Σ(1 ÷ 各点のオッズ)   … どれが当たっても払戻がほぼ同じになるよう、オッズに反比例して賭けた場合
  * 期待値 EV = P × O
  * 資金配分: 各点に「(1÷オッズ) ÷ Σ(1÷オッズ)」の割合で賭ける（100円単位に丸める）
ワイド（当たりが最大3組）は点ごとに独立に当たるので、同額買いの期待値（各点の確率×オッズの平均）で評価し、
的中率は「どれか1点以上当たる確率」を3連単の確率から計算する。

注意: たくさんの組み合わせの中から期待値が最大のものを選ぶと、確率の誤差を拾って期待値を高く見積もりやすい。
組み合わせ買いは「当たりやすさ」と「払戻の安定」を変えるもので、1点ごとの割安さ以上の利益を生むものではない。
"""
from itertools import combinations, permutations

MAX_POINTS = {'3連単': 20, '3連複': 15, '2車単': 10, '2車複': 10, 'ワイド': 6}


def _sorted_key(xs):
    return '-'.join(map(str, sorted(xs)))


def group_metrics(bt, keys, probs, odds, dist3=None):
    """keys の買い目群の評価。オッズが欠けている点があれば None"""
    keys = list(dict.fromkeys(keys))
    if not keys or any(k not in odds.get(bt, {}) for k in keys):
        return None
    o = [odds[bt][k] for k in keys]
    p = [probs[bt].get(k, 0.0) for k in keys]
    if bt == 'ワイド':
        ev = sum(pi * oi for pi, oi in zip(p, o)) / len(keys)
        hit = p[0] if len(keys) == 1 else wide_hit_prob(keys, dist3) if dist3 else max(p)
        return dict(type=bt, keys=keys, n=len(keys), p=hit, odds=None, ev=ev, w=[1.0 / len(keys)] * len(keys),
                    omin=min(o), omax=max(o))
    inv = [1.0 / x for x in o]
    s = sum(inv)
    syn = 1.0 / s
    P = sum(p)
    return dict(type=bt, keys=keys, n=len(keys), p=P, odds=syn, ev=P * syn, w=[x / s for x in inv], omin=min(o), omax=max(o))


def wide_hit_prob(keys, dist3):
    pairs = [tuple(int(x) for x in k.split('-')) for k in keys]
    tot = 0.0
    for (a, b, c), pr in dist3.items():
        top = {a, b, c}
        if any(x in top and y in top for x, y in pairs):
            tot += pr
    return tot


def stakes(g, budget=1000, unit=100):
    """合成オッズの配分を unit 円単位に丸めた賭け金（合計はおおむね budget）"""
    raw = [w * budget for w in g['w']]
    st = [max(unit, int(round(x / unit)) * unit) for x in raw]
    return st


def realized_ev(g, probs, odds, budget=1000, unit=100):
    """丸めた賭け金での期待値（払戻の期待値 ÷ 賭け金の合計）と賭け金。ワイドは同額"""
    bt = g['type']
    st = [unit] * g['n'] if bt == 'ワイド' else stakes(g, budget, unit)
    exp = sum(s * odds[bt][k] * probs[bt].get(k, 0.0) for s, k in zip(st, g['keys']))
    return exp / sum(st), st


def build_groups(probs, odds, lines=None, dist3=None):
    """券種ごとに候補の買い目群を作って評価する。戻り値: [group dict, ...]（label 付き）"""
    out = []
    seen = {}

    def add(bt, label, keys):
        keys = [k for k in dict.fromkeys(keys)]
        if not keys or len(keys) > MAX_POINTS[bt]:
            return
        sig = (bt, frozenset(keys))
        if sig in seen:  # 同じ買い目の組み合わせ: ライン決着などの呼び名だけ付け足す
            g = seen[sig]
            if g is not None and label.startswith('ライン') and label not in g['label']:
                g['label'] = f'{label}（{g["label"]}）'
            return
        g = group_metrics(bt, keys, probs, odds, dist3)
        seen[sig] = g
        if g:
            g['label'] = label
            out.append(g)

    p3 = probs.get('3連単', {})
    cars = sorted({int(x) for k in p3 for x in k.split('-')})
    win = {c: 0.0 for c in cars}
    for k, p in p3.items():
        win[int(k.split('-')[0])] += p

    def p3k(a, b, c):
        return p3.get(f'{a}-{b}-{c}', 0.0)

    # ---- ライン決着（並びが分かる場合）: 同じラインの選手で決まる組
    for line in lines or []:
        line = [c for c in line if c in win]
        if len(line) >= 3:
            add('3連単', f'ライン{"".join(map(str, line[:3]))} 決着（並び順）', [f'{line[0]}-{line[1]}-{line[2]}'])
            add('3連単', f'ライン{"".join(map(str, line[:3]))} 決着（順不同）', [f'{x}-{y}-{z}' for x, y, z in permutations(line[:3])])
            add('3連複', f'ライン{"".join(map(str, line[:3]))} 決着', [_sorted_key(line[:3])])
        if len(line) >= 2:
            add('2車単', f'ライン{line[0]}{line[1]} 決着', [f'{line[0]}-{line[1]}', f'{line[1]}-{line[0]}'])
            add('2車複', f'ライン{line[0]}{line[1]} 決着', [_sorted_key(line[:2])])
    # ---- 3連単
    if '3連単' in odds:
        for a in cars:
            for b in cars:
                if b == a:
                    continue
                rest = sorted((c for c in cars if c not in (a, b)), key=lambda c: -p3k(a, b, c))
                for k in range(1, 6):  # A→B→（相手 k 頭）
                    add('3連単', f'{a}→{b}→{"".join(map(str, rest[:k]))}', [f'{a}-{b}-{c}' for c in rest[:k]])
            for b, c in combinations([x for x in cars if x != a], 2):  # A→BC→BC＋相手
                others = sorted((x for x in cars if x not in (a, b, c)), key=lambda x: -(p3k(a, b, x) + p3k(a, c, x)))
                for k in range(0, 4):
                    third = [b, c] + others[:k]
                    keys = [f'{a}-{s}-{t}' for s in (b, c) for t in third if t != s]
                    add('3連単', f'{a}→{b}{c}→{"".join(map(str, third))}', keys)
            part = sorted((x for x in cars if x != a), key=lambda x: -sum(p3k(a, x, y) + p3k(a, y, x) for y in cars))
            for k in (3, 4, 5):  # A→相手 k 頭→相手 k 頭
                T = part[:k]
                add('3連単', f'{a}→{"".join(map(str, T))}→{"".join(map(str, T))}', [f'{a}-{x}-{y}' for x, y in permutations(T, 2)])
        for a, b in combinations(cars, 2):  # AB→AB→相手（1・2着の裏表）
            rest = sorted((c for c in cars if c not in (a, b)), key=lambda c: -(p3k(a, b, c) + p3k(b, a, c)))
            for k in range(1, 5):
                add('3連単', f'{a}{b}→{a}{b}→{"".join(map(str, rest[:k]))}',
                    [f'{x}-{y}-{c}' for x, y in ((a, b), (b, a)) for c in rest[:k]])
        for a in cars:  # 1頭軸マルチ（軸が何着でもよい）: 相手2頭=6点、相手3頭=18点
            part = sorted((x for x in cars if x != a), key=lambda x: -sum(p for k, p in p3.items() if str(a) in k.split('-') and str(x) in k.split('-')))
            for k in (2, 3):
                T = part[:k]
                keys = [f'{x}-{y}-{z}' for b, c in combinations(T, 2) for x, y, z in permutations((a, b, c))]
                add('3連単', f'{a}軸マルチ（相手{"".join(map(str, T))}）', keys)
        for a, b in combinations(cars, 2):  # 2頭軸マルチ: 相手1頭=6点、2頭=12点、3頭=18点
            rest = sorted((c for c in cars if c not in (a, b)), key=lambda c: -sum(p3k(*o) for o in permutations((a, b, c))))
            for k in (1, 2, 3):
                keys = [f'{x}-{y}-{z}' for c in rest[:k] for x, y, z in permutations((a, b, c))]
                add('3連単', f'{a}{b}軸マルチ（相手{"".join(map(str, rest[:k]))}）', keys)
        for T in combinations(cars, 3):  # 3頭BOX
            add('3連単', f'{"".join(map(str, T))} BOX', [f'{x}-{y}-{z}' for x, y, z in permutations(T)])
    # ---- 3連複
    if '3連複' in odds:
        p = probs.get('3連複', {})
        for a, b in combinations(cars, 2):  # 2頭軸流し
            rest = sorted((c for c in cars if c not in (a, b)), key=lambda c: -p.get(_sorted_key((a, b, c)), 0))
            for k in range(1, 6):
                add('3連複', f'{a}={b}={"".join(map(str, rest[:k]))}', [_sorted_key((a, b, c)) for c in rest[:k]])
        for a in cars:  # 1頭軸流し
            part = sorted((x for x in cars if x != a), key=lambda x: -sum(p.get(_sorted_key((a, x, y)), 0) for y in cars if y not in (a, x)))
            for k in (3, 4, 5):
                add('3連複', f'{a}軸={"".join(map(str, part[:k]))}', [_sorted_key((a, x, y)) for x, y in combinations(part[:k], 2)])
        for k in (4, 5):
            for T in combinations(cars, k):
                add('3連複', f'{"".join(map(str, T))} BOX', [_sorted_key(c) for c in combinations(T, 3)])
    # ---- 2車単・2車複・ワイド
    for bt in ('2車単', '2車複', 'ワイド'):
        if bt not in odds:
            continue
        p = probs.get(bt, {})
        key = (lambda a, b: f'{a}-{b}') if bt == '2車単' else (lambda a, b: _sorted_key((a, b)))
        sep = '→' if bt == '2車単' else '='
        for a in cars:
            rest = sorted((x for x in cars if x != a), key=lambda x: -p.get(key(a, x), 0))
            for k in range(1, 6):
                add(bt, f'{a}{sep}{"".join(map(str, rest[:k]))}', [key(a, x) for x in rest[:k]])
        if bt == '2車単':
            for a, b in combinations(cars, 2):
                add(bt, f'{a}⇔{b}', [f'{a}-{b}', f'{b}-{a}'])
        else:
            for k in (3, 4):
                for T in combinations(cars, k):
                    add(bt, f'{"".join(map(str, T))} BOX', [_sorted_key(c) for c in combinations(T, 2)])
    return out


def combine(groups, probs, odds, dist3=None, top=20, p_seed=0.03):
    """違う形の買い目群を2つ組み合わせた候補（例: 1→2→345 ＋ 4→5→136）。
    券種ごとに期待値の高い群（的中率 p_seed 以上）上位 top 個から2つを選び、重なる点は1つにまとめて合成オッズで評価する。
    組み合わせると的中率は上がり、期待値は元の2つの間になる（的中率を上げても期待値を保てる組を探すのに使う）"""
    out = []
    by_type = {}
    for g in groups:
        if g['p'] >= p_seed and '＋' not in g.get('label', ''):
            by_type.setdefault(g['type'], []).append(g)
    for bt, gs in by_type.items():
        gs = sorted(gs, key=lambda g: (-g['ev'], g['n']))[:top]
        seen = {frozenset(g['keys']) for g in gs}
        for g1, g2 in combinations(gs, 2):
            keys = list(dict.fromkeys(g1['keys'] + g2['keys']))
            sig = frozenset(keys)
            if len(keys) > MAX_POINTS[bt] or sig in seen or sig == frozenset(g1['keys']) or sig == frozenset(g2['keys']):
                continue
            if set(g1['keys']) <= set(g2['keys']) or set(g2['keys']) <= set(g1['keys']):
                continue
            seen.add(sig)
            m = group_metrics(bt, keys, probs, odds, dist3)
            if m:
                m['label'] = f'{g1["label"]} ＋ {g2["label"]}'
                out.append(m)
    return out


def prune(g, probs, odds, p_floor, dist3=None):
    """買い目を切って期待値を上げる。的中率が p_floor を下回らない範囲で、期待値の低い点から外す。
    合成オッズの配分では、ある点の「確率×オッズ」が群の期待値より低ければ、その点を外すと群の期待値は上がる
    （ワイドは同額買いなので、平均より低い点を外すと上がる）。改善しなければ None。
    戻り値: 切った後の群（'cut' に外した買い目）"""
    bt = g['type']
    keys = sorted(g['keys'], key=lambda k: -probs[bt].get(k, 0.0) * odds[bt][k])
    cur = g
    cut = []
    while len(keys) > 1:
        k = keys[-1]
        if probs[bt].get(k, 0.0) * odds[bt][k] >= cur['ev']:
            break
        trial = group_metrics(bt, keys[:-1], probs, odds, dist3)
        if trial is None or trial['p'] < p_floor or trial['ev'] <= cur['ev']:
            break
        keys = keys[:-1]
        cut.append(k)
        cur = trial
    if not cut:
        return None
    out = dict(cur)
    out['cut'] = cut
    out['label'] = g.get('label', '')
    return out


def pick(groups, p_min=0.15, ev_min=1.10, top=5, bt=None):
    """条件に合う買い目群を期待値の高い順に"""
    g = [x for x in groups if (bt is None or x['type'] == bt) and x['p'] >= p_min]
    g.sort(key=lambda x: (-x['ev'], x['n']))
    return [x for x in g if x['ev'] >= ev_min][:top], g[:top]
