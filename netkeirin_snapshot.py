#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netkeirin_snapshot.py — 発走前のオッズ（全券種）を決まった時刻に記録する（私的利用のみ）

  python netkeirin_snapshot.py --out-dir _data_repo/data --until 13:30 --flush-cmd "bash flush.sh"

目的: 「締切直前のオッズで判定したら本当に儲かるか」を正しく検証するため。
結果ページの最終オッズは締切後の値で、実際に買う時点のオッズとは違う。

  * 今日のレース一覧（発走時刻つき）を 30 分ごとに取り直す（発走の遅れ・変更に追従）
  * 基準は **締切**（一覧に締切時刻があればそれ、無ければ発走の5分前）。各レースの締切 --offsets 分前
    （既定 15・10・5・2分前）に、オッズ API を 1 回ずつ呼ぶ。時点の名前は C-15 / C-10 / C-5 / C-2
    （1レースあたり 4 リクエスト。間隔は netkeirin_collector.REQUEST_INTERVAL のまま）
  * 保存先: data/YYYY/odds_snap/snap_YYYYMMDD.csv（枠番の券種は保存しない）
  * --until（JST の時:分、24:20 のように翌日も可）を過ぎるか、記録すべきレースが無くなったら終了
  * --flush-cmd を指定すると、--flush-min 分ごとと終了時に実行する（データ用リポジトリへのコミット用）
