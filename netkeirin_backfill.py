#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netkeirin_backfill.py — 収集が抜けている過去の日を netkeirin から少しずつ補充する（私的利用のみ）

  python netkeirin_backfill.py --out-dir _data_repo/data --from 20260701 --budget-min 150

  * data/YYYY/races.csv の日別レース数が --min-races 未満の日を「抜けている日」とみなす
  * 古い日から順に、取得済みのレースは飛ばして補充する（WINTICKET 由来のデータは上書きしない）
  * --budget-min 分を超えたら次の日に進まず終了する（続きは次回の実行で）
  * 補充を試みた日は data/netkeirin_backfill.json に記録し、同じ日を繰り返さない
    （netkeirin 側にも少ししか無い日で、毎回取り直さないため）
  * 抜けている日が無くなったら、残り時間で「netkeirin の最終オッズが無いレース」（WINTICKET 由来の旧形式など。
    ワイドの倍率が欠けていて検証できない）のオッズだけを 1レース1リクエストで取り直す（--odds-from 以降の日）
  * 「印だけ取り直す」段階（--marks-from / --marks-budget-min）: data/YYYY/marks.csv にまだ無いレースの出走表ページを
    1レース1リクエストで取り、本紙の印・3連単ユーザー支持率（marks.csv）と本紙の買い目（honshi_picks.csv）を追記する。
    racecards.csv（WINTICKET 由来を含む）は書き換えない。有料予想（ウマい車券）には触れない
  * 全段階の合計時間は --total-budget-min 以内に収める
  * リクエスト間隔は netkeirin_collector.REQUEST_INTERVAL（3秒）のまま
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import netkeirin_collector as N

PROGRESS = 'netkeirin_backfill.json'


