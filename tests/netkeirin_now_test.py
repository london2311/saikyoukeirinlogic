#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""netkeirin_now.py のオフラインテスト（架空データ・通信なし）:  python tests/netkeirin_now_test.py"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import netkeirin_now as NOW  # noqa: E402

fails = []


def check(c, msg):
    print(('OK   ' if c else 'FAIL ') + msg)
    if not c:
        fails.append(msg)


LIST = '''<li id = "racing_color_209901019901" data-race_state = "2"><dd class="Race_Data"><span>発走 10:05</span></dd></li>
<li id = "racing_color_209901019902" data-race_state = "1"><dd class="Race_Data"><span>発走 10:35</span></dd></li>
<li id = "racing_color_209812319901"><span>発走 09:00</span></li>'''
check(NOW.parse_race_list(LIST, '20990101') == [('209901019901', '10:05'), ('209901019902', '10:35')], 'レース一覧から発走時刻')
check(NOW.buy_line(0.5) == 2.2 and NOW.buy_line(0.137) == 8.1 and NOW.buy_line(0.55) == 2.0, '買い基準オッズ = 1.10÷p を切り上げ')

res = dict(market_ok=True, hit_picks={
    'ワイド': [dict(key='1-2', prob=0.80, odds=1.5), dict(key='1-3', prob=0.40, odds=2.0)],
    '2車複': [dict(key='1-2', prob=0.30, odds=4.0), dict(key='2-3', prob=0.10, odds=None)]})
s = NOW.summarize(res)
check(s['picks']['ワイド'][0]['buy'] and s['picks']['ワイド'][0]['line'] == 1.4, 'ワイド 1.5倍 ≥ 基準1.4倍 → ★')
check(not s['picks']['ワイド'][1]['buy'] and s['picks']['2車複'][0]['buy'], '基準未満は★なし / 4.0 ≥ 3.7 → ★')
check(s['picks']['2車複'][1]['ev'] is None and not s['picks']['2車複'][1]['buy'], 'オッズなしは判定しない')
check([t for t, _ in s['hit']] == ['ワイド'], '的中率重視: ワイド p≥0.79 のみ該当')
res['market_ok'] = False
check(not any(r['buy'] for r in NOW.summarize(res)['picks']['ワイド']), '市場融合できない時は★を付けない')
print('全テスト成功' if not fails else f'失敗 {len(fails)} 件')
sys.exit(1 if fails else 0)
