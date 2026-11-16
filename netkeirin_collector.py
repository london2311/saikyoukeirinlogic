#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netkeirin_collector.py — 競輪データ収集スクリプト（netkeirin 版）

==============================================================================
【利用上の注意 — 必ずお読みください】

* netkeirin 利用規約 第14条により、取得したデータは「私的利用の範囲内」でのみ
  利用できます。データを公開（不特定または多数の者が知りうる状態におく）・
  再配布・販売・出版すること、営利目的で利用することはできません。
  → 収集データは非公開リポジトリ（例: london2311/datebase）にだけ保存し、
    公開リポジトリには絶対にコミットしないでください。
* アクセス間隔は 1 リクエストごとに 3 秒以上（REQUEST_INTERVAL）。短縮しないこと。
* 403 / 429 など「拒否」を示す応答が返ったら、回避せずにその場で中断します。
* ログにはレースの中身（選手名・オッズ・着順など）を出さず、URL と件数だけを出します。
==============================================================================

仕組み:
  1. レース一覧 API（/race/api_race_list.html）で対象日の開催場と各場のレースIDを得る。
     netkeirin のレースID = YYYYMMDD + 場コード2桁 + レース番号2桁（例 202611142801）。
  2. 出走表ページ（/race/entry/?race_id=…）: 枠・車番・選手・府県・年齢・期別・級班・
     競走得点・脚質・S/H/B・決まり手回数・着回数・勝率/2連対率/3連対率・ギヤ倍数・
     コメント・並び予想（ライン）。
  3. 結果ページ（/race/result/?race_id=…）: 着順・着差・上り・決まり手・SB・払戻・
     レース情報（開催名・グレード・級・種別・距離・周回・発走時刻・開催何日目）。
  4. 最終オッズ: オッズページ（/race/odds/）の JavaScript が読み込むのと同じ API
     POST https://keirin.netkeiba.com/api/race/  class=AplRaceOdds&method=get&race_id=…
     （compress=0 で非圧縮の JSON）。全券種の全組み合わせ・人気順が入っている。

出力は keirin_collector.py（WINTICKET 版）と同じ CSV 形式・同じ列名。
race_id は既存形式（レース番号2桁 + 場コード2桁 + 開催日8桁）に変換して保存するため、
同じレースは WINTICKET 版と同じ race_id になる（分析側の重複除去がそのまま効く）。
races.csv にだけ取得元を示す列 source=netkeirin を加える。

使い方:
  python netkeirin_collector.py                       # 前日（JST）
  python netkeirin_collector.py --date 20261114
  python netkeirin_collector.py --from 20261114 --to 20261120
  python netkeirin_collector.py --date 20261114 --out-dir _data_repo/data