def known_races(out_dir: Path) -> tuple:
    """(日別レース数 Counter, 取得済みレースID set)。年フォルダの races.csv を読む。"""
    per_day, ids = Counter(), set()
    for p in sorted(out_dir.glob('*/races.csv')):
        with open(p, newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                rid, d = row.get('race_id', ''), str(row.get('date', ''))[:8]
                if rid and rid not in ids:
                    ids.add(rid)
                    per_day[d] += 1
    return per_day, ids


def load_progress(out_dir: Path) -> dict:
    try:
        return json.loads((out_dir / PROGRESS).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_progress(out_dir: Path, prog: dict):
    (out_dir / PROGRESS).write_text(json.dumps(prog, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')


def races_of_day(out_dir: Path, date: str) -> dict:
    """その日のレースID（WINTICKET 形式）→ 払戻の行"""
    ids = {}
    p = out_dir / date[:4] / 'races.csv'
    if p.exists():
        with open(p, newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                if str(row.get('date', ''))[:8] == date and str(row.get('cancel', '0')) in ('0', '', '0.0'):
                    ids[row['race_id']] = []
    p = out_dir / date[:4] / 'payoffs.csv'
    if ids and p.exists():
        with open(p, newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                if row.get('race_id') in ids:
                    ids[row['race_id']].append(row)
    return ids


def races_with_nk_odds(out_dir: Path, date: str) -> set:
    """netkeirin の日別オッズファイルに ワイドの倍率まで入っているレース"""
    p = out_dir / date[:4] / 'odds' / f'odds_{date}.csv'
    have = set()
    if p.exists():
        with open(p, newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                if row.get('bet_type') == 'ワイド' and row.get('odds') not in ('', None):
                    have.add(row['race_id'])
    return have


def to_nk(wt_id: str) -> str:
    """WINTICKET 形式 RRVVYYYYMMDD → netkeirin 形式 YYYYMMDDVVRR"""
    return wt_id[4:12] + wt_id[2:4] + wt_id[0:2]


def refetch_odds(out_dir: Path, prog: dict, d_from: str, d_to: str, deadline: float) -> int:
    """最終オッズが netkeirin 形式で無いレースのオッズだけを取り直す。取り直した日数を返す。"""
    import netkeirin_collector as NC
    import keirin_collector as KC
    KC.DATA_DIR = out_dir
    done = prog.setdefault('_odds', {})
    days = 0
    d0, d1 = datetime.strptime(d_from, '%Y%m%d'), datetime.strptime(d_to, '%Y%m%d')
    while d0 <= d1 and time.monotonic() < deadline:
        d = d0.strftime('%Y%m%d')
        d0 += timedelta(days=1)
        if d in done:
            continue
        races = races_of_day(out_dir, d)
        todo = [r for r in sorted(races) if r not in races_with_nk_odds(out_dir, d)]
        rows, ok, fail = [], 0, 0
        for rid in todo:
            if time.monotonic() >= deadline:
                break
            try:
                rows.extend(NC.fetch_odds(to_nk(rid), races[rid]))
                ok += 1
            except NC.Blocked:
                if rows:
                    KC.append_odds_csv(rows)
                raise
            except (NC.HTTPError, NC.URLError, ValueError, KeyError, IndexError, TypeError):
                fail += 1
        KC.append_odds_csv(rows)
        finished = ok + fail == len(todo)
        print(f'オッズの取り直し {d}: 対象 {len(todo)} / 成功 {ok} / 失敗 {fail}' + ('' if finished else '（時間切れ・続きは次回）'))
        if finished:  # 失敗が多い日も繰り返さない（古すぎて配信されていない場合など）。件数は記録に残す
            done[d] = dict(refetched=ok, failed=fail)
        days += 1
    return days


def races_with_marks(out_dir: Path) -> set:
    """marks.csv に1行でもあるレースID（WINTICKET 形式）"""
    have = set()
    for p in sorted(out_dir.glob('*/marks.csv')):
        with open(p, newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                if row.get('race_id'):
                    have.add(row['race_id'])
    return have


def marks_todo(out_dir: Path, prog: dict, d_from: str, d_to: str) -> list:
    """印の取り直し対象: d_from〜d_to の（中止でない）レースのうち、marks.csv に無く、取得失敗の記録も無いもの"""
    have = races_with_marks(out_dir)
    failed = set(prog.get('_marks_failed', {}))
    out = []
    for p in sorted(out_dir.glob('*/races.csv')):
        with open(p, newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                d, rid = str(row.get('date', ''))[:8], row.get('race_id', '')
                if not rid or not (d_from <= d <= d_to) or str(row.get('cancel', '0')) not in ('0', '', '0.0'):
                    continue
                if rid not in have and rid not in failed:
                    out.append((d, rid))
    return sorted(set(out))


def refetch_marks(out_dir: Path, prog: dict, d_from: str, d_to: str, deadline: float) -> dict:
    """本紙の印・支持率・買い目だけを取り直す（出走表ページ 1レース1リクエスト）"""
    todo = marks_todo(out_dir, prog, d_from, d_to)
    failed = prog.setdefault('_marks_failed', {})
    st = dict(todo=len(todo), ok=0, failed=0, no_card=0)
    cards, picks = [], []

    def flush():
        if cards or picks:
            N.save_marks(out_dir, cards, picks)
            cards.clear()
            picks.clear()
    try:
        for i, (d, rid) in enumerate(todo):
            if time.monotonic() >= deadline:
                break
            nk = to_nk(rid)
            try:
                html = N.fetch(f'{N.BASE_URL}/race/entry/', {'race_id': nk})
                c = N.parse_racecard(html, nk)
                html_picks = N.parse_honshi_picks(html, nk)
            except N.Blocked:
                raise
            except (N.HTTPError, N.URLError, ValueError, AttributeError, IndexError) as e:
                st['failed'] += 1
                failed[rid] = type(e).__name__  # 同じレースを毎回取り直さない
                continue
            if not c:
                st['no_card'] += 1
                failed[rid] = 'no_card'
                continue
            cards.extend(c)
            picks.extend(html_picks)
            st['ok'] += 1
            if len(cards) >= 500:
                flush()
    finally:
        flush()
    print(f"印の取り直し: 対象 {st['todo']} / 成功 {st['ok']} / 失敗 {st['failed']} / 出走表なし {st['no_card']} / "
          f"残り {st['todo'] - st['ok'] - st['failed'] - st['no_card']}")
    return st


def todo_dates(per_day: Counter, prog: dict, d_from: str, d_to: str, min_races: int) -> list:
    d0 = datetime.strptime(d_from, '%Y%m%d')
    d1 = datetime.strptime(d_to, '%Y%m%d')
    out = []
    while d0 <= d1:
        d = d0.strftime('%Y%m%d')
        if per_day.get(d, 0) < min_races and d not in prog:  # '_odds' は日付ではないので入らない
            out.append(d)
        d0 += timedelta(days=1)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='抜けている過去の日を netkeirin から補充（私的利用のみ）')
    ap.add_argument('--out-dir', required=True, help='データ用リポジトリの data フォルダ（公開リポジトリ内は不可）')
    ap.add_argument('--from', dest='date_from', default='20260701')
    ap.add_argument('--to', dest='date_to', help='既定: 2日前（前日分は毎朝の収集に任せる）')
    ap.add_argument('--min-races', type=int, default=120, help='これ未満のレース数の日を補充対象にする')
    ap.add_argument('--budget-min', type=float, default=150, help='この分数を超えたら次の日に進まない')
    ap.add_argument('--odds-from', default='20260401', help='最終オッズを取り直す期間の開始日（抜けている日が無い時だけ）')
    ap.add_argument('--odds-budget-min', type=float, default=0, help='オッズの取り直しに使う時間の上限（0=しない）')
    ap.add_argument('--marks-from', help='本紙の印・支持率・買い目を取り直す期間の開始日（指定時のみ）')
    ap.add_argument('--marks-budget-min', type=float, default=0, help='印の取り直しに使う時間の上限（0=しない）')
    ap.add_argument('--total-budget-min', type=float, default=160, help='全段階の合計時間の上限（分）')
    a = ap.parse_args(argv)

    out_dir = Path(a.out_dir)
    d_to = a.date_to or (datetime.now(N.JST) - timedelta(days=2)).strftime('%Y%m%d')
    per_day, ids = known_races(out_dir)
    prog = load_progress(out_dir)
    dates = todo_dates(per_day, prog, a.date_from, d_to, a.min_races)
    print(f'補充対象の日: {len(dates)}（{a.date_from}〜{d_to}、{a.min_races}レース未満）')
    t0 = time.monotonic()
    done = 0
    try:
        for d in dates:
            if (time.monotonic() - t0) / 60 > a.budget_min:
                print('時間の上限に達したため、続きは次回に回します')
                break
            st = N.collect_date(d, out_dir, skip=ids)
            prog[d] = dict(listed=st.get('listed', 0), added=st['races'], failed=st['failed'],
                           at=datetime.now(N.JST).strftime('%Y-%m-%d %H:%M'))
            if st['failed'] and st['failed'] >= max(5, st.get('listed', 0) // 2):
                del prog[d]  # 失敗が多い日は記録せず、次回もう一度試す
            save_progress(out_dir, prog)
            done += 1
    except N.Blocked as e:
        print(f'!! サーバーから拒否されたため中断しました（回避はしません）: {e}')
        save_progress(out_dir, prog)
        sys.exit(2)
    left = len(dates) - done
    print(f'今回の補充: {done}日 / 残り {left}日')
    def remaining(limit):  # 段階の上限と、合計の残り時間の小さい方（分）
        return max(0.0, min(limit, a.total_budget_min - (time.monotonic() - t0) / 60))

    if left == 0 and a.odds_budget_min > 0:
        reserve = a.marks_budget_min if a.marks_from else 0  # 印の段階の時間を残しておく
        deadline = time.monotonic() + max(0.0, min(remaining(a.odds_budget_min),
                                                   a.total_budget_min - reserve - (time.monotonic() - t0) / 60)) * 60
        try:
            refetch_odds(out_dir, prog, a.odds_from, d_to, deadline)
        except N.Blocked as e:
            print(f'!! サーバーから拒否されたため中断しました（回避はしません）: {e}')
            save_progress(out_dir, prog)
            sys.exit(2)
        save_progress(out_dir, prog)
    if a.marks_from and a.marks_budget_min > 0 and remaining(a.marks_budget_min) > 0:
        deadline = time.monotonic() + remaining(a.marks_budget_min) * 60
        try:
            refetch_marks(out_dir, prog, a.marks_from, d_to, deadline)
        except N.Blocked as e:
            print(f'!! サーバーから拒否されたため中断しました（回避はしません）: {e}')
            save_progress(out_dir, prog)
            sys.exit(2)
        save_progress(out_dir, prog)


if __name__ == '__main__':
    main()
