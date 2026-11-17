#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
選手の運気（占い）: 生年月日から 六星占術・四柱推命（算命学の天中殺）を計算する。期待値の計算には使わない（参考表示・後で検証）。

  六星占術
    * 星数 = 生まれた日の干支の番号（甲子=1〜癸亥=60）。1〜10 土星人・11〜20 金星人・21〜30 火星人・31〜40 天王星人・41〜50 木星人・51〜60 水星人
      （それぞれ 天中殺 戌亥・申酉・午未・辰巳・寅卯・子丑 のグループに当たる）
    * ＋/−: 生まれ年の十二支が 子寅辰午申戌 なら＋、丑卯巳未酉亥 なら−。生まれ年が自分の天中殺の年なら「霊合星」
    * 運気（年・月・日）: その年・月・日の十二支が、天中殺の1つ目の支なら「停止」、2つ目なら「減退」、そこから
      種子 → 緑生 → 立花 → 健弱 → 達成 → 乱気 → 再会 → 財成 → 安定 → 陰影 と巡る。陰影・停止・減退 が大殺界、乱気 が中殺界、健弱 が小殺界
  四柱推命
    * 日干（生まれた日の十干）と、その年・月・日の十干の関係（通変星: 比肩・劫財・食神・傷官・偏財・正財・偏官・正官・偏印・印綬）
    * 天中殺（空亡）: 生まれた日の旬で決まる2つの十二支。その年・月・日の十二支が当たれば天中殺

  暦の区切り: 年は立春（2/4ごろ）、月は節入り（毎月4〜8日ごろ）で変わる。節入りは年によって1日ずれることがあるため固定日で近似。
  日の干支は ユリウス日 から計算（2000/1/1 = 戊午）。
