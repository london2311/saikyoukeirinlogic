#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keirin_collector.py — 競輪データ自動収集スクリプト(WINTICKET対応版)

==============================================================================
【利用上の注意 — 必ずお読みください】

* 本スクリプトは、ご自身の競輪予想・分析のための「私的使用」を目的とした
  ツールです。収集したデータの再配布・販売・公開は行わないでください。
* 取得先サイトの利用規約・サイトポリシーをご自身で確認のうえ、
  自己責任でご利用ください。規約は予告なく変更されることがあります。
* サーバーに負荷をかけないため、リクエスト間隔(REQUEST_INTERVAL)は
  2秒以上を維持してください。短縮しての利用は推奨しません。
* サイト構造の変更等により予告なく動作しなくなる可能性があります。
* 本スクリプトの利用により生じたいかなる損害についても、
  作者は責任を負いません。
==============================================================================

仕組み:
  WINTICKET(winticket.jp)はReact製のサイトで、ページHTML内の
  window.__PRELOADED_STATE__ にレースデータがJSONで丸ごと埋め込まれている。
  HTMLタグを解析する代わりに、このJSONを抽出して読む。

  結果ページ1枚に以下が全部含まれる:
    - 出走表(枠番・選手・競走得点・脚質・直近成績・ライン予想)
    - 結果(着順・決まり手・上がりタイム・着差)
    - 払戻(3連単/3連複/2車単/2車複/ワイド)
    - オッズ全組み合わせ(3連単/3連複/2車単/2車複/ワイド/枠単/枠複)
    - 開催情報(全日程・全レース一覧)

毎朝6時に実行 → 前日に開催された全レースを収集 → data/ 配下のCSVに追記。
"""

import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

# ---------------------------------------------------------------- 基本設定

BASE_URL = "https://www.winticket.jp"
DATA_DIR = Path(__file__).parent / "data"

# CSVはExcelで開いた時に文字化けしにくいよう、BOM付きUTF-8で作成する。
# 既存ファイルへの追記時はBOMを途中に入れないため通常のUTF-8で追記する。
CSV_READ_ENCODING = "utf-8-sig"
CSV_WRITE_ENCODING = "utf-8-sig"
CSV_APPEND_ENCODING = "utf-8"

JST = timezone(timedelta(hours=9))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) keirin-data-collector"
REQUEST_INTERVAL = 2.0  # リクエスト間隔(秒)。サーバーに負荷をかけないこと。

# 会場ID → URLスラッグ(全43場)
VENUE_SLUGS = {
    "11": "hakodate", "12": "aomori", "13": "iwakidaira",
    "21": "yahiko", "22": "maebashi", "23": "toride", "24": "utsunomiya",
    "25": "omiya", "26": "seibuen", "27": "keiokaku", "28": "tachikawa",
    "31": "matsudo", "32": "chiba", "34": "kawasaki", "35": "hiratsuka",
    "36": "odawara", "37": "ito", "38": "shizuoka",
    "42": "nagoya", "43": "gifu", "44": "ogaki", "45": "toyohashi",
    "46": "toyama", "47": "matsusaka", "48": "yokkaichi",
    "51": "fukui", "53": "nara", "54": "mukomachi", "55": "wakayama",
    "56": "kishiwada",
    "61": "tamano", "62": "hiroshima", "63": "hofu",
    "71": "takamatsu", "73": "komatsushima", "74": "kochi", "75": "matsuyama",
    "81": "kokura", "83": "kurume", "84": "takeo", "85": "sasebo",
    "86": "beppu", "87": "kumamoto",
}

VENUE_NAMES = {
    "11": "函館", "12": "青森", "13": "いわき平",
    "21": "弥彦", "22": "前橋", "23": "取手", "24": "宇都宮",
    "25": "大宮", "26": "西武園", "27": "京王閣", "28": "立川",
    "31": "松戸", "32": "千葉", "34": "川崎", "35": "平塚",
    "36": "小田原", "37": "伊東", "38": "静岡",
    "42": "名古屋", "43": "岐阜", "44": "大垣", "45": "豊橋",
    "46": "富山", "47": "松阪", "48": "四日市",
    "51": "福井", "53": "奈良", "54": "向日町", "55": "和歌山",
    "56": "岸和田",
    "61": "玉野", "62": "広島", "63": "防府",
    "71": "高松", "73": "小松島", "74": "高知", "75": "松山",
    "81": "小倉", "83": "久留米", "84": "武雄", "85": "佐世保",
    "86": "別府", "87": "熊本",
}

# 賭式の内部名 → 日本語
BET_TYPES = {
    "trifecta": "3連単",
    "trio": "3連複",
    "exacta": "2車単",
    "quinella": "2車複",
    "quinellaPlace": "ワイド",
    "bracketExacta": "枠単",
    "bracketQuinella": "枠複",
}


# ---------------------------------------------------------------- 出走表特徴量の抽出補助

def _norm_key(name: str) -> str:
    """JSONキーを比較しやすい形に正規化する。"""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _pick(obj: dict, *candidates, default=""):
    """候補キーの中から最初に見つかった値を返す。

    WINTICKET側のJSONキーは時期により微妙に変わる可能性があるため、
    exact match と正規化matchの両方で探す。
    """
    if not isinstance(obj, dict):
        return default
    for key in candidates:
        if key in obj and obj.get(key) not in (None, ""):
            return obj.get(key)
    normalized = {_norm_key(k): v for k, v in obj.items()}
    for key in candidates:
        v = normalized.get(_norm_key(key), default)
        if v not in (None, ""):
            return v
    return default


def _to_plain_number(v):
    """CSVに入れやすい数値/文字列へ整える。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return int(v)
    return v


