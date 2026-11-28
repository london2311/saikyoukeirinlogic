#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""占い（六星占術・四柱推命）と選手の生年月日の取得のテスト"""
import csv
import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'analysis'))
import fortune as F  # noqa: E402
import rider_profile as RP  # noqa: E402


def test_calendar():
    assert F.name60(F.day_index(date(2000, 1, 1))) == '戊午'
    assert F.name60(F.day_index(date(2024, 1, 1))) == '甲子'  # 2024年元日は甲子の日
    assert F.name60(F.day_index(date(1949, 10, 1))) == '甲子'
    assert F.name60(F.year_index(date(2026, 6, 1))) == '丙午'
    assert F.name60(F.year_index(date(2026, 2, 3))) == '乙巳'  # 立春前は前の年
    assert F.name60(F.month_index(date(2024, 2, 10))) == '丙寅'
    assert F.name60(F.month_index(date(2025, 1, 10))) == '丁丑'
    assert F.name60(F.month_index(date(2026, 11, 18))) == '己亥'
    assert F.tenchusatsu(0) == (10, 11)  # 甲子旬 → 戌亥
    assert F.tenchusatsu(50) == (0, 1)   # 甲寅旬 → 子丑


def test_rokusei_cycle():
    # 火星人（午未）は 2025〜2027 が大殺界、2026 が停止
    b = date(1984, 2, 2)
    while F.rokusei(b)['star'] != '火星人':
        b = date.fromordinal(b.toordinal() + 1)
    ys = {y: F.fortune(b, date(y, 6, 1))['year'] for y in range(2024, 2029)}
    assert ys == {2024: '安定', 2025: '陰影', 2026: '停止', 2027: '減退', 2028: '種子'}
    f = F.fortune(b, date(2026, 6, 1))
    assert f['grade_year'] == '✕' and f['tcs_year']
    assert '大殺界' in F.text(f) and f['week']['good'] + f['week']['bad'] <= 7


def test_tsuhen():
    assert F.tsuhen(0, 0) == '比肩' and F.tsuhen(0, 1) == '劫財'
    assert F.tsuhen(0, 2) == '食神' and F.tsuhen(0, 4) == '偏財'
    assert F.tsuhen(0, 6) == '偏官' and F.tsuhen(0, 7) == '正官'
    assert F.tsuhen(0, 8) == '偏印' and F.tsuhen(0, 9) == '印綬'


def test_parse_birth():
    html = '<dl><dt>生年月日</dt><dd>1983/01/02（43歳）</dd></dl>'
    assert RP.parse_birth(html) == '1983-01-02'
    assert RP.parse_birth('<th>生年月日</th><td>1990年7月15日</td>') == '1990-07-15'
    assert RP.parse_birth('<p>登録 2010/05/01</p>') is None
    real = ('<div class="ProfileHeader_box_data">\n<span class="ProfileHeader_box_dataText">\n'
            '<a href="https://keirin.netkeiba.com/db/search_result/player.html?pid=player_list&age=4">\n1985/05/06(41歳)\n</a>\n</span>')
    assert RP.parse_birth(real) == '1985-05-06'
    assert RP.parse_birth('') is None


def test_birthdays_budget_and_stop():
    with tempfile.TemporaryDirectory() as d:
        calls = []

        def fetch(pid):
            calls.append(pid)
            return f'<dt>生年月日</dt><dd>1980/0{int(pid) % 9 + 1}/10</dd>'
        bd = RP.Birthdays(d, fetch=fetch, per_day=3)
        now = datetime(2026, 11, 18, 10)
        assert bd.fetch_some(['1', '2', '1'], 5, now) == 2
        assert bd.get('000001') == '1980-02-10'
        assert bd.fetch_some(['3', '4', '5'], 5, now) == 1  # 1日の上限 3
        assert bd.save() and RP.load(d)['000002']['birth']
        # 別の実行が先に書いた分は消さない・同じ選手は生年月日のある方
        other = RP.Birthdays(d, fetch=None)
        other.data = {'000009': dict(birth='1990-01-01', at='x', tries=1), '000001': dict(birth=None, at='x', tries=3)}
        other.dirty = True
        other.save()
        got = RP.load(d)
        assert got['000009']['birth'] == '1990-01-01' and got['000001']['birth'] == '1980-02-10' and got['000002']['birth']
        # union で合わせた（同じ選手が2行・見出しが2回）CSV も読める
        p = Path(d) / 'riders' / 'birthdays.csv'
        p.write_text(p.read_text() + 'player_id,birth,at,tries\n000002,,2026-11-19,2\n000077,1977-07-07,x,1\n')
        got = RP.load(d)
        assert got['000002']['birth'] and got['000077']['birth'] == '1977-07-07'
        # 読めないページが続いたら止める・確認用に数件だけ保存
        bad = RP.Birthdays(d + '/x', fetch=lambda p: '<html>no</html>', per_day=100)
        bad.fetch_some([str(i) for i in range(100, 160)], 60, now)
        assert bad.off and bad.stats['fetched'] == 20
        assert len(list((Path(d) / 'x' / 'riders' / '_samples').glob('*.html'))) == RP.SAMPLES
        assert bad.missing(['100']) == ['000100']  # 3回までは取り直す


