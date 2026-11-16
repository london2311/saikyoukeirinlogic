#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netkeirin_alert.py — 発走前のオッズで事前登録した買い方を判定し、該当があれば自分宛てにメールで知らせる（私的利用のみ）

netkeirin_snapshot.py から呼ばれる（--alert-snaps で指定した時点、既定は 締切5分前・2分前）。
  * 判定する買い方: analysis/strategies.json で "alert": true のもの
  * 出走表は各レース1回だけ取得（1リクエスト）。オッズは記録用に取ったものをそのまま使う
  * 同じレース・同じ買い目は1回だけ知らせる。1日の送信は --alert-max 通まで
  * メールの送信設定（GitHub の Secrets）: ALERT_GMAIL_USER / ALERT_GMAIL_APP_PASSWORD（Gmail のアプリパスワード）
    送り先 ALERT_TO（省略時は ALERT_GMAIL_USER）。未設定なら送らない（判定は行い、件数だけログに出す）
  * ログ（公開リポジトリの Actions ログ）には件数だけを出し、レースや買い目は出さない
"""
import json
import math
import os
import smtplib
import sys
from email.mime.text import MIMEText
from email.utils import formatdate

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'analysis'))
import keirin_collector as KC  # noqa: E402

STRATEGIES = os.path.join(HERE, 'analysis', 'strategies.json')


def gmail_sender():
    """環境変数に Gmail の設定があれば、件名・本文を送る関数を返す。無ければ None"""
    user = os.environ.get('ALERT_GMAIL_USER', '').strip()
    pw = os.environ.get('ALERT_GMAIL_APP_PASSWORD', '').replace(' ', '').strip()
    to = os.environ.get('ALERT_TO', '').strip() or user
    if not user or not pw:
        return None

    def send(subject, body):
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = user
        msg['To'] = to
        msg['Date'] = formatdate(localtime=True)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30) as s:
            s.login(user, pw)
            s.sendmail(user, [to], msg.as_string())
    return send


def buy_line(p, ev_min):
    """EV が基準以上になる最低オッズ（0.1倍単位で切り上げ）"""
    return math.ceil(ev_min / p * 10 - 1e-9) / 10 if p > 0 else float('inf')


class Alerter:
    def __init__(self, model, sender=None, cards_fn=None, snaps=('C-5', 'C-2'), max_per_day=60, strategies_path=STRATEGIES):
        import snapshot_ledger as L
        self.L = L
        self.model = model
        self.sender = sender
        self.cards_fn = cards_fn or self._fetch_cards
        self.snaps = set(snaps)
        self.max = max_per_day
        with open(strategies_path, encoding='utf-8') as f:
            self.strategies = [s for s in json.load(f)['strategies'] if s.get('alert')]
        self.cards = {}
        self.sent_keys = set()
        self.stats = dict(judged=0, races_hit=0, mails=0, mail_failed=0, skipped_cap=0)

    @staticmethod
    def _fetch_cards(nk):
        import netkeirin_collector as N
        return N.parse_racecard(N.fetch(f'{N.BASE_URL}/race/entry/', {'race_id': nk}), nk)

    def on_snapshot(self, nk, snap, start, odds_rows, now, deadline=None):
        """記録した時点のオッズで判定し、新しい該当があればメールを送る。送った買い目の一覧を返す"""
        import keirin_engine as E
        if snap not in self.snaps or not self.strategies:
            return []
        if nk not in self.cards:
            try:
                self.cards[nk] = self.cards_fn(nk)
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ == 'Blocked':
                    raise
                self.cards[nk] = None
        cards = self.cards[nk]
        if not cards:
            return []
        venue = KC.VENUE_NAMES.get(nk[8:10], '')
        res = E.analyze(self.model, cards, odds_rows, venue, {})
        self.stats['judged'] += 1
        if not res.get('ok') or not res.get('market_ok'):
            return []
        new = []
        for s in self.strategies:
            for t in self.L.select(s, res['tickets']):
                k = (nk, t['type'], t['key'])
                if k in self.sent_keys:
                    continue
                self.sent_keys.add(k)
                new.append((s, t))
        if not new:
            return []
        self.stats['races_hit'] += 1
        if self.stats['mails'] >= self.max:
            self.stats['skipped_cap'] += 1
            return new
        from datetime import timedelta
        deadline = deadline or (start - timedelta(minutes=5))
        mins = max(0, int((deadline - now).total_seconds() // 60))
        title = f'{venue}{int(nk[10:12])}R'
        subject = f'【競輪通知】{title}（締切 {deadline:%H:%M}）' + ' / '.join(
            f'{t["type"]} {t["key"].replace("-", "=")}' for _, t in new[:3]) + (' ほか' if len(new) > 3 else '')
        lines = [f'{title}  締切 {deadline:%H:%M}（締切まであと約{mins}分・発走 {start:%H:%M}・{snap} のオッズで判定）', '']
        for s, t in new:
            line = buy_line(t['prob'], s.get('min', 1.10))
            band = ''
            if s.get('odds_min') or s.get('odds_max', 1e9) < 1e8:
                band = f'（対象 {s.get("odds_min", 1):g}〜{s.get("odds_max", 1e9):g}倍）'
            lines.append(f'■ {t["type"]} {t["key"].replace("-", "=")}  今 {t["odds"]:.1f}倍 → {line:.1f}倍以上なら買い{band}')
            lines.append(f'   当たる確率 {t["prob"] * 100:.1f}% / EV {t["ev"]:.2f}  [{s["id"]}: {s["label"]}]')
        lines += ['', '※ 検証中の買い方です（締切前オッズでの成績は台帳で集計中）。1点100円。',
                  '※ 買う直前に倍率を確認し、基準未満なら見送り。ワイドは最低倍率で判断。',
                  '※ このメールは自分用の通知です（netkeirin の情報を含むため転送・公開しないでください）。']
        body = '\n'.join(lines)
        if self.sender is None:
            return new
        try:
            self.sender(subject, body)
            self.stats['mails'] += 1
        except Exception as e:  # noqa: BLE001
            self.stats['mail_failed'] += 1
            print(f'  ! 通知メールの送信に失敗 {type(e).__name__}')
        return new

    def summary(self):
        s = self.stats
        return (f'通知: 判定 {s["judged"]}回 / 該当レース {s["races_hit"]} / 送信 {s["mails"]}通'
                f'（失敗 {s["mail_failed"]}、上限超え {s["skipped_cap"]}）' + ('' if self.sender else ' ※メール未設定のため送信なし'))
