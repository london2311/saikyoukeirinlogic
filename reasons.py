#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reasons.py — 「なぜこの買い目か」の説明を作る（期待値ボード用）

確率や期待値はモデルとオッズの計算で決まる。この説明は、その計算に効いている要素を言葉にしたもの:
  * 選手ごと: 競走得点の順位、脚質、本人コメント（自力・自在・決めず・補充出走・○○君）、並びの位置、
    ラインの相手との関係（同県・同地区、先輩・後輩＝期の差）、地元、1着の確率（モデルとオッズの評価の違い）
  * 買い目ごと: 同じラインか（ワンツー・ライン決着型）、別ラインの組み合わせか、
    3連単のオッズから見た確率と、その券種自身のオッズが示す確率の差（＝なぜ割安か）
"""
import re

REGION = {}
for reg, prefs in {
    '北日本': '北海道 青森 岩手 宮城 秋田 山形 福島', '関東': '茨城 栃木 群馬 埼玉 東京 山梨 新潟 長野',
    '南関東': '千葉 神奈川 静岡', '中部': '愛知 岐阜 三重 富山 石川 福井', '近畿': '滋賀 京都 大阪 兵庫 奈良 和歌山',
    '中国': '鳥取 島根 岡山 広島 山口', '四国': '徳島 香川 愛媛 高知', '九州': '福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 沖縄',
}.items():
    for p in prefs.split():
        REGION[p] = reg

JIRIKI = re.compile(r'自力(?!自在)|自分で|前々|一人で|先行|逃げ')
JIZAI = re.compile(r'自在|取れた位置|流れ見て|位置から|臨機応変')
FOLLOW = re.compile(r'(\S+?)(君|さん)')
STYLE = {'逃': '逃げ（先行）型', '両': '自在（逃げも追いも）型', '追': '追込（番手・差し）型'}


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _intent(comment):
    c = comment or ''
    if '補充' in c:
        return '補充出走（急な代役）'
    if '決めず' in c:
        return '並びを決めず（展開待ち）'
    if '単騎' in c:
        return '単騎で戦う'
    if JIRIKI.search(c):
        return '自力で動く宣言'
    if JIZAI.search(c):
        return '自在に立ち回る'
    m = FOLLOW.search(c)
    if m:
        return f'{m.group(1)}選手の後ろを回る'
    return ''


def rider_notes(cards, res):
    """{車番: [説明, ...]} と、ライン構成の説明"""
    rows = [r for r in cards if str(r.get('absent', 0)) not in ('1', 'True', 'true')]
    by_car = {int(r['car_number']): r for r in rows}
    pts = sorted(((_f(r.get('race_point')) or 0, int(r['car_number'])) for r in rows), reverse=True)
    rank = {c: i + 1 for i, (_, c) in enumerate(pts)}
    lines = {}
    for r in rows:
        single = str(r.get('is_single', '')) in ('1', '1.0', 'True', 'true') or not r.get('line_order')
        lid = f's{r["car_number"]}' if single else str(r.get('line_order'))
        lines.setdefault(lid, []).append((int(_f(r.get('line_position')) or 1), int(r['car_number'])))
    lines = {k: [c for _, c in sorted(v)] for k, v in lines.items()}
    line_of = {c: (k, L) for k, L in lines.items() for c in L}
    n_nige = sum(1 for r in rows if r.get('style') == '逃')
    win = {x['num']: x for x in res.get('riders', [])}
    notes = {}
    for c, r in by_car.items():
        out = []
        rk = rank.get(c)
        if rk and rk <= 3:
            out.append(f'競走得点 {r.get("race_point", "")}（レース{rk}位）')
        st = STYLE.get(r.get('style', ''))
        if st:
            out.append(st + (f'・逃げ型が他に{n_nige - 1}人（主導権争い）' if r.get('style') == '逃' and n_nige >= 3 else ''))
        it = _intent(r.get('comment', ''))
        if it:
            out.append(f'コメント「{r.get("comment", "")}」→ {it}')
        k, L = line_of.get(c, (None, [c]))
        if len(L) <= 1:
            out.append('単騎')
        else:
            pos = L.index(c) + 1
            mates = '-'.join(map(str, L))
            out.append(f'ライン {mates} の{["先頭", "番手", "3番手", "4番手"][min(pos, 4) - 1]}')
            if pos >= 2:
                lead = by_car.get(L[pos - 2])
                if lead:
                    rel = []
                    if lead.get('prefecture') and lead.get('prefecture') == r.get('prefecture'):
                        rel.append('同県')
                    elif REGION.get(lead.get('prefecture')) and REGION.get(lead.get('prefecture')) == REGION.get(r.get('prefecture')):
                        rel.append('同地区')
                    tl, tr = _f(lead.get('term')), _f(r.get('term'))
                    if tl and tr and tl != tr:
                        rel.append('前が後輩' if tl > tr else '前が先輩')
                    if rel:
                        out.append(f'前の{L[pos - 2]}番と' + '・'.join(rel))
            if pos == 1 and len(L) >= 2:
                b = by_car.get(L[1])
                tl, tb = _f(r.get('term')), _f(b.get('term')) if b else None
                if b and tl and tb and tb < tl:
                    out.append(f'後ろ（{L[1]}番）が先輩 → 引っ張る責任があり、逃げる意欲が高い')
        ga = res.get('girls_adj') or {}
        if ga:
            if c == ga.get('fav'):
                out.append('ガールズの1番人気 → 後ろを狙う選手が多い（2着の相手は得点下位まで広く）')
            elif c in ga.get('up', []):
                out.append('ガールズ: 1番人気が勝つ時、得点下位の選手が後ろから2着に来やすい（過去の検証で約1.25倍。補正済み）')
            elif c in ga.get('down', []):
                out.append('ガールズの逃げ型: 1番人気が勝つ時の2着には残りにくい（約0.6倍。補正済み）')
        w = win.get(c)
        if w and w.get('win_market') is not None:
            d = w['win'] - w['win_market']
            if abs(d) >= 0.02:
                out.append(f'1着の確率 {w["win"] * 100:.0f}%（オッズの評価 {w["win_market"] * 100:.0f}% より{"高い" if d > 0 else "低い"}）')
        notes[c] = out
    return notes, lines


def ticket_reason(bt, key, t, own_implied, lines):
    """買い目1点の説明（なぜ割安か・どういう決着か）"""
    cars = [int(x) for x in key.split('-')]
    out = []
    line_of = {c: tuple(L) for L in lines.values() for c in L}
    same = [line_of.get(c) for c in cars]
    if len(set(same)) == 1 and same[0] and len(same[0]) >= len(cars):
        out.append('同じラインで決まる組（ライン決着・ワンツー型）')
    elif len(cars) >= 2 and same[0] and same[1] and same[0] == same[1]:
        out.append(f'{cars[0]}番と{cars[1]}番は同じライン')
    else:
        out.append('別ラインどうしの組み合わせ')
    pm, pi = t.get('prob_market'), own_implied
    if pm and pi and bt != '3連単':  # 3連単は確率の元が3連単のオッズそのものなので比べない
        out.append(f'3連単のオッズから見た確率 {pm * 100:.1f}% に対し、{bt}のオッズが示す確率は {pi * 100:.1f}%'
                   + ('（この券種で売れていない＝割安）' if pm > pi * 1.05 else ''))
    if t.get('prob') and pm:
        d = t['prob'] - pm
        if abs(d) >= 0.005:
            out.append(f'モデル（出走表の要素）の補正で確率 {"＋" if d > 0 else "−"}{abs(d) * 100:.1f}ポイント')
    return out


def build(cards, res, ev_min=0.9, types=('2車複', 'ワイド', '3連複', '2車単', '3連単')):
    """期待値ボードに載せる説明: dict(riders={車番: [..]}, tickets={'券種|組': [..]})（EV ev_min 以上の組だけ）"""
    notes, lines = rider_notes(cards, res)
    by_type = {}
    for t in res.get('tickets', []):
        by_type.setdefault(t['type'], []).append(t)
    tk = {}
    for bt in types:
        ts = by_type.get(bt, [])
        inv = sum(1.0 / t['odds'] for t in ts if t['odds'])
        for t in ts:
            if t['ev'] < ev_min or t['prob'] < 0.005:
                continue
            # その券種のオッズが示す確率（ワイドは当たりが3組あるので合計3になるように）
            own = (1.0 / t['odds']) / inv * (3 if bt == 'ワイド' else 1) if inv else None
            tk[f'{bt}|{t["key"]}'] = ticket_reason(bt, t['key'], t, own, lines)
    return dict(riders={str(k): v for k, v in notes.items()}, tickets=tk)