def test_site_luck_and_study():
    import netkeirin_site as S
    sb = S.SiteBuilder.__new__(S.SiteBuilder)
    sb.date = '20261118'
    sb.birthdays = None
    assert sb._luck([{'car_number': '1', 'player_id': '000001'}]) == {}

    class B:
        def get(self, pid):
            return '1983-01-02' if pid == '000001' else None
    sb.birthdays = B()
    lk = sb._luck([{'car_number': '1', 'player_id': '000001'}, {'car_number': '2', 'player_id': '000002'}])
    assert list(lk) == ['1'] and '六星' in lk['1'][1] and isinstance(lk['1'][2], int)

    import fortune_study as FS
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / '2026'
        p.mkdir()
        with open(p / 'racecards.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['race_id', 'schedule_id', 'car_number', 'absent', 'player_id', 'race_point'])
            for c in range(1, 8):
                w.writerow(['R1', '2026111813', c, 0, f'{c:06d}', 100 - c])
        with open(p / 'results.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['race_id', 'car_number', 'order', 'accident'])
            for c in range(1, 8):
                w.writerow(['R1', c, c if c < 7 else 0, '' if c < 7 else '落車'])
        with open(p / 'odds_202611.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['race_id', 'bet_type', 'combination', 'odds'])
            w.writerow(['R1', '3連単', '1-2-3', 2.0])
            w.writerow(['R1', '3連単', '2-1-3', 8.0])
            w.writerow(['R1', '3連単', '7-1-2', 100.0])
        mk = FS.market_win(Path(d))
        assert abs(sum(mk['R1'].values()) - 1) < 1e-9 and mk['R1'][1] > 0.7
        r = FS.study(Path(d), {f'{c:06d}': {'birth': f'198{c}-03-0{c}'} for c in range(1, 8)})
        assert r['starts'] == 7 and set(r['sections']) == {'win', 'top3', 'fav_lose', 'long_win', 'accident'}
        assert '運気点' in r['sections']['win']['table']
        assert sum(v[1] for v in r['sections']['fav_lose']['table']['運気点']) == 1  # 1着確率45%以上は1番車だけ


def test_lines_of():
    import netkeirin_site as S
    cards = [dict(car_number=c, line=l, line_position=p, line_type='三分戦', absent=a)
             for c, l, p, a in [(4, 1, 1, 0), (1, 1, 2, 0), (2, 1, 3, 0), (3, 2, 1, 0), (7, 2, 2, 0), (5, 3, 1, 0), (6, 3, 2, 1)]]
    assert S.lines_of(cards) == ([[4, 1, 2], [3, 7], [5]], '三分戦')
    assert S.lines_of([dict(car_number=1, line='')]) == ([], '')


def test_nuance():
    import netkeirin_site as S
    t = S.nuance_text({'jr': [0.72, 25], 'bt': [0.05, 30], 'sg': [0.01, 9], 'fm': -0.2})
    assert '自力宣言で実際に動いた率 72%（25回）' in t and '5%多い' in t and '単騎' not in t and '0.2秒遅い' in t
    assert S.nuance_text({}) == '' and S.nuance_text({'sg': [0.01, 9]}) == ''
    import rider_nuance as RN
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / '2026'
        p.mkdir()
        with open(p / 'odds_202611.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['race_id', 'bet_type', 'combination', 'odds'])
            w.writerows([['R1', '3連単', '1-2-3', 2.0], ['R1', '3連単', '2-1-3', 4.0], ['R1', '3連単', '3-1-2', 4.0]])
        m = RN.market(Path(d))['R1']
        assert abs(sum(m['win'].values()) - 1) < 1e-9 and abs(m['top3'][1] - 1) < 1e-9 and abs(m['pair'][(1, 2)] - 0.75) < 1e-9
        st = RN.Stats()
        rs = [dict(car=1, pid='000001', comment='自力。', line='1', pos=1, size=2, single=False, order=1, sb=True, run=True, half=11.0),
              dict(car=2, pid='000002', comment='1番君。', line='1', pos=2, size=2, single=False, order=2, sb=False, run=False, half=11.2),
              dict(car=3, pid='000003', comment='単騎。', line='2', pos=1, size=1, single=True, order=3, sb=False, run=False, half=11.4)]
        st.update(rs, m)
        f1, f2 = st.feats('000001'), st.feats('000002')
        assert f1['jiriki_n'] == 1 and f1['jiriki_rate'] == 1.0 and f2['banete_n'] == 1 and f2['banete'] > 0


if __name__ == '__main__':
    for k, v in list(globals().items()):
        if k.startswith('test_'):
            v()
    print('fortune_test OK')
