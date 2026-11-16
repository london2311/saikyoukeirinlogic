#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ガールズの補正（keirin_engine.girls_adjust）と、ガールズ限定の買い方のテスト（架空レース）:  python tests/girls_test.py"""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
sys.path.insert(0, os.path.join(ROOT, 'analysis'))
import keirin_collector as C  # noqa: E402
import keirin_engine as E  # noqa: E402
import now_test as NT  # noqa: E402
import reasons  # noqa: E402
import snapshot_ledger as SL  # noqa: E402


def check(c, msg):
    if not c:
        raise SystemExit(f'NG   {msg}')
    print(f'OK   {msg}')


def cond2(res, fav):
    tri = {t['key']: t['prob'] for t in res['tickets'] if t['type'] == '3連単'}
    blk = {k: v for k, v in tri.items() if k.startswith(f'{fav}-')}
    tot = sum(blk.values())
    out = {}
    for k, v in blk.items():
        b = int(k.split('-')[1])
        out[b] = out.get(b, 0.0) + v / tot
    return out, tot


def main():
    _, _, full, _ = NT.build_states()
    cards, odds = C.parse_racecard(full), C.parse_odds(full)
    model = E.load_model()
    base = E.analyze(model, cards, odds, '取手', {})
    check(not base['girls'] and base['girls_adj'] is None, '男子のレースは補正しない')
    g_cards = copy.deepcopy(cards)
    for r in g_cards:
        r['class'] = 4
    g = E.analyze(model, g_cards, odds, '取手', {})
    check(g['girls'] and g['girls_adj'] is not None, 'ガールズ（全員 L級）を見分ける')
    fav = g['girls_adj']['fav']
    c0, t0 = cond2(base, fav)
    c1, t1 = cond2(g, fav)
    check(abs(t0 - t1) < 1e-9, f'1番人気（{fav}番）が勝つ確率は変えない')
    pts = sorted(((float(r['race_point']), int(r['car_number'])) for r in g_cards if str(r.get('absent', 0)) != '1'), key=lambda x: (-x[0], x[1]))
    weak = {c for i, (_, c) in enumerate(pts) if i >= 3 and c != fav}
    nige = {int(r['car_number']) for r in g_cards if r.get('style') == '逃'} - {fav}
    check(set(g['girls_adj']['up']) == weak - nige and set(g['girls_adj']['down']) == nige - weak, f'得点4位以下が上げ {sorted(weak)}、逃げ型が下げ {sorted(nige)}')
    for b in g['girls_adj']['up']:
        check(c1[b] > c0[b], f'{b}番: 1番人気が勝つ時の2着 {c0[b]:.3f} → {c1[b]:.3f}')
    for b in g['girls_adj']['down']:
        check(c1[b] < c0[b], f'{b}番（逃）: {c0[b]:.3f} → {c1[b]:.3f}')
    notes, _ = reasons.rider_notes(g_cards, g)
    check(any('ガールズ' in n for n in notes[fav]), '選手メモにガールズの説明')
    s = dict(id='G', bet_type='2車複', signal='EV_f', min=0.0, girls=True)
    check(SL.select(s, base['tickets'], base) == [] and len(SL.select(s, g['tickets'], g)) > 0, 'ガールズ限定の買い方はガールズのレースだけ')
    print('\n全テスト成功')


if __name__ == '__main__':
    main()
