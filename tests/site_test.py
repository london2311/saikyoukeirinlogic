#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netkeirin_site.py のオフラインテスト（架空レース・通信なし）:  python tests/site_test.py

暗号化 → 復号（Python）に加えて、閲覧ページと同じ方式（WebCrypto の PBKDF2 + AES-GCM + DecompressionStream）で
Node から復号できることも確かめる（node が無い環境では省略）。
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
sys.path.insert(0, os.path.join(ROOT, 'analysis'))
import keirin_collector as C  # noqa: E402
import keirin_engine as E  # noqa: E402
import netkeirin_site as S  # noqa: E402
import now_test as NT  # noqa: E402

JST = timezone(timedelta(hours=9))
NK = '209912312305'
PW = 'correct horse battery staple'


def check(c, msg):
    if not c:
        raise SystemExit(f'NG   {msg}')
    print(f'OK   {msg}')


def main():
    _, _, full, _ = NT.build_states()
    cards, odds = C.parse_racecard(full), C.parse_odds(full)
    out = tempfile.mkdtemp()
    pushed = []
    strategies = [s for s in json.load(open(os.path.join(ROOT, 'analysis', 'strategies.json'), encoding='utf-8'))['strategies'] if s.get('alert')]
    sb = S.SiteBuilder(E.load_model(), PW, out, '20991231', cards_fn=lambda nk: cards, push_fn=lambda d: pushed.append(d),
                       strategies=strategies)
    sb.set_races([(NK, '15:00'), ('209912312306', '15:30')])
    start = datetime(2099, 12, 31, 15, 0, tzinfo=JST)
    check(sb.on_snapshot(NK, 'T-12', start, odds, start) is None, '期待値ボードの時点（15/10/5/4分前）以外は作らない')
    for snap, mins in (('T-15', 15), ('T-10', 10), ('T-5', 5)):
        sb.on_snapshot(NK, snap, start, odds, start - timedelta(minutes=mins))
    sb.flush()
    check(len(pushed) == 1, '変更があれば1回だけ公開')
    check(not sb.flush(), '変更が無ければ公開しない')

    files = sorted(os.listdir(out))
    check(files == ['052320991231.enc', 'index.enc', 'meta.json'], f'ファイルはレースごと・一覧・鍵の情報だけ {files}')
    raw = open(os.path.join(out, '052320991231.enc'), 'rb').read()
    check(b'2' not in raw[:0] and '取手'.encode() not in raw and b'odds' not in raw, '中身は暗号化されている（平文が含まれない）')
    meta = json.load(open(os.path.join(out, 'meta.json')))
    key = S.derive_key(PW, base64.b64decode(meta['salt']))
    race = S.decrypt(key, raw)
    check(set(race['snaps']) == {'T-15', 'T-10', 'T-5'}, '1レースに3時点を記録')
    s5 = race['snaps']['T-5']
    check(set(s5['types']) == {'3連単', '2車複'}, 'オッズがある券種はすべて載せる（架空レースのオッズは3連単・2車複のみ）')
    check(len(s5['types']['3連単']) == 210 and len(s5['types']['2車複']) == 21, '全組（7車: 3連単210・2車複21）')
    evs = [x[3] for x in s5['types']['2車複']]
    check(evs == sorted(evs, reverse=True), '期待値の高い順')
    top = s5['types']['2車複'][0]
    check(top[0] == '1-2' and top[3] >= 1.1, f'割安な 2車複 1=2 が先頭 {top}')
    check(any(x[0] == 'Q110' and x[3] == '1-2' for x in s5['strat']), '事前登録した買い方の該当（★）を付ける')
    idx = S.decrypt(key, open(os.path.join(out, 'index.enc'), 'rb').read())
    check(set(idx['races']) == {'052320991231', '062320991231'} and idx['races']['052320991231']['snaps'] == ['T-15', 'T-10', 'T-5'],
          '一覧に今日の全レースと記録済みの時点')
    try:
        S.decrypt(S.derive_key('wrong password!!', base64.b64decode(meta['salt'])), raw)
        check(False, '違うパスワードでは読めない')
    except Exception:  # noqa: BLE001
        check(True, '違うパスワードでは読めない')

    # 同じ日の続き（次の時間帯）は前の分を引き継ぎ、日が変われば作り直す
    sb2 = S.SiteBuilder(E.load_model(), PW, out, '20991231', cards_fn=lambda nk: cards)
    check('052320991231' in sb2.index['races'], '同じ日の続きは一覧を引き継ぐ')
    S.SiteBuilder(E.load_model(), PW, out, '21000101', cards_fn=lambda nk: cards)
    check(sorted(os.listdir(out)) == ['meta.json'], '日が変わったら前の日のデータを消す')

    # 閲覧ページと同じ方式で復号できるか（Node の WebCrypto）
    if shutil.which('node'):
        out2 = tempfile.mkdtemp()
        sb3 = S.SiteBuilder(E.load_model(), PW, out2, '20991231', cards_fn=lambda nk: cards)
        sb3.on_snapshot(NK, 'T-5', start, odds, start - timedelta(minutes=5))
        sb3.flush()
        js = r"""
const fs = require('fs'); const { subtle } = globalThis.crypto;
(async () => {
  const dir = process.argv[1], pw = process.argv[2];
  const meta = JSON.parse(fs.readFileSync(dir + '/meta.json'));
  const base = await subtle.importKey('raw', new TextEncoder().encode(pw), 'PBKDF2', false, ['deriveKey']);
  const key = await subtle.deriveKey({ name: 'PBKDF2', salt: Buffer.from(meta.salt, 'base64'), iterations: meta.iter, hash: 'SHA-256' },
    base, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  const u = new Uint8Array(fs.readFileSync(dir + '/052320991231.enc'));
  const plain = await subtle.decrypt({ name: 'AES-GCM', iv: u.slice(0, 12) }, key, u.slice(12));
  const ds = new Blob([plain]).stream().pipeThrough(new DecompressionStream('deflate'));
  const obj = JSON.parse(await new Response(ds).text());
  console.log(obj.venue + ' ' + obj.rn + 'R ' + obj.snaps['T-5'].types['2車複'][0][0]);
})().catch(e => { console.error(e); process.exit(1); });
"""
        r = subprocess.run(['node', '-e', js, out2, PW], capture_output=True, text=True)
        check(r.returncode == 0 and r.stdout.strip() == '取手 5R 1-2', f'ブラウザと同じ方式で復号できる（{r.stdout.strip() or r.stderr.strip()[:200]}）')
    print('\n全テスト成功')


if __name__ == '__main__':
    main()
