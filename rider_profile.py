#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
選手の生年月日（占いの表示・検証用）。netkeirin の選手ページから1人1回だけ取得して、データ用リポジトリに保存する（私的利用のみ）。

  * 保存先: データ用リポジトリの data/riders/birthdays.json  {選手ID: {"birth": "1983-01-02" or null, "at": 取得日, "tries": 回数}}
  * 記録ジョブの空き時間（最新の更新をしていない時）に、今日の出走選手 → 過去に出走した選手の順で少しずつ取る
    （1日の上限あり・1リクエストごとの間隔は netkeirin_collector の既定どおり）
  * 生年月日が読めなかったページは、解析の確認用に最初の数件だけ data/riders/_samples/ に保存する（非公開のデータ用リポジトリ）
"""
import json
import re
from datetime import datetime
from pathlib import Path

PROFILE_URL = '/db/profile/'
MAX_TRIES = 3
SAMPLES = 3

_DATE = r'((?:19|20)\d{2})\s*[/年.\-]\s*(\d{1,2})\s*[/月.\-]\s*(\d{1,2})'


def _plain(html: str) -> str:
    t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S | re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = t.replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', t)


def parse_birth(html: str):
    """選手ページ → 'YYYY-MM-DD'（見つからなければ None）。「生年月日」の近くにある日付を読む"""
    if not html:
        return None
    t = _plain(html)
    for m in re.finditer(r'生年月日|誕生日|生まれ', t):
        d = re.search(_DATE, t[m.end():m.end() + 80])
        if d:
            y, mo, da = map(int, d.groups())
            try:
                return datetime(y, mo, da).strftime('%Y-%m-%d')
            except ValueError:
                continue
    return None


class Birthdays:
    def __init__(self, data_dir, fetch=None, per_day=400):
        self.dir = Path(data_dir) / 'riders'
        self.path = self.dir / 'birthdays.json'
        self.fetch = fetch
        self.per_day = per_day
        try:
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.data = {}
        self.count = {}  # 日付 → 今日取得した件数
        self.dirty = False
        self.stats = dict(fetched=0, found=0)
        self.off = False  # 最初の数十件で1人も読めなければ、ページの形が想定と違うので止める（無駄に取りに行かない）

    def get(self, pid):
        v = self.data.get(str(pid).zfill(6))
        return v.get('birth') if v else None

    def missing(self, pids):
        out = []
        for p in pids:
            p = str(p).zfill(6) if str(p).strip() else ''
            if not p or p == '000000' or p in out:
                continue
            v = self.data.get(p)
            if v and (v.get('birth') or v.get('tries', 0) >= MAX_TRIES):
                continue
            out.append(p)
        return out

    def fetch_some(self, pids, n, now):
        """足りない選手を最大 n 人取得。取得した人数を返す（サーバーから拒否されたら例外をそのまま上げる）"""
        if self.fetch is None or self.off:
            return 0
        day = now.strftime('%Y%m%d')
        done = 0
        for p in self.missing(pids):
            if done >= n or self.count.get(day, 0) >= self.per_day:
                break
            self.count[day] = self.count.get(day, 0) + 1
            done += 1
            v = self.data.setdefault(p, {})
            v['tries'] = v.get('tries', 0) + 1
            v['at'] = now.strftime('%Y-%m-%d')
            try:
                html = self.fetch(p)
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ == 'Blocked':
                    raise
                html = ''
            b = parse_birth(html)
            self.stats['fetched'] += 1
            if b:
                v['birth'] = b
                self.stats['found'] += 1
            elif html:
                self._sample(p, html)
            self.dirty = True
            if self.stats['fetched'] >= 20 and self.stats['found'] == 0:
                self.off = True
                print('  ! 選手ページから生年月日が読めません（data/riders/_samples/ を確認）。この実行では取得を止めます', flush=True)
                break
        return done

    def _sample(self, pid, html):
        d = self.dir / '_samples'
        try:
            if d.exists() and len(list(d.glob('*.html'))) >= SAMPLES:
                return
            d.mkdir(parents=True, exist_ok=True)
            (d / f'{pid}.html').write_text(html, encoding='utf-8')
        except OSError:
            pass

    def save(self):
        if not self.dirty:
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=0, sort_keys=True), encoding='utf-8')
        self.dirty = False
        return True

    def summary(self):
        n = sum(1 for v in self.data.values() if v.get('birth'))
        return f'選手の生年月日: 取得 {self.stats["fetched"]}人（読めた {self.stats["found"]}人）・保存済み {n}人'


def netkeirin_fetch(pid):
    import netkeirin_collector as N
    return N.fetch(N.BASE_URL + PROFILE_URL, {'id': pid})


def known_players(data_dir):
    """過去に出走した選手ID（新しい順に近い並び）"""
    import csv
    out = []
    for p in sorted(Path(data_dir).glob('*/racecards.csv'), reverse=True) + [Path(data_dir) / 'racecards.csv']:
        try:
            with open(p, newline='', encoding='utf-8-sig') as f:
                for r in csv.DictReader(f):
                    out.append(r.get('player_id', ''))
        except OSError:
            continue
    seen, res = set(), []
    for p in reversed(out):  # ファイルの後ろ（新しいレース）を優先
        if p and p not in seen:
            seen.add(p)
            res.append(p)
    return res
