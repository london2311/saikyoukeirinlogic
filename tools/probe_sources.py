#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
競輪データの取得元候補を、GitHub Actions（海外の無料マシン）から調べる。
各サイトについて: 接続できるか / robots.txt で自動取得が許されているか / データの載り方（JSON埋め込み等）
アクセスは各サイト数回だけ、間隔3秒。結果はログに出す。
"""
import re
import time
import urllib.robotparser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UA = 'Mozilla/5.0 (compatible; keirin-research-probe/1.0; personal analysis)'
SITES = {
    'WINTICKET': ['https://www.winticket.jp/keirin'],
    'KEIRIN.JP（JKA公式）': ['https://keirin.jp/', 'https://keirin.jp/pc/top'],
    '楽天Kドリームス': ['https://www.kdreams.jp/', 'https://www.kdreams.jp/racecard/'],
    'netkeirin': ['https://keirin.netkeiba.com/', 'https://keirin.netkeiba.com/race/race_list/'],
    'オッズパーク競輪': ['https://www.oddspark.com/keirin/'],
    'チャリロト': ['https://www.chariloto.com/'],
    '競輪ステーション': ['https://www.keirin-station.com/'],
}


def get(url):
    req = Request(url, headers={'User-Agent': UA, 'Accept-Language': 'ja'})
    try:
        with urlopen(req, timeout=30) as r:
            body = r.read(400_000).decode('utf-8', 'replace')
            return r.status, r.geturl(), body
    except HTTPError as e:
        body = e.read(20_000).decode('utf-8', 'replace') if e.fp else ''
        return e.code, url, body
    except (URLError, TimeoutError, OSError) as e:
        return None, url, str(e)


def describe(body):
    title = re.search(r'<title[^>]*>(.*?)</title>', body, re.S | re.I)
    text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', body, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    marks = [m for m in ('__NEXT_DATA__', '__PRELOADED_STATE__', '__NUXT__', 'application/ld+json', 'オッズ', '払戻', '出走表', '着順', '海外')
             if m in body]
    links = sorted(set(re.findall(r'href="(/[^"#?]{3,80})"', body)))[:25]
    return (title.group(1).strip()[:80] if title else '-'), text[:240], marks, links


def main():
    for name, urls in SITES.items():
        print(f'\n========== {name} ==========')
        base = re.match(r'https?://[^/]+', urls[0]).group(0)
        st, _, robots = get(base + '/robots.txt')
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(robots.splitlines() if st == 200 else [])
        dis = [l.strip() for l in robots.splitlines() if l.lower().startswith(('disallow', 'user-agent', 'crawl-delay'))][:15] if st == 200 else []
        print(f'robots.txt: HTTP {st}' + ('' if st == 200 else '（なし/取得不可）'))
        for l in dis:
            print(f'   {l}')
        time.sleep(3)
        for u in urls:
            st, final, body = get(u)
            title, text, marks, links = describe(body or '')
            allowed = rp.can_fetch(UA, u) if robots and st is not None else 'n/a'
            print(f'- {u} → HTTP {st} / robots許可: {allowed} / 最終URL: {final}')
            print(f'  タイトル: {title}')
            print(f'  本文冒頭: {text}')
            print(f'  目印: {marks}')
            print(f'  リンク例: {links}')
            time.sleep(3)


if __name__ == '__main__':
    main()
