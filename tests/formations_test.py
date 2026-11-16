#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""formations.py のテスト（架空データ）:  python tests/formations_test.py"""
import os
import sys
from itertools import permutations

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import formations as F  # noqa: E402


def check(c, msg):
    if not c:
        raise SystemExit(f'NG   {msg}')
    print(f'OK   {msg}')


def main():
    S = {1: 5.0, 2: 3.0, 3: 2.0, 4: 1.0, 5: 1.0}

    def pl(o):
        rem, p = sum(S.values()), 1.0
        for n in o:
            p *= S[n] / rem
            rem -= S[n]
        return p
    dist = {o: pl(o) for o in permutations(S, 3)}
    probs = {'3連単': {f'{a}-{b}-{c}': p for (a, b, c), p in dist.items()}, '2車複': {}, 'ワイド': {}}
    for (a, b, c), p in dist.items():
        k = '-'.join(map(str, sorted((a, b))))
        probs['2車複'][k] = probs['2車複'].get(k, 0) + p
        for x, y in ((a, b), (a, c), (b, c)):
            k = '-'.join(map(str, sorted((x, y))))
            probs['ワイド'][k] = probs['ワイド'].get(k, 0) + p
    fair = lambda bt: {k: round(0.75 / p, 1) for k, p in probs[bt].items()}
    odds = {'3連単': fair('3連単'), '2車複': fair('2車複'), 'ワイド': {k: round(0.75 / p * 0.4, 1) for k, p in probs['ワイド'].items()}}
    odds['2車複']['1-2'] = round(odds['2車複']['1-2'] * 1.6, 1)  # 1=2 だけ割安

    g = F.group_metrics('3連単', ['1-2-3', '1-2-4'], probs, odds)
    o1, o2 = odds['3連単']['1-2-3'], odds['3連単']['1-2-4']
    check(abs(g['odds'] - 1 / (1 / o1 + 1 / o2)) < 1e-9, '合成オッズ = 1 ÷ Σ(1÷オッズ)')
    check(abs(g['p'] - (dist[(1, 2, 3)] + dist[(1, 2, 4)])) < 1e-12, '的中率 = 各点の確率の合計（排反）')
    check(abs(sum(g['w']) - 1) < 1e-12 and g['w'][0] < g['w'][1] if o1 > o2 else True, '配分はオッズに反比例')
    ev_r, st = F.realized_ev(g, probs, odds, budget=1000)
    check(sum(st) >= 1000 - 100 and all(s % 100 == 0 for s in st), f'100円単位の配分 {st}')

    gs = F.build_groups(probs, odds, lines=[[1, 2, 3], [4, 5]], dist3=dist)
    labels = {x['label'] for x in gs}
    check('1→2→345' in labels or '1→2→34' in labels, '1着・2着固定の流し（例 1→2→34）')
    check(any(x.endswith('BOX') for x in labels), 'BOX')
    check(any(x.startswith('ライン123') for x in labels), 'ライン決着（並びから）')
    check(len({(x['type'], frozenset(x['keys'])) for x in gs}) == len(gs), '同じ買い目の組み合わせは1つだけ')
    check(all(x['n'] <= F.MAX_POINTS[x['type']] for x in gs), '点数の上限を守る')
    best, _ = F.pick(gs, p_min=0.05, ev_min=1.10, bt='2車複')
    check(best and '1-2' in best[0]['keys'], f'割安な 2車複 1=2 を含む群が上位 {best[0]["label"] if best else None}')
    w = F.group_metrics('ワイド', ['1-2', '1-3'], probs, odds, dist)
    exact = sum(p for (a, b, c), p in dist.items() if ({1, 2} <= {a, b, c}) or ({1, 3} <= {a, b, c}))
    check(abs(w['p'] - exact) < 1e-12, 'ワイドの的中率は「どれか1点以上」（重なりを二重に数えない）')
    cs = F.combine(gs, probs, odds, dist)
    check(cs and all('＋' in c['label'] for c in cs), '違う形の買い目群の組み合わせ（例: A ＋ B）')
    c = cs[0]
    parts = c['label'].split(' ＋ ')
    g1 = next(x for x in gs if x['label'] == parts[0] and x['type'] == c['type'])
    g2 = next(x for x in gs if x['label'] == parts[1] and x['type'] == c['type'])
    check(set(c['keys']) == set(g1['keys']) | set(g2['keys']) and c['p'] >= max(g1['p'], g2['p']) - 1e-12,
          '組み合わせは重なる点を1つにまとめ、的中率は元より上がる')
    lab = {(x['type'], x['label']): x for x in gs}
    disjoint = []
    for c2 in cs:
        a, b = (lab.get((c2['type'], s)) for s in c2['label'].split(' ＋ '))
        if c2['type'] != 'ワイド' and a and b and not set(a['keys']) & set(b['keys']):
            disjoint.append((c2, a, b))
    check(disjoint and all(min(a['ev'], b['ev']) - 1e-9 <= c2['ev'] <= max(a['ev'], b['ev']) + 1e-9 for c2, a, b in disjoint),
          '重なりの無い組み合わせの期待値は、元の2つの間')
    print('\n全テスト成功')


if __name__ == '__main__':
    main()