"""
import argparse
import json
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import keirin_collector as KC
import netkeirin_collector as N

KEEP_TYPES = {'3連単', '3連複', '2車単', '2車複', 'ワイド'}
FIELDS = ['race_id', 'date', 'race_number', 'snap', 'snap_at', 'start_at', 'deadline_at', 'official_dt',
          'bet_type', 'combination', 'odds', 'odds_max', 'popularity', 'absent']
LIST_REFRESH_MIN = 30
LATE_MIN = 2  # 締切直前以外の時点は、予定からこの分数以上遅れたら取らない


def parse_until(date: str, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(':'))
    return datetime.strptime(date, '%Y%m%d').replace(tzinfo=N.JST) + timedelta(hours=h, minutes=m)


def snapshot_rows(resp: dict, nk: str, snap: str, snap_at: datetime, start: datetime, deadline: datetime = None) -> list:
    body = (resp.get('data') or {}).get(f'nkrace_odds::{nk}') or {}
    official = str(body.get('official_dt', '')) if isinstance(body, dict) else ''
    rows = []
    for r in N.parse_odds_api(resp, nk, []):
        if r['bet_type'] not in KEEP_TYPES:
            continue
        rows.append({
            'race_id': r['race_id'], 'date': r['date'], 'race_number': r['race_number'], 'snap': snap,
            'snap_at': snap_at.strftime('%Y-%m-%d %H:%M:%S'), 'start_at': start.strftime('%Y-%m-%d %H:%M'),
            'deadline_at': deadline.strftime('%Y-%m-%d %H:%M') if deadline else '',
            'official_dt': official, 'bet_type': r['bet_type'], 'combination': r['combination'],
            'odds': r['odds'], 'odds_max': r['odds_max'], 'popularity': r['popularity'], 'absent': r['absent'],
        })
    return rows


def fetch_snapshot(nk: str) -> dict:
    text = N.fetch(N.ODDS_API, data={'input': 'UTF-8', 'output': 'json', 'class': 'AplRaceOdds',
                                     'method': 'get', 'compress': '0', 'race_id': nk})
    return json.loads(text)


def due_jobs(races: list, date: str, offsets: list, done: set, now: datetime) -> list:
    """今記録すべき (race_id, snap名, 発走時刻, 締切時刻)。締切 − offset 分を過ぎ、まだ締切前のもの。"""
    jobs = []
    for nk, hhmm in races:
        st = N.start_dt(date, hhmm)
        dl = N.deadline_dt(date, nk, hhmm)
        if st is None or dl is None:
            continue
        for off in offsets:
            snap = f'C-{off}'
            if (nk, snap) in done:
                continue
            # 一番遅い記録以外は、予定から LATE_MIN 分以上遅れたら取らない（別の時点と混ざらないように）
            late_ok = off == min(offsets) or now < dl - timedelta(minutes=off - LATE_MIN)
            if dl - timedelta(minutes=off) <= now < dl and late_ok:
                jobs.append((dl - timedelta(minutes=off), nk, snap, st, dl))
    return [(nk, snap, st, dl) for _, nk, snap, st, dl in sorted(jobs)]


def remaining(races: list, date: str, offsets: list, done: set, now: datetime) -> int:
    n = 0
    for nk, hhmm in races:
        dl = N.deadline_dt(date, nk, hhmm)
        if dl is None or dl <= now:
            continue
        n += sum((nk, f'C-{off}') not in done for off in offsets)
    return n


ALERTER_FACTORY = None  # テスト用に差し替え可能


def make_alerter(a):
    if ALERTER_FACTORY is not None:
        return ALERTER_FACTORY(a)
    import keirin_engine as E
    import netkeirin_alert as NA
    sender = NA.gmail_sender()
    print('通知: メール設定あり' if sender else '通知: メール未設定（判定のみ）')
    return NA.Alerter(E.load_model(), sender=sender, snaps=[x.strip() for x in a.alert_snaps.split(',') if x.strip()],
                      max_per_day=a.alert_max)


SITE_FACTORY = None  # テスト用に差し替え可能


def make_site(a, date, alerter):
    import os
    if SITE_FACTORY is not None:
        return SITE_FACTORY(a, date, alerter)
    pw = os.environ.get('SITE_PASSWORD', '')
    if len(pw) < 12:
        print('期待値ボード: パスワード未設定（または12文字未満）のため作りません')
        return None
    import json
    import keirin_engine as E
    import netkeirin_site as S
    S.restore_from_branch(a.site_dir)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analysis', 'strategies.json'), encoding='utf-8') as f:
        strategies = [x for x in json.load(f)['strategies'] if x.get('alert')]
    model = alerter.model if alerter is not None else E.load_model()
    cache = alerter.cards if alerter is not None else {}
    fetch_cards = alerter.cards_fn if alerter is not None else None
    if fetch_cards is None:
        import netkeirin_alert as NA
        fetch_cards = NA.Alerter._fetch_cards
    print('期待値ボード: 作成します')
    return S.SiteBuilder(model, pw, a.site_dir, date, cards_cache=cache, cards_fn=fetch_cards, push_fn=S.git_push_fn(),
                         strategies=strategies)


def main(argv=None, clock=None, sleep=time.sleep):
    ap = argparse.ArgumentParser(description='発走前のオッズ（全券種）を記録（私的利用のみ）')
    ap.add_argument('--out-dir', required=True, help='データ用リポジトリの data フォルダ（公開リポジトリ内は不可）')
    ap.add_argument('--date', default=None, help='既定: 今日（JST）')
    ap.add_argument('--until', default='24:20', help='この時刻（JST）で終了。翌日は 24:20 のように書く')
    ap.add_argument('--offsets', default='15,10,5,2', help='締切の何分前に記録するか（カンマ区切り）')
    ap.add_argument('--flush-cmd', default='', help='定期的に実行するコマンド（コミット用）')
    ap.add_argument('--flush-min', type=float, default=60)
    ap.add_argument('--alert', action='store_true', help='事前登録した買い方を判定してメールで知らせる（netkeirin_alert.py）')
    ap.add_argument('--alert-snaps', default='C-5,C-2', help='判定する時点（--offsets に含めること）')
    ap.add_argument('--alert-max', type=int, default=60, help='1日に送るメールの上限')
    ap.add_argument('--site', action='store_true', help='期待値ボード（暗号化）のデータを作って site-data ブランチに置く（netkeirin_site.py）')
    ap.add_argument('--site-dir', default='_site_out', help='期待値ボードのデータの作業フォルダ（公開側には入れない）')
    a = ap.parse_args(argv)
    clock = clock or (lambda: datetime.now(N.JST))

    date = a.date or clock().strftime('%Y%m%d')
    until = parse_until(date, a.until)
    offsets = sorted({int(x) for x in a.offsets.split(',') if x.strip()}, reverse=True)
    KC.DATA_DIR = Path(a.out_dir)
    path = Path(a.out_dir) / date[:4] / 'odds_snap' / f'snap_{date}.csv'
    done = set()  # (WINTICKET 形式の race_id, snap名)
    if path.exists():  # 途中から再開した場合は、記録済みのものを飛ばす
        import csv
        with open(path, newline='', encoding='utf-8-sig') as f:
            done = {(row['race_id'], row['snap']) for row in csv.DictReader(f)}
    races, listed_at, flushed_at = [], None, clock()
    stats = dict(snaps=0, failed=0)
    alerter = make_alerter(a) if a.alert else None
    site = make_site(a, date, alerter) if a.site else None
    site_flushed = [clock()]

    def flush():
        nonlocal flushed_at
        if a.flush_cmd:
            subprocess.run(a.flush_cmd, shell=True, check=False)
        flushed_at = clock()

    def done_nk():
        return {(nk, s) for nk, _ in races for s in (f'C-{o}' for o in offsets) if (N.to_wt_race_id(nk), s) in done}

    try:
        while clock() < until:
            now = clock()
            if listed_at is None or now - listed_at >= timedelta(minutes=LIST_REFRESH_MIN):
                try:
                    races = N.list_today(date)
                    listed_at = now
                    print(f'[{now:%H:%M}] レース一覧 {len(races)}件')
                    if site is not None:
                        site.set_races(races)
                except (N.HTTPError, N.URLError, ValueError) as e:
                    print(f'[{now:%H:%M}] レース一覧の取得に失敗 {type(e).__name__}')
                    listed_at = now - timedelta(minutes=LIST_REFRESH_MIN - 5)
            for nk, snap, st, dl in due_jobs(races, date, offsets, done_nk(), clock()):
                if clock() >= dl:
                    continue
                t = clock()
                try:
                    rows = snapshot_rows(fetch_snapshot(nk), nk, snap, t, st, dl)
                except (N.HTTPError, N.URLError, ValueError, KeyError, TypeError) as e:
                    stats['failed'] += 1
                    print(f'  ! {nk} {snap}: 失敗 {type(e).__name__}')
                    rows = None
                done.add((N.to_wt_race_id(nk), snap))
                if rows:
                    KC.append_csv(path, rows, ['race_id', 'snap', 'bet_type', 'combination'])
                    stats['snaps'] += 1
                    n3 = sum(1 for r in rows if r['bet_type'] == '3連単' and r['odds'] != '')
                    print(f'  {nk} {snap}（締切 {dl:%H:%M}）: 3連単 {n3}組')
                    if alerter is not None:
                        try:
                            alerter.on_snapshot(nk, snap, st, rows, clock(), deadline=dl)
                        except N.Blocked:
                            raise
                        except Exception as e:  # noqa: BLE001  判定の失敗で記録を止めない
                            print(f'  ! 判定に失敗 {type(e).__name__}')
                    if site is not None:
                        try:
                            site.on_snapshot(nk, snap, st, rows, clock(), deadline=dl)
                        except N.Blocked:
                            raise
                        except Exception as e:  # noqa: BLE001
                            print(f'  ! 期待値ボードの計算に失敗 {type(e).__name__}')
            if races and remaining(races, date, offsets, done_nk(), clock()) == 0:
                print('今日の記録すべきレースは終わりました')
                break
            if a.flush_min and clock() - flushed_at >= timedelta(minutes=a.flush_min):
                flush()
            if site is not None and clock() - site_flushed[0] >= timedelta(seconds=60):
                site.flush()
                site_flushed[0] = clock()
            sleep(20)
    except N.Blocked as e:
        print(f'!! サーバーから拒否されたため中断しました（回避はしません）: {e}')
    finally:
        flush()
        print(f'記録 {stats["snaps"]}件 / 失敗 {stats["failed"]}件')
        if alerter is not None:
            print(alerter.summary())
        if site is not None:
            site.flush()
            print(site.summary())


if __name__ == '__main__':
    main()
