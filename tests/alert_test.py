#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netkeirin_alert.py のオフラインテスト（架空レース・通信なし・メールは送らない）:  python tests/alert_test.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import keirin_collector as C  # noqa: E402
import keirin_engine as E  # noqa: E402
import netkeirin_alert as NA  # noqa: E402
import now_test as NT  # noqa: E402  架空レース（7車・2車複 1=2 が割安）

JST = timezone(timedelta(hours=9))
NK = '209912312305'  # 2099/12/31 取手(23) 5R


def check(c, msg):
    if not c:
        raise SystemExit(f'NG   {msg}')
    print(f'OK   {msg}')


def main():
    _, _, full, _ = NT.build_states()
    cards = C.parse_racecard(full)
    odds = C.parse_odds(full)
    mails, card_calls = [], []

    def cards_fn(nk):
        card_calls.append(nk)
        return cards
    al = NA.Alerter(E.load_model(), sender=lambda s, b: mails.append((s, b)), cards_fn=cards_fn)
    check({s['id'] for s in al.strategies} == {'Q110', 'Q120M', 'W110'}, '通知の対象は strategies.json の alert の買い方')
    start = datetime(2099, 12, 31, 15, 0, tzinfo=JST)
    got = al.on_snapshot(NK, 'T-12', start, odds, start - timedelta(minutes=12))
    check(got == [] and mails == [], '通知の時点（T-6/T-4）以外では判定しない')
    got = al.on_snapshot(NK, 'T-6', start, odds, start - timedelta(minutes=6))
    keys = {(t['type'], t['key']) for _, t in got}
    check(('2車複', '1-2') in keys, f'割安な 2車複 1=2 を通知 {sorted(keys)}')
    check(len(mails) == 1, '1レースの該当はメール1通にまとめる')
    subj, body = mails[0]
    check('取手5R' in subj and '2車複 1=2' in subj and '15:00' in subj, f'件名にレース・買い目・発走時刻 {subj}')
    check('倍以上なら買い' in body and 'あと約6分' in body, '本文に買い基準オッズと残り時間')
    al.on_snapshot(NK, 'T-4', start, odds, start - timedelta(minutes=4))
    check(len(mails) == 1, '同じ買い目は2回知らせない')
    check(card_calls == [NK], '出走表はレースごとに1回だけ取得')
    check(NA.buy_line(0.05, 1.10) == 22.0 and NA.buy_line(0.034, 1.10) == 32.4, '買い基準オッズ = 1.10 ÷ 確率（0.1倍切り上げ）')

    # メール未設定（sender=None）でも判定は動き、送らない
    al2 = NA.Alerter(E.load_model(), sender=None, cards_fn=lambda nk: cards)
    got = al2.on_snapshot(NK, 'T-6', start, odds, start - timedelta(minutes=6))
    check(got and al2.stats['mails'] == 0 and '未設定' in al2.summary(), 'メール未設定なら判定のみ')
    # 上限
    al3 = NA.Alerter(E.load_model(), sender=lambda s, b: mails.append((s, b)), cards_fn=lambda nk: cards, max_per_day=0)
    al3.on_snapshot(NK, 'T-6', start, odds, start - timedelta(minutes=6))
    check(al3.stats['skipped_cap'] == 1 and al3.stats['mails'] == 0, '1日の上限を超えたら送らない')
    # 環境変数が無ければ送信関数を作らない
    for k in ('ALERT_GMAIL_USER', 'ALERT_GMAIL_APP_PASSWORD'):
        os.environ.pop(k, None)
    check(NA.gmail_sender() is None, 'Gmail の設定が無ければ送信しない')
    print('\n全テスト成功')


if __name__ == '__main__':
    main()
