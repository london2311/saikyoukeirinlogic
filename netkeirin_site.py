#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netkeirin_site.py — 個人閲覧用の「期待値ボード」のデータを作る（私的利用のみ・暗号化して置く）

netkeirin_snapshot.py から呼ばれる。発走15分前・10分前・5分前（と4分前）に記録したオッズで、
全券種・全組の「当たる確率（v105 融合）・今のオッズ・期待値（EV）・買い基準オッズ」を計算し、
レースごとの JSON を **AES-256-GCM で暗号化**して、このリポジトリの site-data ブランチに置く。
閲覧ページ（live/index.html）は、入力したパスワードでブラウザの中だけで復号して表示する。

  * パスワード: 環境変数 SITE_PASSWORD（GitHub の Secrets）。未設定なら何もしない
  * 鍵: PBKDF2-HMAC-SHA256（20万回）でパスワードから作る。塩は日ごとに作り直す（meta.json に平文で置く）
  * 各ファイル: 先頭12バイトが IV、残りが「zlib 圧縮した JSON」の暗号文＋認証タグ
  * ブランチは毎回1コミットだけに作り直して強制 push する（履歴をためない）
  * ログには件数だけを出す
"""
import base64
import json
import os
import secrets
import subprocess
import zlib
from datetime import datetime
from pathlib import Path

import keirin_collector as KC

ITER = 200_000
SITE_SNAPS = ('T-15', 'T-10', 'T-5', 'T-4')
TYPES = ['2車複', 'ワイド', '3連複', '2車単', '3連単']
VALIDATED = {'2車複', 'ワイド'}  # 過去の検証で EV 買いがプラスだった券種（他は参考）


def derive_key(password: str, salt: bytes) -> bytes:
    import hashlib
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITER, dklen=32)


def encrypt(key: bytes, obj) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = secrets.token_bytes(12)
    data = zlib.compress(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), 9)
    return iv + AESGCM(key).encrypt(iv, data, None)


def decrypt(key: bytes, blob: bytes):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return json.loads(zlib.decompress(AESGCM(key).decrypt(blob[:12], blob[12:], None)).decode('utf-8'))


def buy_line(p, ev_min=1.10):
    import math
    return math.ceil(ev_min / p * 10 - 1e-9) / 10 if p > 0 else None


class SiteBuilder:
    def __init__(self, model, password, out_dir, date, cards_cache=None, cards_fn=None, push_fn=None, strategies=None):
        self.model = model
        self.out = Path(out_dir)
        self.date = date
        self.cards = cards_cache if cards_cache is not None else {}
        self.cards_fn = cards_fn
        self.push_fn = push_fn
        self.strategies = strategies or []
        self.out.mkdir(parents=True, exist_ok=True)
        meta_p = self.out / 'meta.json'
        meta = None
        if meta_p.exists():
            try:
                meta = json.loads(meta_p.read_text(encoding='utf-8'))
            except ValueError:
                meta = None
        if not meta or meta.get('date') != date:  # 日が変わったら作り直す
            for p in self.out.glob('*'):
                if p.is_file():
                    p.unlink()
            meta = dict(date=date, salt=base64.b64encode(secrets.token_bytes(16)).decode(), iter=ITER, v=1)
            meta_p.write_text(json.dumps(meta), encoding='utf-8')
        self.key = derive_key(password, base64.b64decode(meta['salt']))
        self.index = self._load('index.enc') or dict(date=date, races={})
        self.dirty = False
        self.stats = dict(updates=0, pushes=0, push_failed=0)

    def _load(self, name):
        p = self.out / name
        if not p.exists():
            return None
        try:
            return decrypt(self.key, p.read_bytes())
        except Exception:  # noqa: BLE001  鍵が変わった等
            return None

    def _save(self, name, obj):
        (self.out / name).write_bytes(encrypt(self.key, obj))

    def set_races(self, races):
        """今日のレース一覧 [(nk, 'HH:MM')]"""
        changed = False
        for nk, hhmm in races:
            wt = KC_wt(nk)
            r = self.index['races'].get(wt)
            if r is None:
                self.index['races'][wt] = dict(venue=KC.VENUE_NAMES.get(nk[8:10], nk[8:10]), rn=int(nk[10:12]), start=hhmm,
                                               snaps=[], best=None)
                changed = True
            elif hhmm and r.get('start') != hhmm:
                r['start'] = hhmm
                changed = True
        if changed:
            self.dirty = True

    def _cards(self, nk):
        if nk not in self.cards:
            try:
                self.cards[nk] = self.cards_fn(nk) if self.cards_fn else None
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ == 'Blocked':
                    raise
                self.cards[nk] = None
        return self.cards[nk]

    def on_snapshot(self, nk, snap, start, odds_rows, now):
        import keirin_engine as E
        if snap not in SITE_SNAPS:
            return None
        cards = self._cards(nk)
        if not cards:
            return None
        venue = KC.VENUE_NAMES.get(nk[8:10], '')
        res = E.analyze(self.model, cards, odds_rows, venue, {})
        if not res.get('ok'):
            return None
        wt = KC_wt(nk)
        types = {}
        for t in res.get('tickets', []):
            types.setdefault(t['type'], []).append(
                [t['key'], round(t['prob'], 5), t['odds'], round(t['ev'], 3), buy_line(t['prob'])])
        for bt in types:
            types[bt].sort(key=lambda x: -x[3])
        hits = {}
        for bt, rows in (res.get('hit_picks') or {}).items():
            hits[bt] = [[h['key'], round(h['prob'], 4), h['odds']] for h in rows]
        strat = []
        if res.get('market_ok') and self.strategies:
            import snapshot_ledger as L
            for s in self.strategies:
                for t in L.select(s, res['tickets']):
                    strat.append([s['id'], s['label'], t['type'], t['key'], t['odds'], round(t['ev'], 3), buy_line(t['prob'], s.get('min', 1.10))])
        riders = []
        for r in sorted(cards, key=lambda x: int(x['car_number'])):
            if str(r.get('absent', 0)) in ('1', 'True', 'true'):
                continue
            riders.append([int(r['car_number']), r.get('player_name', ''), r.get('prefecture', ''), r.get('race_point', ''),
                           r.get('style', ''), r.get('line', '') or r.get('line_order', '')])
        win = {x['num']: round(x['win'], 4) for x in res.get('riders', [])}
        race = self._load(f'{wt}.enc') or dict(id=wt, venue=venue, rn=int(nk[10:12]), start=f'{start:%H:%M}', snaps={})
        race['riders'] = riders
        race['snaps'][snap] = dict(at=f'{now:%H:%M:%S}', market_ok=bool(res.get('market_ok')), win=win, types=types,
                                   hits=hits, strat=strat)
        self._save(f'{wt}.enc', race)
        ent = self.index['races'].setdefault(wt, dict(venue=venue, rn=int(nk[10:12]), start=f'{start:%H:%M}', snaps=[], best=None))
        if snap not in ent['snaps']:
            ent['snaps'].append(snap)
        best = []
        for bt in TYPES:
            rows = [x for x in types.get(bt, []) if x[1] >= 0.005 and x[2] <= 300]
            if rows:
                best.append([bt, rows[0][0], rows[0][2], rows[0][3]])
        ent['best'] = dict(snap=snap, rows=best, strat=len(strat))
        self.index['updated'] = f'{now:%Y-%m-%d %H:%M:%S}'
        self.dirty = True
        self.stats['updates'] += 1
        return race

    def flush(self, force=False):
        """index を書き出して push（変更がある時だけ）"""
        if not (self.dirty or force):
            return False
        self._save('index.enc', self.index)
        self.dirty = False
        if self.push_fn:
            try:
                self.push_fn(self.out)
                self.stats['pushes'] += 1
            except Exception as e:  # noqa: BLE001
                self.stats['push_failed'] += 1
                print(f'  ! 期待値ボードの更新に失敗 {type(e).__name__}')
        return True

    def summary(self):
        s = self.stats
        return f'期待値ボード: 更新 {s["updates"]}件 / 公開 {s["pushes"]}回（失敗 {s["push_failed"]}）'


def KC_wt(nk):
    """netkeirin 形式 YYYYMMDDVVRR → WINTICKET 形式 RRVVYYYYMMDD"""
    return nk[10:12] + nk[8:10] + nk[:8]


def git_push_fn(branch='site-data', remote='origin'):
    """out_dir の中身だけを1コミットにして branch に強制 push する関数を返す（GitHub Actions 用）"""
    def push(out_dir):
        d = str(out_dir)
        run = lambda *a: subprocess.run(['git', *a], cwd=d, check=True, capture_output=True, text=True)
        if not os.path.isdir(os.path.join(d, '.git')):
            run('init', '-q')
            url = subprocess.run(['git', 'remote', 'get-url', remote], check=True, capture_output=True, text=True).stdout.strip()
            run('remote', 'add', remote, url)
            hdr = subprocess.run(['git', 'config', '--get-all', 'http.https://github.com/.extraheader'],
                                 capture_output=True, text=True).stdout.strip()
            if hdr:
                run('config', 'http.https://github.com/.extraheader', hdr)
            run('config', 'user.name', 'github-actions[bot]')
            run('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
        run('checkout', '-q', '--orphan', f'tmp{datetime.now():%H%M%S%f}')
        run('add', '-A')
        run('commit', '-q', '-m', 'site data', '--allow-empty')
        run('push', '-q', '-f', remote, f'HEAD:refs/heads/{branch}')
    return push


def restore_from_branch(out_dir, branch='site-data', remote='origin'):
    """前の時間帯のデータを site-data ブランチから復元する（同じ日の続き）"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(['git', 'fetch', '-q', '--depth=1', remote, branch], capture_output=True, text=True)
    if r.returncode != 0:
        return False
    files = subprocess.run(['git', 'ls-tree', '--name-only', 'FETCH_HEAD'], capture_output=True, text=True).stdout.split()
    for name in files:
        blob = subprocess.run(['git', 'show', f'FETCH_HEAD:{name}'], capture_output=True).stdout
        (out / name).write_bytes(blob)
    return True
