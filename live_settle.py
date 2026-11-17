#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
［賭けた］記録の即時精算（記録ジョブの中で動く）

  * 期待値ボードで記録した賭け（データ用リポジトリの data/bets/bets_YYYYMM.json）のうち、今日のレースで
    まだ精算されていないものを数分ごとに確認する（作業中のファイルを汚さないよう git fetch → git show で読む）
  * 発走の数分後から、そのレースの結果ページを取得して払戻が出ていれば精算し、data/bets/settled_live.json に書く
    （的中・外れ・欠車を含む買い目は返還。確定オッズも付ける）
  * 毎朝・夜の台帳ジョブの settled.json が正式な精算。ページは両方を合わせて表示する（settled.json を優先）
  * 取得するのは「記録がある」レースの結果だけ。払戻が出るまで2分おき（発走から時間がたったレースは10分おき）。
    記録ジョブが途中で止まっていた間に終わったレースも、直近2日分は精算する
"""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import netkeirin_collector as N

JST = timezone(timedelta(hours=9))
CHECK_BETS_MIN = 3        # 記録を読み直す間隔
FIRST_AFTER_MIN = 6       # 発走から何分後に結果を見に行くか
RETRY_MIN = 2             # 払戻が出ていなければ、この間隔で取り直す
SLOW_AFTER_MIN = 60       # 発走からこれ以上たったレースは、取り直しの間隔を広げる
SLOW_RETRY_MIN = 10


def norm(bt, combo):
    parts = [int(x) for x in str(combo).replace('=', '-').split('-') if x.strip().isdigit()]
    if bt in ('3連複', '2車複', 'ワイド', '枠複'):
        parts = sorted(parts)
    return '-'.join(map(str, parts))


def settle_bets_for_race(bets, result, now):
    """result = netkeirin_collector.parse_result の戻り値 → {賭けID: 精算}（払戻がまだ無ければ空）"""
    if not result.get('orders') or not result.get('payoffs'):
        return {}
    pay = {(p['bet_type'], norm(p['bet_type'], p['combination'])): float(p['payoff']) for p in result['payoffs'] if p.get('payoff') not in ('', None)}
    absent = {int(o['car_number']) for o in result['orders'] if o.get('accident') == '欠車' and str(o.get('car_number', '')).isdigit()}
    stamp = now.strftime('%Y-%m-%d %H:%M')
    out = {}
    for b in bets:
        key = norm(b['bt'], b['key'])
        stake = float(b.get('stake') or 0)
        if {int(x) for x in key.split('-')} & absent:
            out[b['id']] = dict(result='void', payout=stake, payoff=None, settled_at=stamp, odds_final=None, live=True)
            continue
        v = pay.get((b['bt'], key))
        out[b['id']] = dict(result='hit' if v else 'miss', payout=round(stake * v / 100.0) if v else 0, payoff=v,
                            settled_at=stamp, odds_final=round(v / 100.0, 1) if v else None, live=True)
    return out


class LiveSettler:
    def __init__(self, repo_dir, fetch_result=None, read_remote=None):
        self.repo = Path(repo_dir)
        self.fetch_result = fetch_result or (lambda nk: N.parse_result(N.fetch(f'{N.BASE_URL}/race/result/', {'race_id': nk}), nk))
        self.read_remote = read_remote or self._git_read
        self.bets, self.settled = [], {}
        self.read_at = None
        self.tried = {}  # race_id → 最後に結果を見に行った時刻
        self.known = {}  # 日付 → その日のレース一覧（翌日に切り替わった後も、前日の最終レースを精算するため）
        self.path = self.repo / 'data' / 'bets' / 'settled_live.json'
        try:
            self.live = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.live = {}
        self.stats = dict(settled=0, checked=0)

    def _git_read(self, rel_paths):
        """リモートの最新から読む（作業中のファイルは変えない）。{相対パス: 中身の文字列 or None}"""
        subprocess.run(['git', '-C', str(self.repo), 'fetch', '-q', 'origin'], capture_output=True)
        out = {}
        for rel in rel_paths:
            r = subprocess.run(['git', '-C', str(self.repo), 'show', f'FETCH_HEAD:{rel}'], capture_output=True, text=True)
            out[rel] = r.stdout if r.returncode == 0 else None
        return out

    def refresh(self, dates, now):
        if self.read_at and now - self.read_at < timedelta(minutes=CHECK_BETS_MIN):
            return
        self.read_at = now
        months = sorted({d[:6] for d in dates})
        got = self.read_remote([f'data/bets/bets_{ym}.json' for ym in months] + ['data/bets/settled.json'])
        self.bets = []
        for ym in months:
            try:
                self.bets += [b for b in json.loads(got.get(f'data/bets/bets_{ym}.json') or '[]') if not b.get('deleted')]
            except ValueError:
                pass
        try:
            self.settled = json.loads(got.get('data/bets/settled.json') or '{}')
        except ValueError:
            self.settled = {}

    def tick(self, races, date, now):
        """精算できたら True（呼び出し側でコミットする）"""
        self.known[date] = list(races)
        for d in sorted(self.known)[:-2]:  # 覚えておくのは直近2日
            del self.known[d]
        self.refresh(list(self.known), now)
        nk_of = {N.to_wt_race_id(nk): (d, nk, hhmm) for d, rs in self.known.items() for nk, hhmm in rs}
        pending = {}
        for b in self.bets:
            if b.get('id') in self.settled or b.get('id') in self.live or b.get('date') not in self.known:
                continue
            pending.setdefault(b.get('race_id'), []).append(b)
        changed = False
        for rid, bs in pending.items():
            if rid not in nk_of:
                continue
            d, nk, hhmm = nk_of[rid]
            st = N.start_dt(d, hhmm)
            if st is None or now < st + timedelta(minutes=FIRST_AFTER_MIN):
                continue
            last = self.tried.get(rid)
            retry = SLOW_RETRY_MIN if now > st + timedelta(minutes=SLOW_AFTER_MIN) else RETRY_MIN
            if last and now - last < timedelta(minutes=retry):
                continue
            self.tried[rid] = now
            self.stats['checked'] += 1
            try:
                res = self.fetch_result(nk)
            except N.Blocked:
                raise
            except Exception as e:  # noqa: BLE001  取れなければ次の機会に
                print(f'  ! {nk} 結果の取得に失敗 {type(e).__name__}', flush=True)
                continue
            done = settle_bets_for_race(bs, res, now)
            if done:
                self.live.update(done)
                self.stats['settled'] += len(done)
                changed = True
                print(f'  {nk}: 賭けの記録 {len(done)}点を精算', flush=True)
        if changed:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.live, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
        return changed

    def summary(self):
        return f'即時精算: {self.stats["settled"]}点（結果の確認 {self.stats["checked"]}回）'
