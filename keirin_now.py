#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keirin_now.py — 発走前のレースの出走表と最新オッズを自動取得して、競輪ロジック v105 で判定する

使い方:
  python keirin_now.py 松戸 5                 # 今日の松戸5R
  python keirin_now.py 松戸 5 --date 20261003  # 日付指定
  python keirin_now.py --scan                  # 今日これから発走する全レースを判定（勝負レースだけ表示）
  python keirin_now.py 松戸 5 --json           # AI・他ツール向けのJSON出力
  python keirin_now.py 松戸 5 --debug          # 取得したページの中身（データの種類）を表示

オプション: --bankroll 30000 / --all-types（2車複以外も候補に） / --ev 1.10

【利用上の注意】keirin_collector.py 冒頭の注意事項がそのまま適用されます。
取得先サイトの利用規約を確認のうえ、ご自身の私的な分析の範囲で利用してください。
取得した情報の再配布・公開はしないでください。リクエスト間隔（2秒）を短くしないでください。
"""
import argparse
import json
import os
import sys
from datetime import datetime
from urllib.error import HTTPError

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import keirin_collector as C  # noqa: E402
import keirin_engine as E  # noqa: E402

# 発走前のレースページの候補（WINTICKETのURL構造が変わっても順に試す）。
# 2026-10 の実ページで確認: racecard / odds のどちらも FETCH_KEIRIN_RACE（出走表）と
# FETCH_KEIRIN_RACE_ODDS（最新オッズ）を含む。raceresult は旧来の結果ページ（予備）。
# 最初に「出走表＋オッズ」が取れた形を .keirin_now_url.json に記憶して次回から優先する。
URL_PATTERNS = [
    '{base}/keirin/{slug}/racecard/{cup}/{idx}/{rn}',
    '{base}/keirin/{slug}/odds/{cup}/{idx}/{rn}',
    '{base}/keirin/{slug}/raceresult/{cup}/{idx}/{rn}',
]
URL_MEMO = os.path.join(HERE, '.keirin_now_url.json')


def _patterns():
    pats = list(URL_PATTERNS)
    try:
        with open(URL_MEMO, encoding='utf-8') as f:
            best = json.load(f).get('pattern')
        if best in pats:
            pats.remove(best)
            pats.insert(0, best)
    except (OSError, ValueError):
        pass
    return pats


def _remember(pattern):
    try:
        with open(URL_MEMO, 'w', encoding='utf-8') as f:
            json.dump({'pattern': pattern}, f)
    except OSError:
        pass


def query_names(state):
    out = []
    for q in state.get('tanStackQuery', {}).get('queries', []):
        key = q.get('queryKey') or []
        if len(key) >= 2:
            out.append(str(key[1]))
    return out


def find_cup(venue, date):
    """トップページから、その日に開催中の開催（会場名 or スラッグ一致）を探す"""
    state = C.extract_state(C.fetch_html(f'{C.BASE_URL}/keirin'))
    cups = C.parse_schedule(state, date)
    if venue is None:
        return cups
    hit = [c for c in cups if venue in (c['venue_name'], c['venue_slug'])]
    if not hit:
        names = '、'.join(c['venue_name'] for c in cups) or '（なし）'
        raise SystemExit(f'{date} に「{venue}」の開催が見つかりません。開催中: {names}')
    return hit


def fetch_race(cup, rn, debug=False):
    """発走前ページから 出走表・オッズ・レース情報 を取得。(cards, odds, meta, state)"""
    cards = odds = None
    meta = None
    state_used = None
    for pat in _patterns():
        url = pat.format(base=C.BASE_URL, slug=cup['venue_slug'], cup=cup['cup_id'], idx=cup['index'], rn=rn)
        try:
            state = C.extract_state(C.fetch_html(url))
        except HTTPError as e:
            if debug:
                print(f'  [debug] {url} → HTTP {e.code}')
            continue
        except Exception as e:  # noqa: BLE001
            if debug:
                print(f'  [debug] {url} → {e}')
            continue
        if debug:
            print(f'  [debug] {url} → データ: {", ".join(query_names(state))}')
        try:
            c = C.parse_racecard(state)
        except Exception:  # noqa: BLE001
            c = None
        o = C.parse_odds(state)
        if c and cards is None:
            cards = c
            meta = C.parse_race_meta(state, cup)
            state_used = state
        if o and any(x['bet_type'] == '3連単' and x['odds'] != '' for x in o):
            odds = o
            _remember(pat)
            break
        if c and C.get_query_data(state, 'FETCH_KEIRIN_RACE_ODDS') is not None:
            # オッズ欄はあるが投票が未集計（発売前など）。他のURLでも同じなので、ここで打ち切る
            _remember(pat)
            break
    return cards, odds or [], meta, state_used


def race_list(state, date):
    """開催情報から、その日のレース番号と発走時刻の一覧"""
    cr = C.get_query_data(state, 'FETCH_KEIRIN_CUP_RACES') or {}
    sched = next((s for s in cr.get('schedules', []) if s.get('date') == date), None)
    races = [r for r in cr.get('races', []) if sched and r.get('scheduleId') == sched.get('id')]
    out = []
    for r in races:
        st = datetime.fromtimestamp(r['startAt'], C.JST) if r.get('startAt') else None
        out.append((int(r.get('number', 0)), st))
    return sorted(out)


def run_one(model, cup, rn, args):
    cards, odds, meta, _ = fetch_race(cup, rn, debug=args.debug)
    title = f'{cup["venue_name"]} {rn}R'
    if meta and meta.get('start_at'):
        title += f'（発走 {meta["start_at"][11:]}）'
    if not cards:
        return title, dict(ok=False, reason='出走表を取得できませんでした（--debug で詳細を確認）')
    res = E.analyze(model, cards, odds, cup['venue_name'], settings(args))
    res['title'] = title
    res['fetched_at'] = datetime.now(C.JST).strftime('%Y-%m-%d %H:%M:%S')
    return title, res


def settings(args):
    s = dict(bankroll=args.bankroll, ev_min=args.ev)
    if args.all_types:
        s['bet_types'] = 'all'
    return s


def main():
    ap = argparse.ArgumentParser(description='発走前のオッズを自動取得して競輪ロジック v105 で判定')
    ap.add_argument('venue', nargs='?', help='会場名（例: 松戸）')
    ap.add_argument('race', nargs='?', type=int, help='レース番号')
    ap.add_argument('--date', default=datetime.now(C.JST).strftime('%Y%m%d'))
    ap.add_argument('--scan', action='store_true', help='これから発走する全レースを判定')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--debug', action='store_true')
    ap.add_argument('--bankroll', type=int, default=E.DEFAULTS['bankroll'])
    ap.add_argument('--ev', type=float, default=E.DEFAULTS['ev_min'])
    ap.add_argument('--all-types', action='store_true')
    args = ap.parse_args()
    model = E.load_model()

    if args.scan:
        now = datetime.now(C.JST)
        results = []
        for cup in find_cup(args.venue, args.date):
            # 1Rのページから開催のレース一覧を取得
            _, _, _, st = fetch_race(cup, 1, debug=args.debug)
            lst = race_list(st, args.date) if st else [(n, None) for n in range(1, 13)]
            for rn, start in lst:
                if start and start < now:
                    continue
                title, res = run_one(model, cup, rn, args)
                results.append(res if res.get('ok') else dict(res, title=title))
                if not args.json:
                    mark = '✅' if res.get('ok') and not res.get('ken') else ('🛑' if res.get('ok') else '⚠')
                    print(f'{mark} {title}', flush=True)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=1, default=float))
        else:
            hits = [r for r in results if r.get('ok') and not r.get('ken')]
            print(f'\n=== 勝負レース {len(hits)} / {len(results)} ===')
            for r in hits:
                print(E.format_text(r, r['title']))
        return

    if not args.venue or not args.race:
        ap.error('会場名とレース番号を指定してください（例: python keirin_now.py 松戸 5）。全レースは --scan')
    cup = find_cup(args.venue, args.date)[0]
    title, res = run_one(model, cup, args.race, args)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    else:
        print(E.format_text(res, title))
        if res.get('ok'):
            print(f'  （取得 {res["fetched_at"]}。締切直前に再実行すると最新オッズで判定できます）')


if __name__ == '__main__':
    main()
