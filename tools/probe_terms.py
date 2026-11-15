#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取得元候補サイトの利用規約から、自動取得・複製・転載に関する条文を抜き出して表示する（GitHub Actions用の調査）。"""
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

UA = 'Mozilla/5.0 (compatible; keirin-research-probe/1.0; personal analysis)'
SITES = {
    'netkeirin': ['https://keirin.netkeiba.com/', 'https://www.netkeiba.com/'],
    'チャリロト': ['https://www.chariloto.com/'],
    '楽天Kドリームス': ['https://www.kdreams.jp/', 'https://my.kdreams.jp/'],
}
LINK_WORDS = ('規約', '利用条件', 'kiyaku', 'terms', 'rule', 'policy', 'ポリシー')
KEYS = ('スクレイピング', 'クローラ', 'クローリング', 'ロボット', '自動', '機械的', 'プログラム', '複製', '転載', '二次利用', '負荷', '営利', '私的')


def get(url):
    try:
        with urlopen(Request(url, headers={'User-Agent': UA, 'Accept-Language': 'ja'}), timeout=30) as r:
            raw = r.read(800_000)
            enc = 'utf-8'
            m = re.search(rb'charset=["\']?([\w-]+)', raw[:3000])
            if m:
                enc = m.group(1).decode()
            return r.status, raw.decode(enc, 'replace')
    except HTTPError as e:
        return e.code, ''
    except (URLError, TimeoutError, OSError) as e:
        return None, str(e)


def text_of(html):
    t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S | re.I)
    t = re.sub(r'<br\s*/?>|</p>|</li>|</div>|</h\d>', '\n', t, flags=re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    return [re.sub(r'\s+', ' ', x).strip() for x in t.split('\n') if x.strip()]


def main():
    for name, tops in SITES.items():
        print(f'\n========== {name} ==========')
        cands = []
        for top in tops:
            st, html = get(top)
            print(f'top {top} → HTTP {st}')
            for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.S | re.I):
                lab = re.sub(r'<[^>]+>', '', label).strip()
                if any(w in (lab + href).lower() for w in LINK_WORDS):
                    cands.append((urljoin(top, href), lab[:30]))
            time.sleep(3)
        seen = set()
        for url, lab in cands:
            if url in seen or not url.startswith('http') or len(seen) >= 4:
                continue
            seen.add(url)
            st, html = get(url)
            lines = text_of(html)
            hits = [l for l in lines if any(k in l for k in KEYS) and len(l) < 400]
            print(f'\n-- 規約候補: {lab} {url} → HTTP {st} / 該当 {len(hits)}行')
            for l in hits[:25]:
                print(f'   ・{l}')
            time.sleep(3)


if __name__ == '__main__':
    main()
