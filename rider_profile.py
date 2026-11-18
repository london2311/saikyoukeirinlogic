#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
選手の生年月日（占いの表示・検証用）。netkeirin の選手ページから1人1回だけ取得して、データ用リポジトリに保存する（私的利用のみ）。

  * 保存先: データ用リポジトリの data/riders/birthdays.csv（1行 = 1選手: 選手ID,生年月日,取得日,回数）
    記録ジョブと一括取得が同時に書いても git で衝突しないよう、1行1選手の CSV にして、データ用リポジトリの
    .gitattributes で merge=union（両方の行を残す）にしている。同じ選手の行が複数あれば、読む時に生年月日のある行を優先する
  * 記録ジョブの空き時間（最新の更新をしていない時）に、今日の出走選手 → 過去に出走した選手の順で少しずつ取る
    （1日の上限あり・1リクエストごとの間隔は netkeirin_collector の既定どおり）
  * 生年月日が読めなかったページは、解析の確認用に最初の数件だけ data/riders/_samples/ に保存する（非公開のデータ用リポジトリ）
"""
import csv
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
    # netkeirin の選手ページ: 見出しの下に「1985/05/06(41歳)」（ラベルなし）
    for d in re.finditer(_DATE + r'\s*[（(]\s*\d{1,3}\s*歳', t):
        y, mo, da = map(int, d.groups())
        try:
            return datetime(y, mo, da).strftime('%Y-%m-%d')
        except ValueError:
            continue
    for m in re.finditer(r'生年月日|誕生日|生まれ', t):
        d = re.search(_DATE, t[m.end():m.end() + 80])
        if d:
            y, mo, da = map(int, d.groups())
            try:
                return datetime(y, mo, da).strftime('%Y-%m-%d')
            except ValueError:
                continue
    return None


FIELDS = ['player_id', 'birth', 'at', 'tries']


def _better(a, b):
    """同じ選手の2つの記録 → 生年月日のある方（どちらも無ければ回数の多い方）"""
    if not a:
        return dict(b)
    if not b:
        return dict(a)
    if bool(a.get('birth')) != bool(b.get('birth')):
        return dict(a if a.get('birth') else b)
    return dict(a if int(a.get('tries') or 0) >= int(b.get('tries') or 0) else b)


def load(data_dir):
    """{選手ID: {birth, at, tries}}（CSV。古い JSON があれば合わせる）"""
    d = Path(data_dir) / 'riders'
    out = {}
    try:
        for k, v in json.loads((d / 'birthdays.json').read_text(encoding='utf-8')).items():
            out[k] = _better(out.get(k), v)
    except (OSError, ValueError):
        pass
    try:
        with open(d / 'birthdays.csv', newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                k = str(r.get('player_id') or '').strip()
                if not k or k == 'player_id':  # 合わせた時に見出し行が混ざっても飛ばす
                    continue
                try:
                    tries = int(r.get('tries') or 0)
                except ValueError:
                    tries = 0
                out[k.zfill(6)] = _better(out.get(k.zfill(6)), dict(birth=r.get('birth') or None, at=r.get('at', ''), tries=tries))
    except OSError:
        pass
    return out


class Birthdays:
    def __init__(self, data_dir, fetch=None, per_day=400):
        self.dir = Path(data_dir) / 'riders'
        self.path = self.dir / 'birthdays.csv'
        self.fetch = fetch
        self.per_day = per_day
        self.data = load(data_dir)
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
        # 書く直前にファイルを読み直して合わせる（別の実行が先に書いた分を消さない）
        cur = load(self.dir.parent)
        for k, v in self.data.items():
            cur[k] = _better(cur.get(k), v)
        self.data = cur
        tmp = self.path.with_suffix('.tmp')
        with open(tmp, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(FIELDS)
            for k in sorted(self.data):
                v = self.data[k]
                w.writerow([k, v.get('birth') or '', v.get('at', ''), v.get('tries', 0)])
        tmp.replace(self.path)
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


def main(argv=None):
    """過去に出走した選手の生年月日をまとめて取得（1回だけの実行用）。ログには件数だけ出す"""
    import argparse
    from datetime import timedelta, timezone
    import netkeirin_collector as N
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True, help='データ用リポジトリの data フォルダ')
    ap.add_argument('--max', type=int, default=3000)
    ap.add_argument('--interval', type=float, default=4.0, help='1リクエストごとの間隔（秒。記録ジョブと同時に動くので既定より長め）')
    a = ap.parse_args(argv)
    jst = timezone(timedelta(hours=9))
    bd = Birthdays(a.data, fetch=lambda p: N.fetch(N.BASE_URL + PROFILE_URL, {'id': p}, interval=max(a.interval, N.REQUEST_INTERVAL)),
                   per_day=a.max)
    pids = bd.missing(known_players(a.data))
    print(f'生年月日が未取得の選手 {len(pids)}人（上限 {a.max}人）', flush=True)
    try:
        while not bd.off:
            n = bd.fetch_some(pids, 100, datetime.now(jst))
            bd.save()
            print(f'  {bd.summary()}', flush=True)
            if n == 0:
                break
    except N.Blocked:
        print('!! サーバーから拒否されたため中断しました（回避はしません）', flush=True)
    finally:
        bd.save()
        print(bd.summary(), flush=True)


if __name__ == '__main__':
    main()