def _debug_record_keys_once(record: dict):
    """DEBUG_RECORD_KEYS=1 の時だけ record のキーをログに出す。"""
    if os.environ.get("DEBUG_RECORD_KEYS") != "1":
        return
    if getattr(_debug_record_keys_once, "done", False):
        return
    _debug_record_keys_once.done = True
    try:
        print("[DEBUG] record keys:", sorted(record.keys()))
        print("[DEBUG] record sample:", json.dumps(record, ensure_ascii=False)[:2000])
    except Exception:
        pass

# ---------------------------------------------------------------- 取得まわり


def fetch_html(url: str, interval: float = REQUEST_INTERVAL) -> str:
    """ページを取得して文字列で返す(リクエスト間隔を空ける)"""
    print(f"  GET {url}")
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as res:
        html = res.read().decode("utf-8")
    time.sleep(interval)
    return html


def extract_state(html: str) -> dict:
    """HTMLから window.__PRELOADED_STATE__ のJSONを抽出する"""
    m = re.search(
        r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*window\.__",
        html,
        re.S,
    )
    if not m:
        raise ValueError("__PRELOADED_STATE__ が見つかりません(ページ構造が変わった可能性)")
    return json.loads(m.group(1))


def get_query_data(state: dict, fetch_name: str):
    """tanStackQuery のクエリ結果から指定名のデータを取り出す"""
    for q in state.get("tanStackQuery", {}).get("queries", []):
        key = q.get("queryKey") or []
        if len(key) >= 2 and key[1] == fetch_name:
            return q.get("state", {}).get("data")
    return None