"""

import argparse
import html as html_lib
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import keirin_collector as KC  # append_csv / append_odds_csv / 会場名を再利用

BASE_URL = "https://keirin.netkeiba.com"
USER_AGENT = "Mozilla/5.0 (compatible; keirin-research/1.0; personal analysis)"
REQUEST_INTERVAL = 3.0  # 秒。1リクエストごとにこれ以上空ける（短縮しないこと）
JST = timezone(timedelta(hours=9))
SOURCE = "netkeirin"

# 全組み合わせのオッズは出走表/結果ページの HTML には無く、オッズページ上の JavaScript
# （netkeirin の race_odds.min.js / race_api_indexdb.min.js）が次の API から読み込む。
# ページと同じ呼び出し方（POST, class=AplRaceOdds, method=get）で取得する。
ODDS_API = f"{BASE_URL}/api/race/"
# 応答の list_N → 券種（N は netkeirin の式別コード。払戻と突き合わせて確認済み）
PLACEHOLDER_ODDS = 9999.9  # 投票が集まる前は全組がこの倍率で表示される
ODDS_LISTS = {"list_3": "枠複", "list_4": "枠単", "list_5": "2車複", "list_6": "2車単",
              "list_7": "ワイド", "list_8": "3連複", "list_9": "3連単"}

# 府県の省略表記（netkeirin は3文字の府県を2文字で表示する）→ 既存データと同じ正式名
PREF_FULL = {"神奈": "神奈川", "和歌": "和歌山", "鹿児": "鹿児島"}

# グレード表記 → 既存データ（WINTICKET）の数値
GRADE_CODES = {"FII": 1, "FI": 2, "GIII": 3, "GII": 4, "GI": 5, "GP": 6}

# 級 → 既存データの class 値（1=S級, 2=A級, 4=L級(ガールズ)）
CLASS_CODES = {"S": 1, "A": 2, "L": 4}

# 決まり手（結果表の「決」列）→ 既存データの表記
FACTOR_MAP = {"逃": "逃", "ク": "捲", "捲": "捲", "差": "差", "マ": "マ"}

# 着順欄が数字でない時（失格など）→ 既存データの accident 表記
ACCIDENT_MAP = {"失": "失格", "落": "落車", "欠": "欠車", "棄": "中止", "中": "中止",
                "故": "故障", "再": "再乗", "入": "入線"}

# 払戻表の賭式名（NFKC 正規化後）→ 既存データの bet_type
BET_NAME_MAP = {"3連単": "3連単", "3連複": "3連複", "2車単": "2車単", "2車複": "2車複",
                "ワイド": "ワイド", "枠単": "枠単", "枠複": "枠複"}


# 種別の表記 → 既存データ（WINTICKET）の略称。該当しなければ netkeirin の表記のまま
RACE_TYPE_MAP = {
    "チャレンジ予選": "チ予選", "チャレンジ一般": "チ一般", "チャレンジ選抜": "チ選抜",
    "チャレンジ準決勝": "チ準決", "チャレンジ決勝": "チ決勝",
    "ガールズ予選1": "ガ予１", "ガールズ予選2": "ガ予２", "ガールズ一般": "ガ一般",
    "ガールズ決勝": "ガ決勝", "ガールズ選抜": "ガ選抜",
    "初日特別選抜": "初特選", "一次予選": "一予選", "二次予選": "二予選",
    "二次予選A": "二予Ａ", "二次予選B": "二予Ｂ", "特別優秀": "特秀", "特選予選": "特選予",
}


def normalize_race_type(t: str) -> str:
    t = re.sub(r"\s+", "", unicodedata.normalize("NFKC", t or ""))
    return RACE_TYPE_MAP.get(t, t)


class Blocked(Exception):
    """サーバーから拒否（403/429 等）された。回避せず中断する。"""


# ---------------------------------------------------------------- 取得

_last_request = [0.0]


def fetch(url: str, params: dict = None, interval: float = REQUEST_INTERVAL, data: dict = None) -> str:
    """GET（data を渡すと POST）して文字列を返す。直前のリクエストから interval 秒以上空ける。"""
    if params:
        url = url + ("&" if "?" in url else "?") + urlencode(params)
    wait = _last_request[0] + interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    body_bytes = urlencode(data).encode() if data else None
    if data:
        print(f"  POST {url} race_id={data.get('race_id', '')}")
    else:
        print(f"  GET {url}")
    req = Request(url, data=body_bytes, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja"})
    try:
        with urlopen(req, timeout=30) as res:
            body = res.read()
            charset = res.headers.get_content_charset() or "utf-8"
    except HTTPError as e:
        if e.code in (401, 403, 429, 503):
            raise Blocked(f"HTTP {e.code}: {url}") from e
        raise
    finally:
        _last_request[0] = time.monotonic()
    text = body.decode(charset, errors="replace")
    if "海外からは" in text and "利用できません" in text:
        raise Blocked(f"海外からの利用制限の表示: {url}")
    return text


# ---------------------------------------------------------------- 文字列の補助


def _text(fragment: str, nfkc: bool = True) -> str:
    """HTML断片からタグを除いた文字列（NFKC 正規化・空白詰め）。"""
    s = re.sub(r"<!--.*?-->", "", fragment, flags=re.S)
    s = re.sub(r"<(script|style)\b.*?</\1>", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_lib.unescape(s)
    if nfkc:
        s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()


def _visible(fragment: str) -> str:
    """display:none の要素（並べ替え用の隠し値）を除いた文字列。"""
    s = re.sub(r'<span[^>]*display:\s*none[^>]*>.*?</span>', "", fragment, flags=re.S)
    return _text(s)


def _num(s):
    """'15.0%' / '97.65' / '1,260円' → 数値。取れなければ ''。"""
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    if not m:
        return ""
    v = float(m.group(0))
    return int(v) if v.is_integer() and "." not in m.group(0) else v


def _cells(row_html: str) -> list:
    return re.findall(r"<td\b[^>]*>(.*?)</td>", row_html, flags=re.S)


def _rows(table_html: str) -> list:
    return re.findall(r"<tr\b[^>]*>.*?</tr>", table_html, flags=re.S)


def _table(page: str, marker: str) -> str:
    """marker（id や summary）を含む <table> の中身を返す。"""
    i = page.find(marker)
    if i < 0:
        return ""
    i = page.rfind("<table", 0, i + 1)
    j = page.find("</table>", i)
    return page[i:j] if j > 0 else ""


def to_wt_race_id(nk_race_id: str) -> str:
    """netkeirin の YYYYMMDDVVRR → 既存形式 RRVVYYYYMMDD。"""
    nk = str(nk_race_id)
    if not re.fullmatch(r"\d{12}", nk):
        raise ValueError(f"レースIDの形式が想定外: {nk}")
    return nk[10:12] + nk[8:10] + nk[:8]


def split_race_id(nk_race_id: str):
    nk = str(nk_race_id)
    return nk[:8], nk[8:10], int(nk[10:12])  # 日付, 場コード, レース番号


def parse_class_group(s: str):
    """'S2' / 'SS' / 'A3' / 'L1' → (class, group)。既存データと同じ数値。"""
    s = unicodedata.normalize("NFKC", s or "").strip().upper()
    if s == "SS":
        return CLASS_CODES["S"], 0
    m = re.fullmatch(r"([SAL])(\d)", s)
    if not m:
        return "", ""
    return CLASS_CODES[m.group(1)], int(m.group(2))


def pref_full(p: str) -> str:
    p = (p or "").strip()
    return PREF_FULL.get(p, p)


# ---------------------------------------------------------------- レース一覧


def parse_venue_codes(list_html: str) -> list:
    """レース一覧 API の応答から、その日の開催場コード一覧（表示順）を返す。"""
    seen = []
    for code in re.findall(r'id="btn_jyoname_(\d{2})"', list_html):
        if code not in seen:
            seen.append(code)
    return seen


def parse_race_ids(list_html: str, date: str, venue: str = None) -> list:
    """レース一覧 API の応答からレースID（netkeirin 形式）を返す。"""
    ids = set(re.findall(r"race_id=(\d{12})", list_html))
    ids = {r for r in ids if r[:8] == date and (venue is None or r[8:10] == venue)}
    return sorted(ids)


def list_races(date: str) -> list:
    """対象日の全レースID（netkeirin 形式）を返す。"""
    params = {"pid": "api_race_list", "input": "UTF-8", "output": "html",
              "kaisai_date": date, "jyocd": "", "link_mode": ""}
    first = fetch(f"{BASE_URL}/race/api_race_list.html", params)
    venues = parse_venue_codes(first)
    if not venues:
        return []
    ids = set(parse_race_ids(first, date))
    have = {r[8:10] for r in ids}
    for v in venues:
        if v in have:
            continue
        params["jyocd"] = v
        ids |= set(parse_race_ids(fetch(f"{BASE_URL}/race/api_race_list.html", params), date, v))
    return sorted(ids)


def parse_race_list(list_html: str, date: str) -> list:
    """レース一覧 API の応答 → [(race_id, 発走 'HH:MM'), ...]"""
    out = {}
    for m in re.finditer(r'<li id\s*=\s*"racing_color_(\d{12})"(.*?)(?=<li id\s*=\s*"racing_color_|\Z)', list_html, flags=re.S):
        rid, body = m.group(1), m.group(2)
        if rid[:8] != date:
            continue
        st = re.search(r'発走\s*(\d{1,2}:\d{2})', body)
        out[rid] = st.group(1) if st else ''
    return sorted(out.items())


def list_today(date: str, venue: str = None) -> list:
    params = {'pid': 'api_race_list', 'input': 'UTF-8', 'output': 'html',
              'kaisai_date': date, 'jyocd': venue or '', 'link_mode': ''}
    first = fetch(f'{BASE_URL}/race/api_race_list.html', params)
    races = dict(parse_race_list(first, date))
    if not venue:
        have = {r[8:10] for r in races}
        for v in parse_venue_codes(first):
            if v in have:
                continue
            params['jyocd'] = v
            races.update(parse_race_list(fetch(f'{BASE_URL}/race/api_race_list.html', params), date))
    elif venue:
        races = {k: v for k, v in races.items() if k[8:10] == venue}
    return sorted(races.items())


def start_dt(date: str, hhmm: str):
    if not hhmm:
        return None
    h, m = map(int, hhmm.split(':'))
    return datetime.strptime(date, '%Y%m%d').replace(tzinfo=JST) + timedelta(hours=h, minutes=m)



# ---------------------------------------------------------------- 出走表


def parse_lines(entry_html: str) -> list:
    """並び予想（ライン）→ [[車番, ...], ...]。区切り（WakuSeparat）でラインを分ける。"""
    i = entry_html.find("block=narabi_yoso")
    if i < 0:
        i = entry_html.find("DeployYoso")
    if i < 0:
        return []
    j = entry_html.find("</section>", i)
    block = entry_html[i:j if j > 0 else None]
    lines, cur = [], []
    for sep, num in re.findall(r'class="(WakuSeparat)"|class="Waku\d+ Shaban_Num"[^>]*>\s*(\d+)\s*<', block):
        if sep:
            if cur:
                lines.append(cur)
            cur = []
        elif num:
            n = int(num)
            if n not in cur:
                cur.append(n)
    if cur:
        lines.append(cur)
    return lines


def line_type_of(lines: list) -> str:
    """2人以上のラインの数から ○分戦 を決める（WINTICKET の表記に合わせた近似）。"""
    k = sum(1 for ln in lines if len(ln) >= 2)
    return {2: "二分戦", 3: "三分戦", 4: "四分戦"}.get(k, "細切れ" if lines else "")


# 出走表（RaceCard_Table_Static）の列順。見出しの文字で列位置を決める。
ENTRY_HEADERS = {
    "競走得点": "race_point", "脚質": "style", "S": "standing_count", "H": "home_count",
    "B": "back_count", "逃げ": "escape_count", "まくり": "makuri_count", "差し": "sashi_count",
    "マーク": "mark_count", "1着": "first_count", "2着": "second_count", "3着": "third_count",
    "着外": "out_count", "勝率": "first_rate", "2連対率": "second_rate", "3連対率": "third_rate",
    "ギヤ倍数": "gear_ratio", "選手コメント": "comment",
}


def _entry_columns(table: str) -> dict:
    """見出し行から {列番号: 出力列名} を作る。"""
    head = re.search(r"<tr[^>]*Header[^>]*>(.*?)</tr>", table, flags=re.S)
    if not head:
        return {}
    cols = {}
    for idx, th in enumerate(re.findall(r"<th\b[^>]*>(.*?)</th>", head.group(1), flags=re.S)):
        t = _text(th).replace(" ", "").replace("|", "ー").replace("｜", "ー")
        t = t.replace("マーク", "マーク").replace("マク", "マーク")
        for label, name in ENTRY_HEADERS.items():
            if t == label or (label in ("選手コメント", "競走得点", "ギヤ倍数", "2連対率", "3連対率") and label in t):
                cols[idx] = name
                break
    return cols


def parse_racecard(entry_html: str, nk_race_id: str) -> list:
    """出走表ページ → racecards.csv の行（1行 = 1選手）。"""
    date, venue, rn = split_race_id(nk_race_id)
    race_id = to_wt_race_id(nk_race_id)
    table = _table(entry_html, 'id="RaceCard_Table_Static"')
    if not table:
        raise ValueError("出走表テーブルが見つかりません")
    cols = _entry_columns(table)

    lines = parse_lines(entry_html)
    ltype = line_type_of(lines)
    line_info = {}
    for li, ln in enumerate(lines, start=1):
        order = "-".join(map(str, ln))
        for pos, n in enumerate(ln, start=1):
            line_info[n] = dict(line=li, line_position=pos, line_order=order, line_size=len(ln),
                                line_head=ln[0], is_single=int(len(ln) == 1))

    rows = []
    for tr in _rows(table):
        if "PlayerList" not in tr[:80]:
            continue
        tds = _cells(tr)
        if len(tds) < 6:
            continue
        bracket, car = _num(_text(tds[0])), _num(_text(tds[1]))
        info = next((td for td in tds if "Player_Info" in td or "PlayerName" in td), "")
        info_i = tds.index(info) if info in tds else 4
        m_id = re.search(r'id="names?_(\d+)_', tr)
        name = _text(re.search(r'class="PlayerName"[^>]*>(.*?)<(?:img|span|/dt)', info, flags=re.S).group(1)) \
            if re.search(r'class="PlayerName"', info) else ""
        yomi = re.search(r'<span style="display:\s*none;?">([^<]*)</span>', info)
        frm = re.search(r'class="PlayerFrom"[^>]*>(.*?)</dd>', info, flags=re.S)
        frm_t = _text(frm.group(1)) if frm else ""
        pref = pref_full(re.sub(r"\s*\d+歳.*$", "", frm_t))
        age = re.search(r"(\d+)歳", frm_t)
        pcls = re.search(r'class="PlayerClass"[^>]*>(.*?)</dd>', info, flags=re.S)
        pcls_t = _text(pcls.group(1)) if pcls else ""
        term = re.search(r"(\d+)期", pcls_t)
        klass, group = parse_class_group(re.sub(r"^\s*\d+期\s*", "", pcls_t))

        row = {
            "race_id": race_id,
            "schedule_id": date + venue,
            "race_number": rn,
            "car_number": car,
            "bracket_number": bracket,
            "absent": int(bool(re.search(r"欠車|Cancel|Kesshou", tr))),
            "player_id": m_id.group(1).zfill(6) if m_id else "",
            "player_name": name,
            "player_yomi": unicodedata.normalize("NFKC", yomi.group(1)).strip() if yomi else "",
            "age": int(age.group(1)) if age else "",
            "prefecture": pref,
            "term": int(term.group(1)) if term else "",
            "class": klass,
            "group": group,
        }
        for idx, colname in cols.items():
            if idx <= info_i or idx >= len(tds):
                continue
            v = _visible(tds[idx])
            row[colname] = v if colname in ("style", "comment") else _num(v)
        for k in ("gear_ratio", "style", "race_point", "first_rate", "second_rate", "third_rate",
                  "standing_count", "home_count", "back_count", "escape_count", "makuri_count",
                  "sashi_count", "mark_count", "first_count", "second_count", "third_count", "out_count"):
            row.setdefault(k, "")
        li = line_info.get(car, {})
        row.update({k: li.get(k, "") for k in ("line", "line_position", "line_order", "line_size",
                                                 "line_head", "is_single")})
        row["line_type"] = ltype
        row.setdefault("comment", "")
        rows.append(row)

    # 列順を既存 CSV に合わせる
    order = ["race_id", "schedule_id", "race_number", "car_number", "bracket_number", "absent",
             "player_id", "player_name", "player_yomi", "age", "prefecture", "term", "class", "group",
             "gear_ratio", "style", "race_point", "first_rate", "second_rate", "third_rate",
             "standing_count", "home_count", "back_count", "escape_count", "makuri_count",
             "sashi_count", "mark_count", "first_count", "second_count", "third_count", "out_count",
             "line", "line_position", "line_order", "line_size", "line_head", "is_single",
             "line_type", "comment"]
    return [{k: r.get(k, "") for k in order} for r in rows]


# ---------------------------------------------------------------- 結果・払戻・レース情報


def parse_result(result_html: str, nk_race_id: str) -> dict:
    """結果ページ → {"orders": results.csv の行, "payoffs": payoffs.csv の行}。"""
    date, venue, rn = split_race_id(nk_race_id)
    race_id = to_wt_race_id(nk_race_id)
    orders = []
    table = _table(result_html, 'id="All_Result_Table"')
    for tr in _rows(table):
        if "PlayerList" not in tr[:80]:
            continue
        tds = _cells(tr)
        if len(tds) < 8:
            continue
        rank_t = _text(tds[0])
        m = re.match(r"(\d+)", rank_t)
        order = int(m.group(1)) if m else 0
        accident = ""
        if not m and rank_t:
            accident = next((v for k, v in ACCIDENT_MAP.items() if rank_t.startswith(k)), rank_t)
        pid = re.search(r"profile/\?id=(\d+)", tr)
        name = re.search(r'class="PlayerName"[^>]*>(.*?)<', tds[3], flags=re.S)
        sb = _text(tds[7]).upper()
        orders.append({
            "race_id": race_id,
            "schedule_id": date + venue,
            "race_number": rn,
            "order": order,
            "car_number": _num(_text(tds[2])),
            "player_id": pid.group(1).zfill(6) if pid else "",
            "player_name": _text(name.group(1)) if name else "",
            "factor": FACTOR_MAP.get(_text(tds[6]), _text(tds[6])),
            "final_half": _text(tds[5]),
            "margin": _text(tds[4]),
            "accident": accident,
            "standing": int("S" in sb),
            "back": int("B" in sb),
        })

    payoffs = []
    ptable = _table(result_html, 'class="Payout_Detail_Table"')
    bet = ""
    for tr in _rows(ptable):
        th = re.search(r"<th\b[^>]*>(.*?)</th>", tr, flags=re.S)
        if th:
            bet = BET_NAME_MAP.get(_text(th.group(1)).replace(" ", ""), _text(th.group(1)))
        res = re.search(r'class="Result"[^>]*>(.*?)</td>', tr, flags=re.S)
        pay = re.search(r'class="Payout"[^>]*>(.*?)</td>', tr, flags=re.S)
        if not (bet and res and pay):
            continue
        combo = re.sub(r"\s*[>\-→]\s*", "-", _text(res.group(1)))
        if not re.fullmatch(r"\d+(-\d+)*", combo):
            continue  # 「特払い」「返還」など
        pop = re.search(r'class="Ninki"[^>]*>(.*?)</td>', tr, flags=re.S)
        payoffs.append({
            "race_id": race_id,
            "race_number": rn,
            "bet_type": bet,
            "combination": combo,
            "payoff": _num(_text(pay.group(1))),
            "popularity": _num(_text(pop.group(1))) if pop else "",
        })
    return {"orders": orders, "payoffs": payoffs}


def parse_race_meta(page_html: str, nk_race_id: str, entries_number=None) -> dict:
    """結果（または出走表）ページ → races.csv の行。"""
    date, venue, rn = split_race_id(nk_race_id)
    title = page_html[page_html.find("block=race_title"):]
    grade_m = re.search(r'Icon_GradeType\d*">\s*([^<]+?)\s*<', title)
    grade_t = unicodedata.normalize("NFKC", grade_m.group(1)).strip().upper() if grade_m else ""
    group_m = re.search(r'class="Race_GroupName">(.*?)</span>', title, flags=re.S)
    name_m = re.search(r'class="Race_Name">(.*?)<span', title, flags=re.S) or \
        re.search(r'class="Race_Name">(.*?)</div>', title, flags=re.S)
    race_name = _text(name_m.group(1)) if name_m else ""
    cls_m = re.match(r"([SAL])級\s*(.*)$", race_name)
    data_m = re.search(r'class="Race_Data">(.*?)</div>', title, flags=re.S)
    data_t = _text(data_m.group(1)) if data_m else ""
    start = re.search(r"発走\s*(\d{1,2}):(\d{2})", data_t)
    dist = re.search(r"(\d+)\s*m", data_t)
    lap = re.search(r"(\d+)\s*周", data_t)

    # 開催日程（初日〜最終日）から開催初日と何日目かを求める
    days = re.findall(r"kaisai_date=(\d{8})&(?:amp;)?jyocd=" + venue + r'"[^>]*>\s*<span[^>]*>[^<]*<br>\s*([^<]+)<', title)
    day_dates = sorted({d for d, _ in days})
    start_date = day_dates[0] if day_dates else ""
    day_index = day_dates.index(date) + 1 if date in day_dates else ""
    if not day_index:
        m = re.search(r'class="DayCount">\s*(\d+)日目', title)
        day_index = int(m.group(1)) if m else (1 if re.search(r'class="DayCount">\s*初日', title) else "")

    hh, mm = (int(start.group(1)), start.group(2)) if start else (None, None)
    start_at = ""
    if start:
        d = datetime.strptime(date, "%Y%m%d") + timedelta(days=1 if hh >= 24 else 0)
        start_at = f"{d:%Y-%m-%d} {hh % 24:02d}:{mm}"
    return {
        "race_id": to_wt_race_id(nk_race_id),
        "date": date,
        "venue_id": venue,
        "venue_name": KC.VENUE_NAMES.get(venue, ""),
        "cup_id": (start_date + venue) if start_date else "",
        "cup_name": _text(group_m.group(1), nfkc=False) if group_m else "",
        "grade": GRADE_CODES.get(grade_t, ""),
        "day_index": day_index,
        "race_number": rn,
        "class": (cls_m.group(1) + "級") if cls_m else "",
        "race_type": normalize_race_type(cls_m.group(2) if cls_m else race_name),
        "distance": int(dist.group(1)) if dist else "",
        "lap": int(lap.group(1)) if lap else "",
        "entries_number": entries_number if entries_number is not None else "",
        "start_at": start_at,
        "weather": "",      # netkeirin のページには無い
        "wind_speed": "",   # netkeirin のページには無い
        "cancel": 0,
        "source": SOURCE,
    }


# ---------------------------------------------------------------- オッズ


def _combo(code: str) -> str:
    """'030604' → '3-6-4'（2桁ずつの車番/枠番）。"""
    code = str(code)
    if not re.fullmatch(r"(\d\d){2,3}", code):
        raise ValueError(f"組み合わせの形式が想定外: {code}")
    return "-".join(str(int(code[k:k + 2])) for k in range(0, len(code), 2))


def parse_odds_api(resp: dict, nk_race_id: str, payoffs: list = None) -> list:
    """オッズ API の応答（compress=0 の JSON）→ odds CSV の行（既存形式）。

    応答: {"status": "OK", "data": {"nkrace_odds::<race_id>": {"official_dt": …,
           "list_9": [["010203", "434.9", "0", "110"], …], …}}}
    各要素 = [組み合わせ, 倍率（ワイドは最低）, 最高倍率（ワイドのみ、他は0）, 人気順]。
    odds は常に倍率、ワイドは odds=最低 / odds_max=最高、的中組の払戻は payoff 列。
    倍率が 0 以下（欠車を含む組など）は odds を空欄・absent=1 にする。
    9999.9倍（発売前・投票なしの仮表示）は odds を空欄にする（absent=0）。
    """
    if not isinstance(resp, dict) or resp.get("status") == "NG":
        raise ValueError("オッズ API の応答が NG")
    body = (resp.get("data") or {}).get(f"nkrace_odds::{nk_race_id}")
    if not isinstance(body, dict):
        return []
    date, _venue, rn = split_race_id(nk_race_id)
    race_id = to_wt_race_id(nk_race_id)
    win = {(p["bet_type"], p["combination"]): p for p in (payoffs or [])}
    rows = []
    for key, bet in ODDS_LISTS.items():
        for item in body.get(key) or []:
            combo = _combo(item[0])
            lo = _num(item[1]) if len(item) > 1 else ""
            hi = _num(item[2]) if (bet == "ワイド" and len(item) > 2) else ""
            pop = _num(item[3]) if len(item) > 3 else ""
            absent = 0
            if lo == "" or lo <= 0:
                lo, hi, absent = "", "", 1
            elif lo >= PLACEHOLDER_ODDS:
                lo, hi = "", ""  # 発売前・投票なしの仮表示（9999.9倍）は実際のオッズではない
            if hi != "" and hi <= 0:
                hi = ""
            p = win.get((bet, combo))
            rows.append({
                "race_id": race_id, "date": date, "race_number": rn, "bet_type": bet,
                "combination": combo, "odds": lo, "odds_max": hi,
                "payoff": p["payoff"] if p else "",
                "popularity": pop if pop not in ("", 0) else "",
                "absent": absent,
            })
    return rows


def fetch_odds(nk_race_id: str, payoffs: list) -> list:
    """最終オッズ（全券種・全組み合わせ）を取得する。"""
    import json
    text = fetch(ODDS_API, data={"input": "UTF-8", "output": "json", "class": "AplRaceOdds",
                                 "method": "get", "compress": "0", "race_id": nk_race_id})
    return parse_odds_api(json.loads(text), nk_race_id, payoffs)


# ---------------------------------------------------------------- メイン


def collect_date(date: str, out_dir: Path, skip: set = None) -> dict:
    """skip: 取得済みのレースID（WINTICKET 形式）。過去分の補充で、既にあるレースを取り直さないために使う。"""
    print(f"=== {date} の収集（netkeirin） ===")
    KC.DATA_DIR = out_dir  # append_odds_csv の保存先
    race_ids = list_races(date)
    print(f"レース数（一覧）: {len(race_ids)} / 開催場数: {len({r[8:10] for r in race_ids})}")
    if skip:
        before = len(race_ids)
        race_ids = [r for r in race_ids if to_wt_race_id(r) not in skip]
        print(f"取得済みのため省略: {before - len(race_ids)}")
    stats = dict(races=0, failed=0, no_result=0, odds_races=0, odds_failed=0, listed=len(race_ids))
    metas, cards, orders, payoffs, odds = [], [], [], [], []
    for nk in race_ids:
        try:
            entry = fetch(f"{BASE_URL}/race/entry/", {"race_id": nk})
            result = fetch(f"{BASE_URL}/race/result/", {"race_id": nk})
            c = parse_racecard(entry, nk)
            r = parse_result(result, nk)
            if not r["orders"]:
                stats["no_result"] += 1
                print(f"  {nk}: 結果なし（中止/未確定）スキップ")
                continue
            absent_cars = {o["car_number"] for o in r["orders"] if o["accident"] == "欠車"}
            for row in c:
                if row["car_number"] in absent_cars:
                    row["absent"] = 1
            m = parse_race_meta(result, nk, entries_number=len(c))
        except Blocked:
            raise
        except (HTTPError, URLError, ValueError, AttributeError, IndexError) as e:
            stats["failed"] += 1
            print(f"  ! {nk}: 失敗 {type(e).__name__}")
            continue
        try:
            o = fetch_odds(nk, r["payoffs"])
        except Blocked:
            raise
        except (HTTPError, URLError, ValueError, KeyError, IndexError, TypeError) as e:
            o = []
            stats["odds_failed"] += 1
            print(f"  ! {nk}: オッズ取得失敗 {type(e).__name__}")
        metas.append(m)
        cards.extend(c)
        orders.extend(r["orders"])
        payoffs.extend(r["payoffs"])
        odds.extend(o)
        stats["races"] += 1
        n3 = sum(1 for x in o if x["bet_type"] == "3連単" and x["odds"] != "")
        n_act = len(c) - sum(int(x["absent"]) for x in c)
        full = n_act * (n_act - 1) * (n_act - 2)
        stats["odds_races"] += int(n3 > 0)
        stats["odds_full"] = stats.get("odds_full", 0) + int(full > 0 and n3 >= full)
        print(f"  {nk}: 出走{len(c)} / 着順{len(r['orders'])} / 払戻{len(r['payoffs'])} / 3連単オッズ{n3}")

    year_dir = out_dir / date[:4]
    print("=== 保存 ===")
    print(f"races.csv      +{KC.append_csv(year_dir / 'races.csv', metas, ['race_id'])}")
    print(f"racecards.csv  +{KC.append_csv(year_dir / 'racecards.csv', cards, ['race_id', 'car_number'])}")
    print(f"results.csv    +{KC.append_csv(year_dir / 'results.csv', orders, ['race_id', 'car_number'])}")
    print(f"payoffs.csv    +{KC.append_csv(year_dir / 'payoffs.csv', payoffs, ['race_id', 'bet_type', 'combination'])}")
    print(f"odds           +{KC.append_odds_csv(odds)}")
    print(f"成功 {stats['races']} / 失敗 {stats['failed']} / 結果なし {stats['no_result']} / "
          f"3連単オッズあり {stats['odds_races']}（全組そろい {stats.get('odds_full', 0)}） / "
          f"オッズ取得失敗 {stats['odds_failed']}")
    return stats


def _dates(a: str, b: str) -> list:
    d0, d1 = datetime.strptime(a, "%Y%m%d"), datetime.strptime(b, "%Y%m%d")
    if d1 < d0:
        d0, d1 = d1, d0
    return [(d0 + timedelta(days=i)).strftime("%Y%m%d") for i in range((d1 - d0).days + 1)]


def main(argv=None):
    ap = argparse.ArgumentParser(description="netkeirin から競輪データを収集する（私的利用のみ）")
    ap.add_argument("--date", help="収集する日 YYYYMMDD（既定: 前日 JST）")
    ap.add_argument("--from", dest="date_from", help="期間の開始日 YYYYMMDD")
    ap.add_argument("--to", dest="date_to", help="期間の終了日 YYYYMMDD（既定: 前日 JST）")
    ap.add_argument("--out-dir", default=str(Path(__file__).parent / "data"),
                    help="保存先（data/YYYY/... を作る親フォルダ）。公開リポジトリ内は不可")
    a = ap.parse_args(argv)

    yesterday = (datetime.now(JST) - timedelta(days=1)).strftime("%Y%m%d")
    if a.date_from:
        dates = _dates(a.date_from, a.date_to or yesterday)
    else:
        dates = [(a.date or "").strip() or yesterday]
    for d in dates:
        if not re.fullmatch(r"\d{8}", d):
            sys.exit(f"日付は YYYYMMDD で指定してください: {d}")
    out_dir = Path(a.out_dir)
    total_fail = 0
    try:
        for d in dates:
            total_fail += collect_date(d, out_dir)["failed"]
    except Blocked as e:
        print(f"!! サーバーから拒否されたため中断しました（回避はしません）: {e}")
        sys.exit(2)
    if total_fail:
        print(f"! 失敗したレースが {total_fail} 件あります")


if __name__ == "__main__":
    main()
