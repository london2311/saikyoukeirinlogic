#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netkeirin_collector.py のオフラインテスト:  python tests/netkeirin_test.py

通信はせず、netkeirin のページと同じ形の「架空の」HTML（実在しない選手・レース）で
解析処理と CSV 保存を確認する。実データは使わない・保存しない。
"""
import csv
import os
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import keirin_collector as KC  # noqa: E402
import netkeirin_collector as N  # noqa: E402

NK = "209901019907"  # 架空: 2099/01/01・場コード99・7R
DATE = "20990101"


def entry_row(car, waku, pid, name, yomi, pref, age, term, klass, nums, style, comment):
    """nums = [得点, S, H, B, 逃, 捲, 差, マ, 1着, 2着, 3着, 着外, 勝率, 2連対率, 3連対率, ギヤ]"""
    return f'''<tr class="PlayerList " id="player-{car}">
<td class="RaceCardCell01">{waku}</td><td class="RaceCardCell01 Waku{waku}">{car}</td>
<td class="Player_Select"></td><td class="RaceCardCell01 Yoso01"><span style="display: none;">1</span></td>
<td class="Player_Info"><a href="javascript:void(0);" class="Player01" id="name_{pid}_{NK}">
<div class="Player_InfoWrap DispA"><span style="display: none;">{yomi}</span>
<dl><dt class="PlayerName">
{name}
<img id="Note_{pid}" class="Icon_Note" src="x.png" style="display: none;"></dt>
<div class="bottomPlayerInfo"><dd class="PlayerFrom"><span class="">{pref}</span> {age}歳</dd>
<dd class="PlayerClass">{term}期 {klass}</dd></div></dl></div></a></td>
<td class="RaceCardCell01 GroupLeft"><span class="">{nums[0]}</span></td>
<td class="RaceCardCell01 GroupLeft"><span style="display: none;">1</span>{style}</td>
{"".join(f'<td class="RaceCardCell01"><span class="">{v}</span></td>' for v in nums[1:11])}
<td class="RaceCardCell01">{nums[11]}</td>
<td class="RaceCardCell01"><span class="">{nums[12]}%</span></td>
<td class="RaceCardCell01"><span class="">{nums[13]}%</span></td>
<td class="RaceCardCell01"><span class="">{nums[14]}%</span></td>
<td class="RaceCardCell01 GroupLeft">
{nums[15]}
</td>
<td class="RaceCardCell01 GroupLeft Txt_L">{comment}</td>
</tr>'''


HEAD = ''.join(f'<th class="RaceCard_Th02"><div class="ThWrap"><span class="RaceCard_Th02_Txt">{h}</span></div></th>'
               for h in ["競走<br>得点", "脚質", "S", "H", "B", "逃げ", "まくり", "差し", "マーク", "1着", "2着",
                         "3着", "着外", "勝率", "2連対率", "3連対率", "ギヤ<br>倍数", "選手コメント"])

ENTRY_HTML = f'''<html><body>
<!-- block=race_title (cp) -->
<table id="RaceCard_Table_Static" class="RaceCard_Table">
<thead><tr class="Header"><th>枠</th><th>車</th><th></th><th>本紙</th>
<th class="Player_Info">選手名</th>{HEAD}</tr></thead><tbody>
{entry_row(1, 1, 99001, "架空一郎", "カクウイチロウ", "神奈", 30, 101, "Ｓ1",
           [95.5, 2, 1, 3, 4, 1, 0, 0, 5, 3, 2, 10, 25.0, 40.0, 50.0, 3.92],
           "逃", "自力。")}
{entry_row(2, 2, 99002, "試験次郎", "シケンジロウ", "和歌", 41, 88, "Ｓ2",
           [90.1, 0, 0, 0, 0, 0, 3, 2, 1, 4, 3, 12, 5.0, 25.0, 40.0, 3.93],
           "追", "1番手へ。")}
{entry_row(3, 3, 9903, "仮名三郎", "カメイサブロウ", "東京", 25, 125, "ＳＳ",
           [99.9, 5, 6, 7, 8, 2, 0, 0, 9, 4, 1, 6, 45.0, 65.0, 70.0, 4.0],
           "両", "自在に。")}
</tbody></table>
<!-- block=narabi_yoso (cp) -->
<section class="Contents_Box DeployYoso"><div class="DeployBox">
<div class="DeployInBox"><div class="Shaban_InBox"><span class="Waku1 Shaban_Num">1</span><span class="Name">架空</span></div></div>
<div class="DeployInBox"><div class="Shaban_InBox"><span class="Waku2 Shaban_Num">2</span><span class="Name">試験</span></div></div>
<div class="DeployInBox"><div class="Shaban_InBox"><span class="WakuSeparat"></span><span class="Name"></span></div></div>
<div class="DeployInBox"><div class="Shaban_InBox"><span class="Waku3 Shaban_Num">3</span><span class="Name">仮名</span></div></div>
</div></section>
</body></html>'''


def result_row(rank, waku, car, pid, name, margin, agari, factor, sb):
    return f'''<tr class="PlayerList">
<td class="RaceCardCell01 Result_Num">{rank}</td><td class="RaceCardCell01 WakbanCell">{waku}</td>
<td class="RaceCardCell01 Waku{waku}">{car}</td>
<td class="Player_Info"><a href="https://keirin.netkeiba.com/db/profile/?id={pid}" class="Player01">
<dl><dt class="PlayerName">
{name}
<!--<span class="FavIcon01"></span>--></dt></dl></a></td>
<td class="RaceCardCell01">{margin}</td><td class="RaceCardCell01">{agari}</td>
<td class="RaceCardCell01">{factor}</td><td class="RaceCardCell01">{sb}</td></tr>'''


RESULT_HTML = f'''<html><body>
<!-- block=race_title (cp) -->
<ul class="DayChange_List">
<li><a href="https://keirin.netkeiba.com/race/race_list/?kaisai_date=20981231&jyocd=99" class=""><span style="color:#fff;">12/31<br>初日</span></a></li>
<li><a href="https://keirin.netkeiba.com/race/race_list/?kaisai_date=20990101&jyocd=99" class=""><span style="color:#fff;">1/1<br>最終日</span></a></li>
</ul>
<div class="Race_Group"><a href="#"><span class="Icon_GradeType Icon_GradeType5">FI</span>
<span class="Race_GroupName">架空記念杯</span></a></div>
<div class="Race_Name">Ａ級 チャレンジ予選<span class="jsSwitchArrow"></span></div>
<div class="Race_Data">発走 21:35 締切 21:30 1625m 4周</div>
<!-- block=result (d) -->
<table summary="全着順" class="RaceCard_Table" id="All_Result_Table"><tbody>
<tr class="Header"><th>着</th><th>枠番</th><th>車番</th><th>選手名</th><th>着差</th><th>上り</th><th>決</th><th>SB</th></tr>
{result_row("1着", 3, 3, 9903, "仮名三郎", "", "11.2", "ク", "B")}
{result_row("2着", 1, 1, 99001, "架空一郎", "1車身", "11.5", "", "S")}
{result_row("3着", 2, 2, 99002, "試験次郎", "1/2車輪", "11.4", "", "")}
</tbody></table>
<table summary="払戻し" class="Payout_Detail_Table"><tbody>
<tr class="Umaren"><th rowspan="1">２車複</th><td class="Result">1-3</td><td class="Payout"><span>350円</span></td><td class="Ninki"><span>1人気</span></td></tr>
<tr class="Wide"><th rowspan="2">ワイド</th><td class="Result">1-3</td><td class="Payout"><span>150円</span></td><td class="Ninki"><span>1人気</span></td></tr>
<tr class="Wide"><td class="Result">2-3</td><td class="Payout"><span>210円</span></td><td class="Ninki"><span>2人気</span></td></tr>
<tr class="Tan3"><th rowspan="1">３連単</th><td class="Result">3&gt;1&gt;2</td><td class="Payout"><span>1,230円</span></td><td class="Ninki"><span>4人気</span></td></tr>
</tbody></table>
</body></html>'''

LIST_HTML = '''<ul class="jyo_tab"><li id="btn_jyoname_99"><a>架空</a></li><li id="btn_jyoname_98"><a>試験</a></li></ul>
<a href="https://keirin.netkeiba.com/race/result/?race_id=209901019901&rf=racellist">1R</a>
<a href="https://keirin.netkeiba.com/race/entry/?race_id=209901019901&rf=racellist_inmenu">1R</a>
<a href="https://keirin.netkeiba.com/race/result/?race_id=209901019902">2R</a>
<a href="https://keirin.netkeiba.com/race/result/?race_id=209812319901">前日</a>'''

failures = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


def test_ids_and_list():
    check(N.to_wt_race_id(NK) == "079920990101", "race_id を既存形式 RRVVYYYYMMDD に変換")
    check(N.parse_venue_codes(LIST_HTML) == ["99", "98"], "レース一覧から開催場コード")
    check(N.parse_race_ids(LIST_HTML, DATE) == ["209901019901", "209901019902"], "レース一覧からその日のレースID（重複・他日除外）")
    check(N.parse_class_group("Ｓ2") == (1, 2) and N.parse_class_group("ＳＳ") == (1, 0)
          and N.parse_class_group("Ａ3") == (2, 3) and N.parse_class_group("Ｌ1") == (4, 1), "級班 → class/group")


def test_racecard():
    rows = N.parse_racecard(ENTRY_HTML, NK)
    check(len(rows) == 3, "出走表 3名")
    r1, r2, r3 = rows
    check(list(r1.keys())[:6] == ["race_id", "schedule_id", "race_number", "car_number", "bracket_number", "absent"], "列順")
    check(r1["race_id"] == "079920990101" and r1["schedule_id"] == "2099010199" and r1["race_number"] == 7, "ID 列")
    check(r1["player_id"] == "099001" and r3["player_id"] == "009903", "選手IDを6桁ゼロ埋め")
    check(r1["player_name"] == "架空一郎" and r1["player_yomi"] == "カクウイチロウ", "選手名・よみ")
    check(r1["prefecture"] == "神奈川" and r2["prefecture"] == "和歌山" and r3["prefecture"] == "東京", "府県の省略を正式名に")
    check(r1["age"] == 30 and r1["term"] == 101 and r1["class"] == 1 and r1["group"] == 1, "年齢・期別・級班")
    check(r1["race_point"] == 95.5 and r1["style"] == "逃" and r2["style"] == "追", "競走得点・脚質（隠し値を除く）")
    check((r1["standing_count"], r1["home_count"], r1["back_count"]) == (2, 1, 3), "S/H/B")
    check((r1["escape_count"], r1["makuri_count"], r1["sashi_count"], r1["mark_count"]) == (4, 1, 0, 0), "決まり手回数")
    check((r1["first_count"], r1["second_count"], r1["third_count"], r1["out_count"]) == (5, 3, 2, 10), "着回数")
    check((r1["first_rate"], r1["second_rate"], r1["third_rate"]) == (25.0, 40.0, 50.0), "勝率・連対率（%の数値）")
    check(r1["gear_ratio"] == 3.92 and r1["comment"] == "自力。", "ギヤ倍数・コメント")
    check(r1["line_order"] == "1-2" and r1["line_position"] == 1 and r2["line_position"] == 2
          and r2["line_head"] == 1 and r1["line_size"] == 2, "並び予想 → ライン")
    check(r3["is_single"] == 1 and r3["line_size"] == 1 and r1["is_single"] == 0, "単騎")
    check(r1["line_type"] == "細切れ", "ライン構成（2人以上のラインが1つ → 細切れ）")
    check(N.line_type_of([[1, 2], [3, 4], [5, 6, 7]]) == "三分戦", "三分戦の判定")


def test_result_and_meta():
    res = N.parse_result(RESULT_HTML, NK)
    o = res["orders"]
    check([x["order"] for x in o] == [1, 2, 3] and [x["car_number"] for x in o] == [3, 1, 2], "着順・車番")
    check(o[0]["factor"] == "捲" and o[0]["back"] == 1 and o[0]["standing"] == 0, "決まり手 ク→捲・SB")
    check(o[1]["standing"] == 1 and o[1]["margin"] == "1車身" and o[0]["final_half"] == "11.2", "S・着差・上り")
    check(o[0]["player_id"] == "009903" and o[0]["player_name"] == "仮名三郎", "結果の選手ID・名前")
    p = {(x["bet_type"], x["combination"]): x for x in res["payoffs"]}
    check(p[("3連単", "3-1-2")]["payoff"] == 1230 and p[("3連単", "3-1-2")]["popularity"] == 4, "3連単の払戻（3>1>2 → 3-1-2）")
    check(("ワイド", "2-3") in p and p[("2車複", "1-3")]["payoff"] == 350, "ワイド2行目・2車複")
    m = N.parse_race_meta(RESULT_HTML, NK, entries_number=3)
    check(m["race_id"] == "079920990101" and m["date"] == DATE and m["venue_id"] == "99", "レース情報のID")
    check(m["cup_id"] == "2098123199" and m["day_index"] == 2, "開催ID（初日+場）・何日目")
    check(m["grade"] == 2 and m["class"] == "A級" and m["race_type"] == "チ予選", "グレード・級・種別")
    check(m["distance"] == 1625 and m["lap"] == 4 and m["start_at"] == "2099-01-01 21:35", "距離・周回・発走")
    check(m["cup_name"] == "架空記念杯" and m["source"] == "netkeirin" and m["weather"] == "", "開催名・取得元・天候なし")


def test_accident():
    html = RESULT_HTML.replace('<td class="RaceCardCell01 Result_Num">3着</td>', '<td class="RaceCardCell01 Result_Num">失</td>')
    o = N.parse_result(html, NK)["orders"]
    check(o[2]["order"] == 0 and o[2]["accident"] == "失格", "失格 → order 0 / accident 失格")


def _fake_odds_api():
    """オッズ API と同じ形の架空の応答（3車立て・全組み合わせ）。"""
    import itertools
    tri = [[f"{a:02d}{b:02d}{c:02d}", str(5.0 + i), "0", str(i + 1)]
           for i, (a, b, c) in enumerate(itertools.permutations([1, 2, 3], 3))]
    for t in tri:
        if t[0] == "030102":
            t[1], t[3] = "12.3", "4"
    return {"status": "OK", "reason": "", "data": {
        f"nkrace_odds::{NK}": {
            "official_dt": "2099-01-01 21:36:00",
            "list_9": tri,
            "list_7": [["0103", "1.4", "1.6", "1"], ["0203", "2.0", "2.5", "2"], ["0102", "0", "0", "0"]],
            "list_5": [["0103", "3.5", "0", "1"]],
        },
        f"nkrace_odds::{NK}_last_dt": "2099-01-01 21:40:00"}}


def test_odds_json_and_save():
    payoffs = N.parse_result(RESULT_HTML, NK)["payoffs"]
    rows = N.parse_odds_api(_fake_odds_api(), NK, payoffs)
    w = next(r for r in rows if r["bet_type"] == "ワイド" and r["combination"] == "1-3")
    t = next(r for r in rows if r["bet_type"] == "3連単" and r["combination"] == "3-1-2")
    check(sum(1 for r in rows if r["bet_type"] == "3連単") == 6, "3連単の全組み合わせ（3車→6通り）")
    check(w["odds"] == 1.4 and w["odds_max"] == 1.6 and w["popularity"] == 1, "ワイド odds=最低 / odds_max=最高・人気")
    check(t["odds"] == 12.3 and t["payoff"] == 1230 and t["popularity"] == 4, "的中組の払戻は payoff 列、odds は倍率")
    z = next(r for r in rows if r["bet_type"] == "ワイド" and r["combination"] == "1-2")
    check(z["odds"] == "" and z["absent"] == 1, "倍率0（欠車を含む組）は空欄・absent=1")
    n2 = next(r for r in rows if r["bet_type"] == "2車複")
    check(n2["odds_max"] == "" and n2["race_id"] == "079920990101" and n2["date"] == DATE, "ワイド以外は odds_max 空欄・ID")
    try:
        N.parse_odds_api({"status": "NG", "data": {}}, NK)
        check(False, "NG 応答は例外")
    except ValueError:
        check(True, "NG 応答は例外")
    with tempfile.TemporaryDirectory() as d:
        KC.DATA_DIR = Path(d)
        KC.append_odds_csv(rows)
        f = Path(d) / "2099" / "odds" / "odds_20990101.csv"
        with open(f, encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh))
        check(header == ["race_id", "date", "race_number", "bet_type", "combination", "odds", "odds_max",
                         "payoff", "popularity", "absent"], "オッズCSVの列が既存形式")


def test_blocked():
    def boom(req, timeout=30):
        raise HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
    orig = N.urlopen
    N.urlopen = boom
    try:
        try:
            N.fetch("https://keirin.netkeiba.com/x", interval=0)
            check(False, "429 で Blocked")
        except N.Blocked:
            check(True, "429 で Blocked（回避せず中断）")
    finally:
        N.urlopen = orig
    check(N.REQUEST_INTERVAL >= 3.0 and "keirin-research" in N.USER_AGENT, "アクセス間隔3秒以上・正直な User-Agent")


def test_collect_offline():
    pages = {"api_race_list": LIST_HTML.replace("209901019902", "209901019907").replace("209901019901", "209901019907"),
             "/race/entry/": ENTRY_HTML, "/race/result/": RESULT_HTML}

    import json

    def fake_fetch(url, params=None, interval=0, data=None):
        if "/api/race/" in url:
            assert data["class"] == "AplRaceOdds" and data["method"] == "get" and data["race_id"] == NK
            return json.dumps(_fake_odds_api())
        for k, v in pages.items():
            if k in url:
                return v
        raise AssertionError(url)
    orig = N.fetch
    N.fetch = fake_fetch
    try:
        with tempfile.TemporaryDirectory() as d:
            st = N.collect_date(DATE, Path(d) / "data")
            y = Path(d) / "data" / "2099"
            check(st["races"] == 1 and st["failed"] == 0, "収集の流れ（一覧→出走表→結果→保存）")
            with open(y / "racecards.csv", encoding="utf-8-sig") as fh:
                rc = list(csv.DictReader(fh))
            with open(y / "races.csv", encoding="utf-8-sig") as fh:
                rs = list(csv.DictReader(fh))
            check(len(rc) == 3 and rc[0]["race_id"] == "079920990101", "racecards.csv 保存")
            check(rs[0]["source"] == "netkeirin" and rs[0]["entries_number"] == "3", "races.csv 保存（source 列）")
            check((y / "results.csv").exists() and (y / "payoffs.csv").exists(), "results/payoffs 保存")
            check(st["odds_races"] == 1 and st["odds_full"] == 1 and (y / "odds" / "odds_20990101.csv").exists(),
                  "オッズ取得・保存（3連単 全組そろい）")
    finally:
        N.fetch = orig


if __name__ == "__main__":
    for t in [test_ids_and_list, test_racecard, test_result_and_meta, test_accident,
              test_odds_json_and_save, test_blocked, test_collect_offline]:
        print(f"--- {t.__name__}")
        t()
    print(f"\n{'全テスト成功' if not failures else f'失敗 {len(failures)} 件'}")
    sys.exit(1 if failures else 0)
