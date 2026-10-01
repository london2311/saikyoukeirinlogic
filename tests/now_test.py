#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keirin_now.py / keirin_engine.py のオフラインテスト:  python tests/now_test.py

通信の代わりに、架空のレースで WINTICKET と同じ形のページ（__PRELOADED_STATE__）を返して、
「URL候補の自動切替 → 出走表・オッズの解析 → v105の判定」が通しで動くことを確認する。
"""
import io
import json
import os
import sys
import tempfile
from urllib.error import HTTPError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import keirin_collector as C  # noqa: E402
import keirin_engine as E  # noqa: E402
import keirin_now as N  # noqa: E402

# ---- 架空のレース（7車・3ライン）
STRENGTH = {1: 5.0, 2: 3.0, 3: 2.0, 4: 1.5, 5: 1.0, 6: 0.8, 7: 0.6}
LINES = [[1, 4, 7], [2, 5], [3, 6]]


def pl(o):
    rem, p = sum(STRENGTH.values()), 1.0
    for n in o:
        p *= STRENGTH[n] / rem
        rem -= STRENGTH[n]
    return p


def build_states():
    nums = list(STRENGTH)
    players, records, entries = [], [], []
    for n in nums:
        pid = f'p{n}'
        players.append(dict(id=pid, name=f'選手{n}', yomi='', age=30, prefecture='千葉', term=100))
        records.append(dict(playerId=pid, style='逃' if n <= 3 else '追', racePoint=110 - n * 3, firstRate=30 - n * 3,
                            secondRate=50 - n * 4, thirdRate=65 - n * 5, standingCount=n, homeCount=1, backCount=8 - n,
                            escapeCount=4 - n % 4, makuriCount=1, sashiCount=n % 3, markCount=0,
                            firstCount=8 - n, secondCount=5, thirdCount=4, outCount=n + 2, comment=''))
        entries.append(dict(number=n, bracketNumber=n, absent=False, playerId=pid,
                            playerCurrentTermClass=1, playerCurrentTermGroup=1))
    race = dict(id='012320991231', scheduleId='2099123123', number=5, startAt=4102444800,
                **{'class': 'S級'}, raceType3='一般', distance=2025, lap=5, entriesNumber=7)
    lp = dict(lineType='三分戦', lines=[dict(entries=[dict(numbers=l)]) for l in LINES])
    race_q = dict(queryKey=['k', 'FETCH_KEIRIN_RACE'],
                  state=dict(data=dict(race=race, players=players, records=records, entries=entries, linePrediction=lp)))
    outs = [(a, b, c) for a in nums for b in nums for c in nums if len({a, b, c}) == 3]
    tri = [dict(id=f't{a}{b}{c}', key=[a, b, c], odds=round(0.75 / pl((a, b, c)), 1), popularityOrder=1) for a, b, c in outs]
    pair = {}
    for a, b, c in outs:
        k = tuple(sorted((a, b)))
        pair[k] = pair.get(k, 0.0) + pl((a, b, c))
    # 2車複 1=2 だけ、3連単から見て1.8倍の配当（＝割安）にしておく
    quin = [dict(id=f'q{a}{b}', key=[a, b], odds=round(0.75 / p * (1.8 if (a, b) == (1, 2) else 1.0), 1),
                 popularityOrder=1) for (a, b), p in pair.items()]
    odds_q = dict(queryKey=['k', 'FETCH_KEIRIN_RACE_ODDS'], state=dict(data=dict(trifecta=tri, quinella=quin)))
    # 発走前で投票が未集計のページ: 全組 9999.9倍・人気順0（2026-10 の実ページと同じ形）
    pre = dict(trifecta=[dict(x, odds=9999.9, popularityOrder=0) for x in tri],
               quinellaPlace=[dict(id=f'w{a}{b}', key=[a, b], odds=0, minOdds=9999.9, maxOdds=9999.9, popularityOrder=0)
                              for (a, b) in pair],
               isAggregated=False, oddsUpdatedAt=0)
    pre_q = dict(queryKey=['k', 'FETCH_KEIRIN_RACE_ODDS'], state=dict(data=pre))
    cup = dict(id='2099123123', startDate='20991230', endDate='20991231', venueId='23', name='テスト杯', grade=2)
    top = dict(tanStackQuery=dict(queries=[dict(queryKey=['k', 'TOP'], state=dict(data=dict(cups=[cup])))]))
    return (top, dict(tanStackQuery=dict(queries=[race_q])), dict(tanStackQuery=dict(queries=[race_q, odds_q])),
            dict(tanStackQuery=dict(queries=[race_q, pre_q])))


def main():
    top, card_only, full, presale = build_states()
    calls = []
    pages = {}

    def fake_fetch(url, interval=0):
        calls.append(url)
        print(f'  GET {url}')  # 本物の fetch_html と同じく取得ログを出す（--json の出力を壊さないこと）
        wrap = lambda st: '<script>window.__PRELOADED_STATE__ = ' + json.dumps(st, ensure_ascii=False) + ';window.__X = 1</script>'
        if url.endswith('/keirin'):
            return wrap(top)
        for part, st in pages.items():
            if f'/{part}/' in url:
                if st is None:
                    raise HTTPError(url, 404, 'not found', None, None)
                return wrap(st)
        raise HTTPError(url, 404, 'not found', None, None)

    C.fetch_html = fake_fetch
    N.URL_MEMO = os.path.join(tempfile.mkdtemp(), 'memo.json')

    def run(argv):
        calls.clear()
        sys.argv = ['keirin_now.py'] + argv
        buf, old = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            N.main()
        finally:
            sys.stdout = old
        return buf.getvalue()

    # 1つ目の候補: 出走表のみ → 2つ目: 存在しない → 3つ目: 出走表＋オッズ
    pages.update(racecard=card_only, odds=None, raceresult=full)
    out = json.loads(run(['取手', '5', '--date', '20991231', '--json']))
    assert out['ok'] and out['market_ok'], out
    assert [b['type'] + ' ' + b['key'] for b in out['bets']] == ['2車複 1-2'], out['bets']
    assert abs(sum(r['win'] for r in out['riders']) - 1) < 1e-9
    assert len(out['hit_picks']['ワイド']) == 3
    print('✔ 取得 → 解析 → 判定（割安な2車複 1=2 だけを検出）')

    assert len(calls) == 4, calls  # トップ + URL候補3つ（出走表のみ → 404 → 出走表＋オッズ）
    print('✔ URL候補を順に試して、出走表とオッズがそろうページを見つける')

    text = run(['取手', '5', '--date', '20991231'])
    assert len(calls) == 2, calls  # トップ + 記憶したURLの1回だけ
    print('✔ 動いたURLの形を記憶し、2回目は無駄なアクセスをしない')
    assert '勝負レース' in text and '2車複 1=2' in text, text
    print('✔ 人が読む出力')

    # オッズが取れない場合はモデルのみで確率を出し、EV判定はしない
    res = E.analyze(E.load_model(), C.parse_racecard(card_only), [], '取手')
    assert res['ok'] and not res['market_ok'] and not res['bets'] and res['warnings']
    print('✔ オッズ不足時はEVを出さず警告')

    # 発走前（9999.9倍の仮表示）: オッズなし扱い。オッズ欄のあるページで打ち切り、他のURLは試さない
    assert all(o['odds'] == '' and o['odds_max'] == '' for o in C.parse_odds(presale))
    assert {o['odds'] for o in C.parse_odds(full) if o['bet_type'] == '3連単'} != {''}
    N.URL_MEMO = os.path.join(tempfile.mkdtemp(), 'memo.json')
    pages.update(racecard=presale, odds=full, raceresult=full)
    out = json.loads(run(['取手', '5', '--date', '20991231', '--json']))
    assert out['ok'] and not out['market_ok'] and not out['bets'] and out['warnings'], out
    assert len(calls) == 2, calls  # トップ + racecard の1回だけ
    print('✔ 発売前の仮オッズ（9999.9倍）は使わず、モデルのみで判定')
    print('\nall passed')


if __name__ == '__main__':
    main()
