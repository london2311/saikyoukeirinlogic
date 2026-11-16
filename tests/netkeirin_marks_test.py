#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本紙の印・3連単ユーザー支持率・本紙の買い目の解析と、印の取り直し（netkeirin_backfill）のオフラインテスト:
    python tests/netkeirin_marks_test.py

通信はせず、架空の HTML 片（実在しない選手・レース）だけを使う。
"""
import csv
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import netkeirin_backfill as B  # noqa: E402
import netkeirin_collector as N  # noqa: E402
import netkeirin_test as NT  # noqa: E402  架空の出走表 HTML を再利用

NK = NT.NK          # 209901019907（架空）
WT = '079920990101'

fails = []


def check(c, msg):
    print(('OK   ' if c else 'FAIL ') + msg)
    if not c:
        fails.append(msg)


def with_marks(html, marks):
    """架空の出走表の「本紙」列に印コードを入れる（車番順）"""
    parts = html.split('<td class="RaceCardCell01 Yoso01"><span style="display: none;">1</span></td>')
    out = parts[0]
    for code, rest in zip(marks, parts[1:]):
        out += f'<td class="RaceCardCell01 Yoso01"><span style="display: none;">{code}</span><svg class="x"></svg></td>' + rest
    return out


SUPPORT = '''<h2>3連単ユーザー支持率</h2><canvas id="myChart"></canvas>
<script>new Chart(document.getElementById("myChart"), { type: "horizontalBar", data: { labels: ["1着", "2着", "3着"],
datasets: [ { label: "1番 架空", data: [40.000000,30.500000,10.000000], backgroundColor: "x" },
{ label: "2番 試験", data: [25.000000,35.000000,20.000000], backgroundColor: "x" },
{ label: "3番 仮名", data: [35.000000,34.500000,70.000000], backgroundColor: "x" }, ] } });</script>'''


def box(n):
    return f'<div class="Shaban_InBox"><span class="Wakuban Waku{n}">{n}</span></div>'


PICKS = f'''<h2>netkeirin本紙の競輪予想</h2><section class="YosoDetailKenkai fc">
<div class="YosoKenkaiTxt">架空の見解の文章（保存しない）。</div>
<div class="OddsWrap">
<dl><dt>ワイド</dt><div><dd ><div class="Num">{box(3)}<span class="Hyphen"></span>{box(1)}</div>
<span class="OddsData01">1.4倍 &#xFF5E; 1.6倍</span></dd></div></dl>
<dl><dt>2車単</dt><div><dd ><div class="Num">{box(3)}<span class="Kaime_Arrow"></span>{box(1)}</div><span class="OddsData01">4.3倍</span></dd></div></dl>
<dl class="Trifecta"><dt>3連単</dt><div><dd ><div class="Num">{box(3)}<span class="Kaime_Arrow"></span>{box(1)}
<div class="Shaban_Other"><span class="Kaime_Arrow"></span>{box(2)}{box(1)}</div></div>
<span class="OddsData01">28.6倍 &#xFF5E; 61.8倍 計2点</span></dd></div></dl>
</div><a class="Button_01 off">投票締め切り</a><div class="YosokaLinkBtn01"></div></section>'''

ENTRY = with_marks(NT.ENTRY_HTML, [2, 100, 1]).replace('</body>', SUPPORT + PICKS + '</body>')


def test_parse():
    c = N.parse_racecard(ENTRY, NK)
    by = {r['car_number']: r for r in c}
    check((by[1]['hs_mark'], by[2]['hs_mark'], by[3]['hs_mark']) == (2, 100, 1), '本紙の印（1=◎ 2=○ 100=無印）')
    check((by[1]['sup1'], by[1]['sup2'], by[1]['sup3']) == (40.0, 30.5, 10.0), '3連単ユーザー支持率（1着・2着・3着）')
    check(by[3]['sup3'] == 70.0, '支持率 3番の3着')
    plain = N.parse_racecard(NT.ENTRY_HTML.replace('Yoso01', 'Other'), NK)
    check(plain[0]['hs_mark'] == '' and plain[0]['sup1'] == '', '本紙・支持率が無いページは空欄')
    p = N.parse_honshi_picks(ENTRY, NK)
    got = [(r['bet_type'], r['combination'], r['kind']) for r in p]
    check(('ワイド', '1-3', '1点:3-1') in got, 'ワイドは昇順の組、元表記は kind に')
    check(('2車単', '3-1', '1点:3→1') in got, '2車単は順序どおり')
    t3 = [g for g in got if g[0] == '3連単']
    check([g[1] for g in t3] == ['3-1-2'] and t3[0][2] == 'フォーメーション:3→1→2,1',
          '3連単フォーメーションを展開（同じ車番の重なりは除く）')
    check(all(r['race_id'] == WT and r['date'] == '20990101' for r in p), '買い目の race_id・date')
    check(all('見解' not in str(v) and '架空の見解' not in str(v) for r in p for v in r.values()), '見解の文章は保存しない')
    check(N.expand_pick('3連複', [[1], [2, 3], [2, 3, 4]]) == ['1-2-3', '1-2-4', '1-3-4'], '順不同の券種は重複をまとめる')


def write_races(d: Path, rows):
    p = d / '2099' / 'races.csv'
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['race_id', 'date', 'cancel'])
        w.writeheader()
        w.writerows(rows)


def test_backfill_marks():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        write_races(d, [dict(race_id=WT, date='20990101', cancel=0),
                        dict(race_id='089920990101', date='20990101', cancel=0),   # 取得失敗にするレース
                        dict(race_id='099920990101', date='20990101', cancel=1),   # 中止
                        dict(race_id='019920981231', date='20981231', cancel=0)])  # 期間外
        rc = d / '2099' / 'racecards.csv'
        rc.write_text('race_id,car_number,race_point\n' + WT + ',1,90\n', encoding='utf-8-sig')
        before = rc.read_bytes()
        prog = {}
        todo = B.marks_todo(d, prog, '20990101', '20990101')
        check([r for _, r in todo] == [WT, '089920990101'], '対象選び: 期間内・中止でない・marks.csv に無いレース')

        calls = []

        def fake_fetch(url, params=None, interval=0, data=None):
            calls.append(params['race_id'])
            if params['race_id'] == '209901019908':
                raise ValueError('架空の失敗')
            return ENTRY
        orig = N.fetch
        N.fetch = fake_fetch
        try:
            st = B.refetch_marks(d, prog, '20990101', '20990101', time.monotonic() + 60)
            check(st['ok'] == 1 and st['failed'] == 1 and calls == [NK, '209901019908'], '1レース1リクエスト（出走表のみ）')
            with open(d / '2099' / 'marks.csv', encoding='utf-8-sig') as f:
                m = list(csv.DictReader(f))
            check(len(m) == 3 and list(m[0].keys()) == N.MARKS_FIELDS and m[0]['race_id'] == WT, 'marks.csv の列・行')
            check((d / '2099' / 'honshi_picks.csv').exists(), 'honshi_picks.csv に追記')
            check(rc.read_bytes() == before, 'racecards.csv は書き換えない')
            calls.clear()
            st2 = B.refetch_marks(d, prog, '20990101', '20990101', time.monotonic() + 60)
            check(calls == [] and st2['todo'] == 0, '二重取得しない（取得済み・失敗記録済みは対象外）')
            check('089920990101' in prog.get('_marks_failed', {}), '失敗したレースは記録して繰り返さない')
            calls.clear()
            B.refetch_marks(d, {}, '20990101', '20990101', time.monotonic() - 1)
            check(calls == [], '時間切れなら取得しない')
        finally:
            N.fetch = orig


def test_collect_writes_marks():
    import json
    pages = {'api_race_list': NT.LIST_HTML.replace('209901019902', NK).replace('209901019901', NK),
             '/race/entry/': ENTRY, '/race/result/': NT.RESULT_HTML}

    def fake_fetch(url, params=None, interval=0, data=None):
        if '/api/race/' in url:
            return json.dumps(NT._fake_odds_api())
        for k, v in pages.items():
            if k in url:
                return v
        raise AssertionError(url)
    orig = N.fetch
    N.fetch = fake_fetch
    try:
        with tempfile.TemporaryDirectory() as tmp:
            N.collect_date('20990101', Path(tmp))
            y = Path(tmp) / '2099'
            with open(y / 'racecards.csv', encoding='utf-8-sig') as f:
                header = next(csv.reader(f))
            with open(y / 'marks.csv', encoding='utf-8-sig') as f:
                m = list(csv.DictReader(f))
            check('hs_mark' not in header and 'sup1' not in header, '毎日の収集: racecards.csv に印の列を増やさない')
            check(len(m) == 3 and {r['hs_mark'] for r in m} == {'1', '2', '100'}, '毎日の収集: marks.csv に印を書く')
            check((y / 'honshi_picks.csv').exists(), '毎日の収集: honshi_picks.csv に買い目を書く')
    finally:
        N.fetch = orig


if __name__ == '__main__':
    for t in (test_parse, test_backfill_marks, test_collect_writes_marks):
        print(f'--- {t.__name__}')
        t()
    print('全テスト成功' if not fails else f'失敗 {len(fails)} 件')
    sys.exit(1 if fails else 0)