def _scan_for_cups(obj, found):
    """ステート全体を再帰的に走査して開催(cup)らしきオブジェクトを集める。
    ページ内のデータ配置が変わっても動くようにするための防御的実装。"""
    if isinstance(obj, dict):
        if {"id", "startDate", "endDate", "venueId"} <= set(obj.keys()):
            found[obj["id"]] = obj
        for v in obj.values():
            _scan_for_cups(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _scan_for_cups(v, found)

# ---------------------------------------------------------------- 解析関数


def parse_schedule(state: dict, target_date: str) -> list:
    """対象日(YYYYMMDD)に開催中の開催(cup)一覧を返す。
    トップページ(/keirin)のステートから抽出する。"""
    cups = {}
    _scan_for_cups(state, cups)
    out = []
    for cup in cups.values():
        if cup["startDate"] <= target_date <= cup["endDate"]:
            venue_id = str(cup["venueId"])
            slug = VENUE_SLUGS.get(venue_id)
            if not slug:
                print(f"  ! 未知の会場ID: {venue_id}({cup.get('name')})スキップ")
                continue
            start = datetime.strptime(cup["startDate"], "%Y%m%d")
            target = datetime.strptime(target_date, "%Y%m%d")
            out.append({
                "cup_id": cup["id"],
                "cup_name": cup.get("name", ""),
                "venue_id": venue_id,
                "venue_name": VENUE_NAMES.get(venue_id, ""),
                "venue_slug": slug,
                "start_date": cup["startDate"],
                "end_date": cup["endDate"],
                "grade": cup.get("grade", ""),
                # 開催何日目か(=URLのindex)。初日=1
                "index": (target - start).days + 1,
            })
    return out


def parse_racecard(state: dict) -> list:
    """結果(またはレース詳細)ページのステートから出走表データを返す。
    1行 = 1選手。

    v101拡張:
      基本情報に加えて、出走前データとして表示される
      S/H/B、逃・捲・差・マ、1着/2着/3着/着外、勝率、2連対率、3連対率、
      ギア倍数、コメントを保存する。

    注意:
      WINTICKET側JSONのキー名変更に備えて、複数の候補キーを防御的に読む。
      もし追加列が空になる場合は、GitHub Actionsで DEBUG_RECORD_KEYS=1 を付けて
      1回実行すると、recordの実キーをログに出せる。
    """
    data = get_query_data(state, "FETCH_KEIRIN_RACE")
    if not data:
        raise ValueError("FETCH_KEIRIN_RACE のデータが見つかりません")

    race = data["race"]
    players = {p["id"]: p for p in data.get("players", [])}
    records = {r["playerId"]: r for r in data.get("records", [])}

    # ライン予想: 車番 → ライン情報
    line_of = {}           # 車番 → 所属ライン番号
    line_position_of = {}  # 車番 → ライン内の番手(1=先頭, 2=番手, 3=三番手...)
    line_order_of = {}     # 車番 → ライン全体の並び(例: 7-1-3)
    line_size_of = {}      # 車番 → ライン人数
    line_head_of = {}      # 車番 → ライン先頭の車番
    is_single_of = {}      # 車番 → 単騎なら1、それ以外0

    lp = data.get("linePrediction") or {}
    for i, line in enumerate(lp.get("lines", []), start=1):
        numbers = []
        for ent in line.get("entries", []):
            for num in ent.get("numbers", []):
                numbers.append(num)

        seen = set()
        numbers = [n for n in numbers if not (n in seen or seen.add(n))]

        line_order = "-".join(map(str, numbers))
        line_size = len(numbers)
        line_head = numbers[0] if numbers else ""

        for pos, num in enumerate(numbers, start=1):
            line_of[num] = i
            line_position_of[num] = pos
            line_order_of[num] = line_order
            line_size_of[num] = line_size
            line_head_of[num] = line_head
            is_single_of[num] = int(line_size == 1)

    rows = []
    for e in data.get("entries", []):
        p = players.get(e["playerId"], {})
        r = records.get(e["playerId"], {})
        _debug_record_keys_once(r)

        # 画面の基本情報タブにある成績系。キー名変更に備えて候補を多めに持つ。
        standing_count = _pick(r, "standingCount", "startCount", "sCount", "standing", "start")
        home_count = _pick(r, "homeCount", "hCount", "home")
        back_count = _pick(r, "backCount", "bCount", "back")

        escape_count = _pick(r, "escapeCount", "nigeCount", "runAwayCount", "leadingCount", "leadCount", "escape", "nige", "frontRunner")
        makuri_count = _pick(r, "makuriCount", "sprintCount", "dashCount", "sweepCount", "makuri", "stalker")
        sashi_count = _pick(r, "sashiCount", "sashCount", "passingCount", "passCount", "sashi", "deepCloser")
        mark_count = _pick(r, "markCount", "maCount", "mark", "marker")

        first_count = _pick(r, "firstCount", "firstPlaceCount", "winCount", "winsCount", "first")
        second_count = _pick(r, "secondCount", "secondPlaceCount", "place2Count", "second")
        third_count = _pick(r, "thirdCount", "thirdPlaceCount", "place3Count", "third")
        out_count = _pick(r, "outCount", "outsideCount", "otherCount", "rankOutCount", "unplacedCount", "loseCount", "out", "others")

        rows.append({
            "race_id": race["id"],
            "schedule_id": race["scheduleId"],
            "race_number": race["number"],
            "car_number": e["number"],
            "bracket_number": e["bracketNumber"],
            "absent": int(e.get("absent", False)),
            "player_id": e["playerId"],
            "player_name": p.get("name", ""),
            "player_yomi": p.get("yomi", ""),
            "age": p.get("age", ""),
            "prefecture": p.get("prefecture", ""),
            "term": p.get("term", ""),
            "class": e.get("playerCurrentTermClass", ""),
            "group": e.get("playerCurrentTermGroup", ""),
            "gear_ratio": _pick(r, "gearRatio", "gear", default=""),
            "style": _pick(r, "style", "legType", default=""),
            "race_point": _pick(r, "racePoint", "score", "point", default=""),
            "first_rate": _pick(r, "firstRate", "winRate", "winningRate", default=""),
            "second_rate": _pick(r, "secondRate", "quinellaRate", "twoRate", default=""),
            "third_rate": _pick(r, "thirdRate", "trioRate", "threeRate", default=""),

            # v101 追加列: 画面表示にある出走前の詳細特徴量
            "standing_count": _to_plain_number(standing_count),  # S
            "home_count": _to_plain_number(home_count),          # H
            "back_count": _to_plain_number(back_count),          # B
            "escape_count": _to_plain_number(escape_count),      # 逃
            "makuri_count": _to_plain_number(makuri_count),      # 捲
            "sashi_count": _to_plain_number(sashi_count),        # 差
            "mark_count": _to_plain_number(mark_count),          # マ
            "first_count": _to_plain_number(first_count),        # 1着数
            "second_count": _to_plain_number(second_count),      # 2着数
            "third_count": _to_plain_number(third_count),        # 3着数
            "out_count": _to_plain_number(out_count),            # 着外数

            "line": line_of.get(e["number"], ""),
            "line_position": line_position_of.get(e["number"], ""),
            "line_order": line_order_of.get(e["number"], ""),
            "line_size": line_size_of.get(e["number"], ""),
            "line_head": line_head_of.get(e["number"], ""),
            "is_single": is_single_of.get(e["number"], ""),
            "line_type": lp.get("lineType", ""),
            "comment": _pick(r, "comment", "playerComment", default=""),
        })
    return rows

def parse_odds(state: dict) -> list:
    """結果ページのステートから全オッズを返す。
    1行 = 1レース × 1賭式 × 1組み合わせ。

    注意:
      結果ページから取得できるのは、基本的に締切後に残っている最終オッズです。
      時間変化を取りたい場合は、発走前に定期実行する別スクリプトが必要です。
    """
    race_data = get_query_data(state, "FETCH_KEIRIN_RACE")
    odds_data = get_query_data(state, "FETCH_KEIRIN_RACE_ODDS")
    if not race_data or not odds_data:
        return []

    race = race_data["race"]
    start = datetime.fromtimestamp(race["startAt"], JST) if race.get("startAt") else None
    race_date = start.strftime("%Y%m%d") if start else ""
    rows = []
    for bet_key, bet_name in BET_TYPES.items():
        for o in odds_data.get(bet_key, []) or []:
            key = o.get("key", [])
            if isinstance(key, list):
                combination = "-".join(map(str, key))
            else:
                combination = str(key)

            # WINTICKETのstateでは odds / oddsValue / payout 等、構造変更の可能性があるため
            # まず既存のpayoffUnitPriceを最優先し、他の候補も防御的に見る。
            odds_value = (
                o.get("payoffUnitPrice")
                or o.get("odds")
                or o.get("oddsValue")
                or o.get("payout")
                or ""
            )

            rows.append({
                "race_id": race["id"],
                "date": race_date,
                "schedule_id": race["scheduleId"],
                "race_number": race["number"],
                "bet_key": bet_key,
                "bet_type": bet_name,
                "odds_id": o.get("id", ""),
                "combination": combination,          # 例: 7-4-5 / 2-5 / 1-3
                "odds": odds_value,                 # 100円あたりの想定払戻額/オッズ値
                "popularity": o.get("popularityOrder", ""),
                "absent": int(o.get("absent", False)) if isinstance(o.get("absent", False), bool) else o.get("absent", ""),
            })
    return rows

def parse_result(state: dict) -> dict:
    """結果ページのステートから着順と払戻を返す。
    {"orders": [...], "payoffs": [...]} の形式。"""
    data = get_query_data(state, "FETCH_KEIRIN_RACE")
    odds_data = get_query_data(state, "FETCH_KEIRIN_RACE_ODDS")
    if not data:
        raise ValueError("FETCH_KEIRIN_RACE のデータが見つかりません")

    race = data["race"]
    players = {p["id"]: p for p in data.get("players", [])}
    car_of = {e["playerId"]: e["number"] for e in data.get("entries", [])}

    # --- 着順 ---
    orders = []
    for res in data.get("results", []):
        orders.append({
            "race_id": race["id"],
            "schedule_id": race["scheduleId"],
            "race_number": race["number"],
            "order": res.get("order", ""),             # 着順
            "car_number": car_of.get(res["playerId"], ""),
            "player_id": res["playerId"],
            "player_name": players.get(res["playerId"], {}).get("name", ""),
            "factor": res.get("factor", ""),            # 決まり手(逃/差/捲/マ)
            "final_half": res.get("finalHalfRecord", ""),  # 上がりタイム
            "margin": res.get("margin", ""),            # 着差
            "accident": res.get("accidentName", ""),    # 失格・落車等
            "standing": int(res.get("standing", False)),  # S(スタート)
            "back": int(res.get("back", False)),          # B(バック)
        })

    # --- 払戻 ---
    # 的中目のID一覧(<bet>WinningOddsIds)と、オッズ一覧のpayoffUnitPriceを突き合わせる
    payoffs = []
    if odds_data:
        for bet_key, bet_name in BET_TYPES.items():
            win_ids = set(data.get(f"{bet_key}WinningOddsIds") or [])
            if not win_ids:
                continue
            for o in odds_data.get(bet_key, []):
                if o["id"] in win_ids:
                    payoffs.append({
                        "race_id": race["id"],
                        "race_number": race["number"],
                        "bet_type": bet_name,
                        "combination": "-".join(map(str, o["key"])),  # 例 7-4-5
                        "payoff": o.get("payoffUnitPrice", ""),       # 100円あたり払戻(円)
                        "popularity": o.get("popularityOrder", ""),   # 人気順
                    })

    return {"orders": orders, "payoffs": payoffs}


def parse_race_meta(state: dict, cup_info: dict) -> dict:
    """レース基本情報(1行 = 1レース)"""
    data = get_query_data(state, "FETCH_KEIRIN_RACE")
    race = data["race"]
    start = datetime.fromtimestamp(race["startAt"], JST) if race.get("startAt") else None
    return {
        "race_id": race["id"],
        "date": start.strftime("%Y%m%d") if start else "",
        "venue_id": cup_info["venue_id"],
        "venue_name": cup_info["venue_name"],
        "cup_id": cup_info["cup_id"],
        "cup_name": cup_info["cup_name"],
        "grade": cup_info.get("grade", ""),
        "day_index": cup_info["index"],
        "race_number": race["number"],
        "class": race.get("class", ""),       # S級/A級/L級
        "race_type": race.get("raceType3", ""),  # 予選/準決勝/決勝など
        "distance": race.get("distance", ""),
        "lap": race.get("lap", ""),
        "entries_number": race.get("entriesNumber", ""),
        "start_at": start.strftime("%Y-%m-%d %H:%M") if start else "",
        "weather": race.get("weather", ""),
        "wind_speed": race.get("windSpeed", ""),
        "cancel": int(race.get("cancel", False)),
    }

# ---------------------------------------------------------------- 保存まわり


def append_csv(path: Path, rows: list, key_fields: list):
    """CSVに追記する。key_fieldsの組み合わせが既存行と重複するものは追記しない。

    ただし、旧版CSVに後から列を追加した場合は、同じキーの既存行についても
    「空欄の列」を新しい解析結果で埋める。

    例:
      旧版 racecards.csv に line_position / line_order 等が無い、または空欄の場合、
      同じ日付を再実行すると既存行の空欄を補完する。
    """
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)

    desired_fields = list(rows[0].keys())
    old_rows = []
    old_fields = []
    existing_keys = set()
    row_index_by_key = {}

    if path.exists():
        with open(path, newline="", encoding=CSV_READ_ENCODING) as f:
            reader = csv.DictReader(f)
            old_fields = reader.fieldnames or []
            for idx, row in enumerate(reader):
                old_rows.append(row)
                key = tuple(str(row.get(k, "")) for k in key_fields)
                existing_keys.add(key)
                # 同一キーが万一複数あっても、最初の行を更新対象にする
                row_index_by_key.setdefault(key, idx)

    # 既存ヘッダーを優先し、新しい列だけ末尾に追加する
    fieldnames = list(old_fields) if old_fields else list(desired_fields)
    for f in desired_fields:
        if f not in fieldnames:
            fieldnames.append(f)

    new_rows = []
    updated_existing = False

    for r in rows:
        key = tuple(str(r.get(k, "")) for k in key_fields)
        if key in existing_keys:
            # 既存行がある場合は、空欄の列だけ新しい値で補完する
            idx = row_index_by_key.get(key)
            if idx is not None:
                old = old_rows[idx]
                for f in fieldnames:
                    old_val = old.get(f, "")
                    new_val = r.get(f, "")
                    if (old_val is None or str(old_val) == "") and new_val not in (None, ""):
                        old[f] = new_val
                        updated_existing = True
            continue
        existing_keys.add(key)
        row_index_by_key[key] = len(old_rows) + len(new_rows)
        new_rows.append(r)

    # 既存CSVに新列が増えた、または既存行を補完した場合は書き直す
    if path.exists() and old_fields and (fieldnames != old_fields or updated_existing):
        with open(path, "w", newline="", encoding=CSV_WRITE_ENCODING) as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(old_rows)

    if not new_rows:
        return 0

    write_header = not path.exists()
    append_encoding = CSV_WRITE_ENCODING if write_header else CSV_APPEND_ENCODING
    with open(path, "a", newline="", encoding=append_encoding) as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)
    return len(new_rows)