"""
from datetime import date, datetime, timedelta

STEMS = '甲乙丙丁戊己庚辛壬癸'
BRANCHES = '子丑寅卯辰巳午未申酉戌亥'
ELEM = '木木火火土土金金水水'  # 十干の五行
STAR_NAMES = ['土星人', '金星人', '火星人', '天王星人', '木星人', '水星人']
CYCLE = ['停止', '減退', '種子', '緑生', '立花', '健弱', '達成', '乱気', '再会', '財成', '安定', '陰影']
# 運気の目安（表示用）: ◎ 好調・○ 良い・− ふつう・△ 小殺界・▲ 中殺界・✕ 大殺界
GRADE = {'安定': '◎', '財成': '◎', '達成': '◎', '立花': '○', '緑生': '○', '再会': '○', '種子': '−',
         '健弱': '△', '乱気': '▲', '陰影': '✕', '停止': '✕', '減退': '✕'}
SATSUKAI = {'陰影': '大殺界', '停止': '大殺界', '減退': '大殺界', '乱気': '中殺界', '健弱': '小殺界'}
TSUHEN_NOTE = {'比肩': '自立・マイペース', '劫財': '競争心・強気', '食神': '余裕・楽しむ', '傷官': '鋭さ・反骨', '偏財': '勝負運・人脈',
               '正財': '堅実・安定', '偏官': '攻め・行動力', '正官': '規律・評価', '偏印': 'ひらめき・気まぐれ', '印綬': '守り・学び'}
# 節入り（その日から次の月の十二支）: 1月 小寒→丑、2月 立春→寅、… 12月 大雪→子
SETSU_DAY = {1: 6, 2: 4, 3: 6, 4: 5, 5: 6, 6: 6, 7: 7, 8: 8, 9: 8, 10: 8, 11: 7, 12: 7}


def _jdn(d: date) -> int:
    return d.toordinal() + 1721425


def day_index(d: date) -> int:
    """日の干支の番号（0 = 甲子 〜 59 = 癸亥）"""
    return (_jdn(d) + 49) % 60


def year_index(d: date) -> int:
    """年の干支（立春で切り替え）"""
    y = d.year if (d.month, d.day) >= (2, SETSU_DAY[2]) else d.year - 1
    return (y - 4) % 60


def month_branch(d: date) -> int:
    """月の十二支（節入りで切り替え。2月の立春〜 = 寅）"""
    m = d.month if d.day >= SETSU_DAY[d.month] else d.month - 1
    m = 12 if m == 0 else m
    return (m + 0) % 12  # 1月(小寒〜)=丑(1), 2月=寅(2), …, 11月=亥(11), 12月=子(0)


def month_index(d: date) -> int:
    """月の干支（年の十干から 寅月の十干が決まる）"""
    b = month_branch(d)
    ys = year_index(d) % 10
    tora_stem = (ys % 5) * 2 + 2  # 甲己年→丙寅、乙庚年→戊寅、…
    k = (b - 2) % 12  # 寅月から何か月目
    s = (tora_stem + k) % 10
    # 干支の番号: 十干 s と 十二支 b が一致する 0〜59
    return next(i for i in range(60) if i % 10 == s and i % 12 == b)


def name60(i: int) -> str:
    return STEMS[i % 10] + BRANCHES[i % 12]


def tenchusatsu(day_idx: int):
    """生まれた日の旬 → 天中殺（空亡）の2つの十二支"""
    k = day_idx // 10
    x = (10 - 2 * k) % 12
    return x, (x + 1) % 12


def tsuhen(day_stem: int, other_stem: int) -> str:
    """日干から見た通変星"""
    e = {'木': 0, '火': 1, '土': 2, '金': 3, '水': 4}
    a, b = e[ELEM[day_stem]], e[ELEM[other_stem]]
    same = (day_stem % 2) == (other_stem % 2)
    if a == b:
        return '比肩' if same else '劫財'
    if (a + 1) % 5 == b:  # 我生（日干が生む）
        return '食神' if same else '傷官'
    if (a + 2) % 5 == b:  # 我剋（日干が剋す）
        return '偏財' if same else '正財'
    if (b + 2) % 5 == a:  # 剋我
        return '偏官' if same else '正官'
    return '偏印' if same else '印綬'  # 生我


def rokusei(birth: date):
    i = day_index(birth)
    star = STAR_NAMES[i // 10]
    x, y = tenchusatsu(i)
    yb = year_index(birth) % 12
    sign = '+' if yb % 2 == 0 else '−'
    return dict(star_no=i + 1, star=star, sign=sign, reigo=yb in (x, y), tcs=(x, y))


def cycle_of(branch: int, tcs) -> str:
    return CYCLE[(branch - tcs[0]) % 12]


def fortune(birth: date, on: date) -> dict:
    """その日の運気のまとめ（表示と検証用）"""
    r = rokusei(birth)
    bi = day_index(birth)
    ds = bi % 10
    yi, mi, di = year_index(on), month_index(on), day_index(on)
    y, m, d = cycle_of(yi % 12, r['tcs']), cycle_of(mi % 12, r['tcs']), cycle_of(di % 12, r['tcs'])
    x1, x2 = r['tcs']
    return dict(
        star=r['star'], sign=r['sign'], reigo=r['reigo'],
        year=y, month=m, day=d, grade_year=GRADE[y], grade_month=GRADE[m], grade_day=GRADE[d],
        nikkan=STEMS[ds] + '（' + ELEM[ds] + '）',
        tsuhen_year=tsuhen(ds, yi % 10), tsuhen_month=tsuhen(ds, mi % 10), tsuhen_day=tsuhen(ds, di % 10),
        tcs=BRANCHES[x1] + BRANCHES[x2],
        tcs_year=(yi % 12) in r['tcs'], tcs_month=(mi % 12) in r['tcs'], tcs_day=(di % 12) in r['tcs'],
        week=week_of(r['tcs'], on),
    )


def week_of(tcs, on: date) -> dict:
    """今週（月〜日）の日運: 良い日（◎○）と殺界の日（✕▲）の数"""
    mon = on - timedelta(days=on.weekday())
    gs = [GRADE[cycle_of(day_index(mon + timedelta(days=k)) % 12, tcs)] for k in range(7)]
    good, bad = sum(g in '◎○' for g in gs), sum(g in '✕▲' for g in gs)
    return dict(good=good, bad=bad, grade='◎' if good >= 4 and bad <= 1 else '✕' if bad >= 4 else '○' if good > bad else '△' if bad > good else '−')


def score(f: dict) -> int:
    """ざっくりの運気点（検証用）: 年・月・日の ◎+2 ○+1 −0 △-1 ▲-2 ✕-2、天中殺の日は-1"""
    pt = {'◎': 2, '○': 1, '−': 0, '△': -1, '▲': -2, '✕': -2}
    return pt[f['grade_year']] + pt[f['grade_month']] + pt[f['grade_day']] - int(f['tcs_day'])


def short(f: dict) -> str:
    """選手の並びに添える短い表示（例: 今年 停止✕・今月 安定◎・今日 達成◎）"""
    return f'{f["star"]}{f["sign"]} 年{f["grade_year"]}月{f["grade_month"]}週{f["week"]["grade"]}日{f["grade_day"]}'


def text(f: dict) -> str:
    """期待値ボードの選手メモ用の1行"""
    def cyc(lab, c, g):
        s = SATSUKAI.get(c)
        return f'{lab} {c}{"（" + s + "）" if s else ""}{g}'
    w = f['week']
    six = (f'六星 {f["star"]}({f["sign"]}){"・霊合星" if f["reigo"] else ""}: '
           + '・'.join([cyc('今年', f['year'], f['grade_year']), cyc('今月', f['month'], f['grade_month']),
                       f'今週 良い日{w["good"]}・殺界{w["bad"]}{w["grade"]}', cyc('今日', f['day'], f['grade_day'])]))
    tcs = [lab for lab, k in (('年', 'tcs_year'), ('月', 'tcs_month'), ('日', 'tcs_day')) if f[k]]
    four = (f'四柱 日干 {f["nikkan"]}: 今日 {f["tsuhen_day"]}（{TSUHEN_NOTE[f["tsuhen_day"]]}）・今月 {f["tsuhen_month"]}'
            + f'・天中殺 {f["tcs"]}' + (f'（今は{"・".join(tcs)}が天中殺）' if tcs else ''))
    return six + ' ／ ' + four


def parse_date(s):
    try:
        return datetime.strptime(str(s)[:10].replace('/', '-'), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None
