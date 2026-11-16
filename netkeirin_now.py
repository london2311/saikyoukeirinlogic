#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netkeirin_now.py — 発走前のレースを netkeirin から取得して v105 で判定する（私的利用のみ）

==============================================================================
【利用上の注意】
* netkeirin 利用規約により、取得データ・計算結果は私的利用の範囲内でのみ使えます。
  出力（買い目・オッズ・確率）を公開・再配布しないでください。リポジトリにもコミットしないこと。
  → 出力は --out で指定したファイル（既定: /tmp/netkeirin_now_YYYYMMDD.md）に書きます。
* アクセス間隔は 3 秒以上（netkeirin_collector.REQUEST_INTERVAL）、User-Agent は正直に名乗ります。
  403 / 429 など拒否の応答が返ったら、その場で中断します。
==============================================================================

仕組み:
  1. レース一覧 API で今日の開催場・レースID・発走時刻を取得（発走済みは除く）
  2. 各レースの出走表（/race/entry/）と現在のオッズ（POST /api/race/ class=AplRaceOdds）を取得し、
     netkeirin_collector.py と同じ行形式に変換
  3. keirin_engine.analyze()（v105、Benter型融合）で各組の当たる確率 p（融合）を計算
  4. 出力（レースごと・発走時刻順）
     - 回収率重視: ワイド・2車複を p の高い順に3点。買い基準オッズ = 1.10 ÷ p（小数第1位に切り上げ）
       現在のオッズが基準以上なら「★現在買い条件」
     - 的中率重視: ワイド本命の p ≥ 0.79、または 2車複本命の p ≥ 0.62 のレースを「当てたい人向け」に表示

使い方:
  python netkeirin_now.py                          # 今日のこれから発走する全レース
  python netkeirin_now.py --venue 34               # 場コードで絞る（例 34=川崎）
  python netkeirin_now.py --limit 20 --out /tmp/now.md