def append_odds_csv(rows: list):
    """オッズCSVを月別に保存する。

    10000レース規模では odds.csv がGitHubの100MB制限を超えやすいため、
    data/YYYY/odds_YYYYMM.csv に分割して保存する。
    例: data/2026/odds_202606.csv
    """
    if not rows:
        return 0
    buckets = {}
    for r in rows:
        d = str(r.get("date", ""))
        ym = d[:6] if len(d) >= 6 else "unknown"
        year = ym[:4] if len(ym) >= 4 else "unknown"
        buckets.setdefault((year, ym), []).append(r)

    total = 0
    for (year, ym), group in sorted(buckets.items()):
        total += append_csv(DATA_DIR / year / f"odds_{ym}.csv", group,
                            ["race_id", "bet_type", "combination"])
    return total

# ---------------------------------------------------------------- メイン処理


def collect_date(target_date: str):
    """指定日(YYYYMMDD)の全レースを収集する"""
    print(f"=== {target_date} のデータ収集 ===")

    # 1. トップページから当日開催中の開催一覧を取得
    top_html = fetch_html(f"{BASE_URL}/keirin")
    top_state = extract_state(top_html)
    cups = parse_schedule(top_state, target_date)
    if not cups:
        print("対象日の開催が見つかりませんでした")
        return
    print(f"開催数: {len(cups)}")
    for c in cups:
        print(f"  {c['venue_name']} {c['cup_name']} ({c['index']}日目)")

    all_meta, all_cards, all_orders, all_payoffs, all_odds = [], [], [], [], []

    # 2. 開催ごとに各レースの結果ページを取得
    for cup in cups:
        print(f"\n--- {cup['venue_name']} {cup['cup_name']} ---")
        # まず1Rを取得し、その日のレース数をステートから確認
        url1 = (f"{BASE_URL}/keirin/{cup['venue_slug']}/raceresult/"
                f"{cup['cup_id']}/{cup['index']}/1")
        try:
            state = extract_state(fetch_html(url1))
        except Exception as e:
            print(f"  ! 取得失敗: {e}")
            continue

        # その日のレース数を開催情報から数える
        cup_races = get_query_data(state, "FETCH_KEIRIN_CUP_RACES") or {}
        schedules = cup_races.get("schedules", [])
        sched = next((s for s in schedules if s.get("date") == target_date), None)
        if sched:
            n_races = sum(1 for r in cup_races.get("races", [])
                          if r.get("scheduleId") == sched.get("id"))
        else:
            n_races = 12  # 確認できなければ最大12Rまで試す
        n_races = n_races or 12
        print(f"  レース数: {n_races}")

        for rn in range(1, n_races + 1):
            if rn > 1:  # 1Rは取得済み
                url = (f"{BASE_URL}/keirin/{cup['venue_slug']}/raceresult/"
                       f"{cup['cup_id']}/{cup['index']}/{rn}")
                try:
                    state = extract_state(fetch_html(url))
                except Exception as e:
                    print(f"  ! {rn}R 取得失敗: {e}")
                    continue
            try:
                meta = parse_race_meta(state, cup)
                cards = parse_racecard(state)
                result = parse_result(state)
                odds = parse_odds(state)
            except Exception as e:
                print(f"  ! {rn}R 解析失敗: {e}")
                continue

            if not result["orders"]:
                print(f"  {rn}R: 結果未確定(中止/未開催?)スキップ")
                continue

            all_meta.append(meta)
            all_cards.extend(cards)
            all_orders.extend(result["orders"])
            all_payoffs.extend(result["payoffs"])
            all_odds.extend(odds)
            print(f"  {rn}R: 出走{len(cards)}名 / 払戻{len(result['payoffs'])}件 / オッズ{len(odds)}件 OK")

    # 3. CSVに保存(年別フォルダ。1ファイル100MB制限対策)
    year_dir = DATA_DIR / target_date[:4]
    print("\n=== 保存 ===")
    n = append_csv(year_dir / "races.csv", all_meta, ["race_id"])
    print(f"{target_date[:4]}/races.csv      +{n}行")
    n = append_csv(year_dir / "racecards.csv", all_cards, ["race_id", "car_number"])
    print(f"{target_date[:4]}/racecards.csv  +{n}行")
    n = append_csv(year_dir / "results.csv", all_orders, ["race_id", "car_number"])
    print(f"{target_date[:4]}/results.csv    +{n}行")
    n = append_csv(year_dir / "payoffs.csv", all_payoffs,
                   ["race_id", "bet_type", "combination"])
    print(f"{target_date[:4]}/payoffs.csv    +{n}行")
    n = append_odds_csv(all_odds)
    print(f"{target_date[:4]}/odds_YYYYMM.csv +{n}行")


def main():
    # 引数で日付指定可(YYYYMMDD)。指定がなければ前日(JST)を収集。
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        target = (datetime.now(JST) - timedelta(days=1)).strftime("%Y%m%d")
    collect_date(target)


if __name__ == "__main__":
    main()