"""
import argparse
import math
import re
import sys
import time
from datetime import datetime, timedelta

import keirin_collector as KC
import keirin_engine as E
import netkeirin_collector as N

EV_MIN = 1.10          # 買い基準: 融合EV 1.10 以上（検証で使った基準）
HIT_WIDE_P = 0.79      # 的中率重視: ワイド本命1点の確率の下限
HIT_QUIN_P = 0.62      # 的中率重視: 2車複本命1点の確率の下限
TYPES = ['ワイド', '2車複']


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
    first = N.fetch(f'{N.BASE_URL}/race/api_race_list.html', params)
    races = dict(parse_race_list(first, date))
    if not venue:
        have = {r[8:10] for r in races}
        for v in N.parse_venue_codes(first):
            if v in have:
                continue
            params['jyocd'] = v
            races.update(parse_race_list(N.fetch(f'{N.BASE_URL}/race/api_race_list.html', params), date))
    elif venue:
        races = {k: v for k, v in races.items() if k[8:10] == venue}
    return sorted(races.items())


def start_dt(date: str, hhmm: str):
    if not hhmm:
        return None
    h, m = map(int, hhmm.split(':'))
    return datetime.strptime(date, '%Y%m%d').replace(tzinfo=N.JST) + timedelta(hours=h, minutes=m)


def buy_line(p: float) -> float:
    """買い基準オッズ = EV_MIN ÷ p を小数第1位に切り上げ。"""
    return math.ceil(EV_MIN / p * 10 - 1e-9) / 10 if p > 0 else float('inf')


def summarize(res: dict) -> dict:
    """analyze() の結果 → 出力用（ワイド・2車複 上位3点と基準オッズ）"""
    picks = {}
    for t in TYPES:
        rows = []
        for h in res['hit_picks'].get(t, [])[:3]:
            p, od = h['prob'], h['odds']
            line = buy_line(p)
            rows.append(dict(key=h['key'], p=p, odds=od, ev=(p * od) if od else None, line=line,
                             buy=bool(od and res['market_ok'] and od >= line)))
        picks[t] = rows
    hit = []
    if picks['ワイド'] and picks['ワイド'][0]['p'] >= HIT_WIDE_P:
        hit.append(('ワイド', picks['ワイド'][0]))
    if picks['2車複'] and picks['2車複'][0]['p'] >= HIT_QUIN_P:
        hit.append(('2車複', picks['2車複'][0]))
    return dict(picks=picks, hit=hit)


def analyze_race(model, nk: str):
    entry = N.fetch(f'{N.BASE_URL}/race/entry/', {'race_id': nk})
    cards = N.parse_racecard(entry, nk)
    odds = N.fetch_odds(nk, [])
    res = E.analyze(model, cards, odds, KC.VENUE_NAMES.get(nk[8:10], ''))
    return res, cards


def fmt_race(title: str, res: dict, s: dict) -> list:
    L = [f'### {title}', '']
    for w in res.get('warnings', []):
        L.append(f'> ⚠ {w}')
        L.append('')
    L.append('| 券種 | 組 | 当たる確率 p | 現在オッズ | 現在の融合EV | 買い基準オッズ | 判定 |')
    L.append('|---|---|---:|---:|---:|---:|---|')
    for t in TYPES:
        for r in s['picks'][t]:
            od = f'{r["odds"]:.1f}' if r['odds'] else '—'
            ev = f'{r["ev"]:.2f}' if r['ev'] is not None else '—'
            L.append(f'| {t} | {r["key"].replace("-", "=")} | {r["p"] * 100:.1f}% | {od} | {ev} | '
                     f'{r["line"]:.1f}倍以上 | {"★現在買い条件" if r["buy"] else ""} |')
    if s['hit']:
        L.append('')
        for t, r in s['hit']:
            L.append(f'- 🎯 当てたい人向け: {t} {r["key"].replace("-", "=")}（p {r["p"] * 100:.1f}%）')
    L.append('')
    return L


def main(argv=None):
    ap = argparse.ArgumentParser(description='発走前のレースを netkeirin から取得して v105 で判定（私的利用のみ）')
    ap.add_argument('--date', default=datetime.now(N.JST).strftime('%Y%m%d'))
    ap.add_argument('--venue', help='場コード2桁で絞る（例 34）')
    ap.add_argument('--limit', type=int, default=0, help='発走が近い順に最大何レース（0=全部）')
    ap.add_argument('--out', help='出力 Markdown の保存先（既定 /tmp/netkeirin_now_YYYYMMDD.md）。リポジトリ内は不可')
    a = ap.parse_args(argv)
    out_path = a.out or f'/tmp/netkeirin_now_{a.date}.md'

    t0 = time.monotonic()
    model = E.load_model()
    now = datetime.now(N.JST)
    try:
        races = list_today(a.date, a.venue)
        todo = [(nk, st) for nk, st in races if not (start_dt(a.date, st) and start_dt(a.date, st) <= now)]
        todo.sort(key=lambda x: (x[1] or '99:99', x[0]))
        if a.limit:
            todo = todo[:a.limit]
        print(f'レース {len(races)}件 / これから発走 {len(todo)}件を判定')
        blocks, hit_lines, buy_count = [], [], 0
        for nk, st in todo:
            title = f'{KC.VENUE_NAMES.get(nk[8:10], nk[8:10])} {int(nk[10:12])}R（発走 {st or "?"}）'
            try:
                res, _cards = analyze_race(model, nk)
            except N.Blocked:
                raise
            except Exception as e:  # 1レースの失敗で全体を止めない
                print(f'  ! {nk}: 失敗 {type(e).__name__}')
                blocks += [f'### {title}', '', f'取得・計算できませんでした（{type(e).__name__}）', '']
                continue
            if not res.get('ok'):
                blocks += [f'### {title}', '', f'計算できません: {res.get("reason")}', '']
                continue
            s = summarize(res)
            buy_count += sum(r['buy'] for t in TYPES for r in s['picks'][t])
            for t, r in s['hit']:
                od = f'{r["odds"]:.1f}' if r['odds'] else '—'
                hit_lines.append(f'| {title} | {t} | {r["key"].replace("-", "=")} | {r["p"] * 100:.1f}% | {od} |')
            blocks += fmt_race(title, res, s)
            print(f'  {nk}: OK')
    except N.Blocked as e:
        print(f'!! サーバーから拒否されたため中断しました（回避はしません）: {e}')
        sys.exit(2)

    elapsed = time.monotonic() - t0
    head = [
        f'# 競輪 v105 判定（netkeirin）{a.date[:4]}/{a.date[4:6]}/{a.date[6:]}',
        '',
        f'- 取得時刻: {now:%Y-%m-%d %H:%M} JST（取得にかかった時間 {elapsed / 60:.1f}分、{len(todo)}レース）',
        '- **オッズは締切まで動くので、締切5〜10分前に「買い基準オッズ」と見比べること。**'
        '締切直前のオッズが基準以上なら買い（融合EV 1.10 以上）。',
        '- 当たる確率 p は v105 モデルと3連単オッズの融合（Benter型 α=0.03 β=1.029）。ワイドのオッズは最低倍率。',
        '- 回収率重視（ワイド・2車複 上位3点）は融合EV 1.10 以上を買い条件とする。',
        f'- 的中率重視（ワイド本命 p≥{HIT_WIDE_P:.2f} / 2車複本命 p≥{HIT_QUIN_P:.2f}）は検証で的中率 86〜89% / 70〜76%、'
        '回収率は 80〜90%（100%未満）。',
        '- 私的利用のみ。公開・再配布しないこと。',
        f'- 現時点で★買い条件の組: {buy_count}',
        '',
        '## 的中率重視（当てたい人向け）',
        '',
    ]
    if hit_lines:
        head += ['| レース | 券種 | 組 | p | 現在オッズ |', '|---|---|---|---:|---:|'] + hit_lines
    else:
        head.append('該当なし')
    head += ['', '## レース別（発走時刻順）', '']
    text = '\n'.join(head + blocks)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(text + '\n')
    print(f'出力: {out_path}（{elapsed / 60:.1f}分）')


if __name__ == '__main__':
    main()
