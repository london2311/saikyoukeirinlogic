# 使用説明書 — netkeirin 収集（netkeirin_collector.py）

2026/11/15 から WINTICKET が海外IPからの利用を遮断し、GitHub Actions（海外）からは
`keirin_collector.py` で収集できなくなりました。代わりに netkeirin（https://keirin.netkeiba.com/）
から、前日の全レースを毎朝収集します。遮断の回避（日本のプロキシ・VPN 経由など）はしません。

## 規約上の注意（必ず守ること）

netkeirin 利用規約 第14条により、取得したデータは**私的利用の範囲内**でのみ使えます。

- データを公開しない（「不特定または多数の者が知りうる状態におく行為」は目的を問わず不可）。
  → 収集データは**非公開リポジトリ（london2311/datebase）にだけ**保存します。
    このリポジトリ（saikyoukeirinlogic、公開）には CSV・HTML・JSON の中身を**コミットしない**こと。
- 複製物の販売・出版、営利目的の利用は不可。用途は本人の分析のみ。
- GitHub Actions のログ（公開）にはレースの中身を出さない（URL・件数・成功/失敗だけ）。
- アクセスは 1 リクエストごとに 3 秒以上空ける。User-Agent は
  `Mozilla/5.0 (compatible; keirin-research/1.0; personal analysis)`（正直に名乗る）。
  1日1回、その日の分だけ取る。
- 403 / 429 など拒否の応答が返ったら、回避せず中断します（終了コード 2、ログに表示）。

## 仕組み

| 手順 | URL（すべて keirin.netkeiba.com、robots.txt で制限なし） | 取れるもの |
|---|---|---|
| 1 | `/race/api_race_list.html?kaisai_date=YYYYMMDD&jyocd=…` | 開催場とレースID（場ごとに1回） |
| 2 | `/race/entry/?race_id=…` | 出走表・並び予想（ライン） |
| 3 | `/race/result/?race_id=…` | 着順・決まり手・SB・払戻・レース情報 |
| 4 | `POST /api/race/`（`class=AplRaceOdds&method=get&compress=0&race_id=…`） | 最終オッズ（全券種・全組み合わせ・人気順） |

手順4は、オッズページ（`/race/odds/`）上の netkeirin 自身の JavaScript がオッズ表示のために呼ぶのと
同じ API・同じパラメータです（`compress=0` は同じ JS が持つ非圧縮の指定）。応答の `list_3`〜`list_9` が
枠複・枠単・2車複・2車単・ワイド・3連複・3連単で、払戻と突き合わせて対応を確認しています。

1日あたり「開催場数 + レース数×3」回のアクセス（例: 7場 67レース → 208回・約11分）。

### race_id の形式

netkeirin のレースIDは `YYYYMMDD + 場コード2桁 + レース番号2桁`（例 `202611142801`）。
場コードは WINTICKET と同じ（JKA の場コード）なので、**既存形式
`レース番号2桁 + 場コード2桁 + 開催日8桁`（例 `012820261114`）に変換して保存**します。
同じレースは WINTICKET 版と同じ race_id になり、分析側の重複除去（`DEDUP_KEYS`）がそのまま効きます。
取得元の区別のため、`races.csv` にだけ列 `source`（値 `netkeirin`）を追加しています
（WINTICKET 版の行は空欄）。`cup_id` も既存と同じ「開催初日 + 場コード」、
`schedule_id` は「開催日 + 場コード」です。

## 出力（keirin_collector.py と同じ形式・同じ列名）

```
data/YYYY/races.csv        1行 = 1レース
data/YYYY/racecards.csv    1行 = 1選手
data/YYYY/results.csv      1行 = 1選手の着順
data/YYYY/payoffs.csv      1行 = 的中1組の払戻
data/YYYY/odds/odds_YYYYMMDD.csv   1行 = 1レース×1券種×1組み合わせ
```

保存は `keirin_collector.py` の `append_csv` / `append_odds_csv` を再利用しています
（同じキーの行は追記しない・空欄だけ補完）。

### 取れる列・取れない列

**racecards.csv（出走表）**

| 列 | netkeirin | 備考 |
|---|---|---|
| race_id, schedule_id, race_number, car_number, bracket_number | ○ | |
| player_id | ○ | 登録番号を6桁ゼロ埋め（既存と同じ） |
| player_name, player_yomi | ○ | よみはカタカナ |
| age, prefecture, term | ○ | 府県の省略（神奈・和歌・鹿児）は正式名に直す |
| class, group | ○ | 級班 S1→class 1/group 1、SS→1/0、A3→2/3、L1→4/1（既存の数値と同じ） |
| race_point, style, gear_ratio, comment | ○ | |
| first_rate, second_rate, third_rate | ○ | % の数値（例 25.0） |
| standing_count, home_count, back_count（S/H/B） | ○ | **v105 の拡張特徴量に使用** |
| escape_count, makuri_count, sashi_count, mark_count（決まり手回数） | ○ | **v105 の拡張特徴量に使用** |
| first_count, second_count, third_count, out_count | ○ | |
| line, line_position, line_order, line_size, line_head, is_single | ○ | 出走表ページの「並び予想」から |
| line_type | △ | netkeirin に表記が無いため「2人以上のライン数」から推定（2→二分戦, 3→三分戦, 4→四分戦, それ以外→細切れ）。WINTICKET の分類とは一部異なる場合あり |
| absent | ○ | 結果で「欠車」の選手を 1 |

**results.csv（着順）**: race_id, schedule_id, race_number, order, car_number, player_id, player_name,
factor（決まり手。netkeirin の「ク」は既存表記の「捲」に変換）, final_half（上り）, margin（着差）,
accident（失格・落車など。order は 0）, standing（S）, back（B）— すべて取得。

**payoffs.csv（払戻）**: 3連単・3連複・2車単・2車複・ワイド・枠単・枠複の race_id, race_number, bet_type,
combination（`3>1>2` は `3-1-2` に変換）, payoff（100円あたり）, popularity — すべて取得。

**races.csv（レース情報）**

| 列 | netkeirin | 備考 |
|---|---|---|
| race_id, date, venue_id, venue_name, race_number | ○ | |
| cup_id, cup_name, grade, day_index | ○ | grade は既存と同じ数値（FII=1, FI=2, GIII=3, GII=4, GI=5, GP=6） |
| class, race_type, distance, lap, start_at, entries_number | ○ | race_type は WINTICKET の略称に変換（チャレンジ予選→チ予選、ガールズ予選1→ガ予１、初日特別選抜→初特選 など。対応表に無い表記はそのまま）|
| weather, wind_speed | × | netkeirin のページに無いため空欄 |
| cancel | △ | 結果が無いレースは保存しないため常に 0 |
| source | ○ | 追加列。`netkeirin` |

**odds（最終オッズ）**: 列 race_id, date, race_number, bet_type, combination, odds, odds_max, payoff,
popularity, absent（既存と同じ）。3連単・3連複・2車単・2車複・ワイド・枠単・枠複の全組み合わせ。
odds は常に倍率、ワイドは odds=最低 / odds_max=最高、的中組は payoff 列に 100円あたり払戻を入れる。
倍率が 0（欠車を含む組など）は odds 空欄・absent=1。取得は結果確定後なので締切時の最終オッズ。

## GitHub Actions（.github/workflows/netkeirin-collect.yml）

- 毎朝 06:17 JST（cron `17 21 * * *`）に前日分を収集。手動実行（Actions → netkeirin-collect →
  Run workflow）では `date`、または `date_from`/`date_to`（期間。例 11/14 以降の空白期間を埋める）を指定できます。
- 公開リポジトリをチェックアウトしてスクリプトを動かし、データ用の非公開リポジトリを
  `_data_repo` にチェックアウトして、出力先を `_data_repo/data` にします。コミット・push は
  **データ用リポジトリにだけ**行います（ワークフローの権限は `contents: read`）。
- `DATA_REPO` / `DATA_REPO_TOKEN` が未設定なら、何もせず「設定が必要」と警告を出して終了します。
- 旧 `keirin-collect.yml`（WINTICKET 用）は schedule を削除し、手動実行のみにしました。

### トークン設定手順（初回のみ）

1. GitHub 右上のアイコン → **Settings** → **Developer settings** → **Personal access tokens** →
   **Fine-grained tokens** → **Generate new token**。
   - Repository access: **Only select repositories** → `london2311/datebase` **だけ**を選ぶ。
   - Permissions → Repository permissions → **Contents: Read and write**（他は不要）。
   - 有効期限を決めて作成し、表示されたトークンをコピー。
2. `london2311/saikyoukeirinlogic` → **Settings** → **Secrets and variables** → **Actions**。
   - **Secrets** タブ → New repository secret → 名前 `DATA_REPO_TOKEN`、値 = 1 のトークン。
   - **Variables** タブ → New repository variable → 名前 `DATA_REPO`、値 `london2311/datebase`。
3. Actions → netkeirin-collect → Run workflow で動作確認（ログには件数だけが出ます）。
   トークンの期限が切れたら 1〜2 をやり直してください。

`keirin-train.yml` も同じ `DATA_REPO` / `DATA_REPO_TOKEN` を使って `_data_repo` を読みます。

## 発走前オッズの記録（netkeirin_snapshot.py / .github/workflows/netkeirin-snapshot.yml）

結果ページの最終オッズは締切後の値なので、「買う時点のオッズで判定したら儲かるか」は検証できない。
そこで各レースの **締切15分前・10分前・5分前・2分前**（締切は発走の5分前。レース一覧に締切時刻があればそれを使う）に、全券種（3連単・3連複・2車単・2車複・ワイド）のオッズを1回ずつ記録する。

- 保存先: データ用リポジトリの `data/YYYY/odds_snap/snap_YYYYMMDD.csv`（公開側には何も書かない）
- 列: race_id, date, race_number, snap（C-15 / C-10 / C-5 / C-2 = 締切の何分前か。2026/11/16 の分は発走基準の T-xx）, deadline_at（締切時刻）, snap_at（実際に取得した時刻）, start_at, official_dt,
  bet_type, combination, odds（ワイドは最低）, odds_max, popularity, absent
- 1日を3つの時間帯（08:13〜13:30 / 13:07〜18:50 / 18:41〜24:20 JST）に分けて実行。1時間ごとにコミット
- 1レースあたり4リクエスト＋出走表1回＋レース一覧の再取得（30分ごと）。間隔は3秒のまま
- 予定より2分以上遅れた記録は取らない（一番遅い締切2分前は取る）。締切を過ぎたら取らない。途中から再開しても記録済みは取り直さない

## 過去分の補充（netkeirin_backfill.py / .github/workflows/netkeirin-backfill.yml）

`data/YYYY/races.csv` のレース数が120未満の日（2026/07 以降）を、古い日から順に補充する。
1日4回、1回あたり約2時間。取得済みのレース（WINTICKET 由来を含む）は取り直さない。
補充を試みた日は `data/netkeirin_backfill.json` に記録し、繰り返さない。全部埋まれば何もせず終わる。

## 通知メール（netkeirin_alert.py）

締切前オッズの記録（netkeirin-snapshot）の中で、締切5分前・2分前のオッズで `analysis/strategies.json` の
`"alert": true` の買い方（2車複 EV1.10以上 / 2車複 EV1.20以上×20〜100倍 / ワイド EV1.10以上）を判定し、
該当があれば自分宛てに Gmail で知らせる。

- 設定（初回のみ）: Gmail の2段階認証をオンにして「アプリパスワード」（16文字）を作り、このリポジトリの
  Settings → Secrets and variables → Actions → New repository secret に登録する
  - `ALERT_GMAIL_USER` … 送信に使う Gmail アドレス
  - `ALERT_GMAIL_APP_PASSWORD` … アプリパスワード（通常のログインパスワードではない）
  - `ALERT_TO` … 送り先（省略時は ALERT_GMAIL_USER と同じ）
- 未設定なら判定だけ行い、メールは送らない。1日の送信は60通まで。同じ買い目は1回だけ
- Actions のログには件数だけを出す（レース・買い目は出さない）

## 期待値ボード（個人閲覧用・netkeirin_site.py / live/index.html）

締切前オッズの記録（締切15分前・10分前・5分前・2分前）のたびに、全券種・全組の「当たる確率（v105 融合）・今のオッズ・
期待値（EV）・この倍率以上なら買い」を計算し、**AES-256-GCM で暗号化**してこのリポジトリの `site-data` ブランチに置く。
閲覧ページ `https://<ユーザー名>.github.io/saikyoukeirinlogic/live/` でパスワードを入れると、ブラウザの中だけで復号して表示する。

- 設定: Secrets に `SITE_PASSWORD`（**16文字以上を推奨**、12文字未満なら作らない）を登録する
- 鍵は PBKDF2-SHA256（20万回）。塩は日ごとに作り直す。暗号化されたファイルは公開の場所にあるが、パスワード無しでは読めない
- `site-data` ブランチは毎回1コミットに作り直して強制 push する（履歴をためない）。1〜2分ごとに更新
- 場ごとのタブ → レース → 時点（締切15/10/5/2分前）を切り替え。券種ごとに期待値の高い順（既定は確率0.5%以上・300倍以下の上位15組、全組も表示可）
- 「買い目群（合成オッズ）」表示: 流し・フォーメーション・BOX・ライン決着の候補（formations.py）を、
  合成オッズ（1÷Σ(1÷オッズ)、オッズに反比例して賭けた場合）×的中率 の期待値順に表示。期待値重視（的中率10%以上）と
  的中率重視（30%以上）。✂ は「期待値の低い買い目を切ると期待値が上がる」提案（的中率の下限は守る）。
  過去の検証（analysis/formation_study.py）では、買い目群・切りとも1点買いと同程度で、期間によって結果がぶれる（参考扱い）
- ★ = 事前登録した買い方（strategies.json の alert）に該当。2車複・ワイド以外は過去の検証で負けていたため「参考」表示
- パスワードを変えた場合は、次の時間帯の実行から新しいパスワードで読めるようになる（その日の分は作り直し）

## 実行のリレー（GitHub の定時実行に頼らない）

- GitHub の定時実行（cron）は、数時間遅れたり実行されないことがある（実際に 18:41 の実行が翌朝 4:51 に始まり、08:13 と 06:17 の実行は来なかった）
- そこで記録ジョブ（netkeirin-snapshot）は **リレー方式**: 1回 約5時間半で終わり、終わる直前に次の実行を自分で予約する。
  その日のレースが終わったら同じ実行のまま翌日の予定に切り替え、翌日の分も続けて記録する
- その日の最後のレースの記録が終わったら、**結果の収集（netkeirin-collect、払戻の確定を45分待つ）→ 台帳・検証レポート（netkeirin-ledger）** を順に予約する
- 定時実行は、リレーが途切れた時の再開用として残している
- リレーを止めたい時: Settings → Secrets and variables → Actions → **Variables** に `SNAPSHOT_RELAY` = `off`。
  実行中のものを手動でキャンセルした時と、サーバーから拒否された時も止まる

## 日付の切り替え（期待値ボード）

- 夜の時間帯（18:41〜）で最後のレースが終わったら、すぐに**翌日の全レースの予定**を取得して期待値ボードを切り替える
  （オッズと期待値は、翌日の各レースの締切15分前から順に出る）
- 念のため 00:03 にも、その日の予定を出す実行がある（切り替え済みなら変更なし）
- ページは一覧の日付を見て「明日の予定」「前日のデータ」を表示し、前日のレースを「締切まで」と数えない

## 締切直前の「最新」（期待値ボードの更新の速さ）

- 記録（台帳・検証に使う）は従来どおり締切15・10・5・2分前の4回
- それとは別に、**締切6分前から締切まで30秒ごと**にオッズを取り直し、期待値ボードの「最新」だけを更新する（記録のファイルには入れない）。
  確定予想は、締切までの残りに近い時点の下落の学習を使う
- 締切直前のレースがある間は、期待値ボードを15秒ごとに公開（それ以外は60秒）
- ページは、資金管理のトークンを登録した端末では15秒ごと、未登録の端末では60秒ごとに確認する
  （GitHub の API はトークン無しだと1時間60回まで）
- 5秒ごとにしない理由: 取得先のサーバーへの負担（私的利用の範囲で控えめにする）と、GitHub の公開・API の制限。
  表示までの遅れは、オッズの取得（30秒ごと）＋公開（15秒ごと）＋ページの確認（15秒ごと）で、おおむね30〜60秒

## 確定予想オッズ（締切後の下落の学習・v2）

- 締切前に**割安に見えた組ほど、確定までにオッズが下がる**（後から票が集まる）。逆に割高な組は上がる
  （11/16 の38レース: 2車複・ワイド・2車単で締切2分前に EV1.1〜1.3 だった組は確定で平均 0.82倍、締切10分前に EV1.3以上だった組は 0.5〜0.6倍）
- 以前の区分ごとの中央値は、EV の高い区分の点数が足りず全体の値（1.0〜1.4倍）が使われ、確定予想が甘かった
  （同じ日に「確定予想EV1.10以上」とした70点は、実際の確定オッズでは平均EV 0.92）
- v2 は時点ごとの回帰: log(確定/締切前) = 券種の定数 + b1·log(EV) + b2·min(log EV, 0) + c·log(オッズ)。毎日の台帳ジョブで学習し直す
  （data/ledger/drift.json の `_model`）。組数が足りない時点は従来の表を使う

## 提案のあり方の比較（📊検証）と、今日の推奨の成績（掲示板）

- 台帳ジョブが、締切5分前・2分前の 2車複・ワイドの候補（確率0.5%以上・300倍以下）を `data/ledger/cands.csv` に積み上げる
  （今のオッズ・確定予想の期待値・実際の確定オッズ・1番人気を含むか・払戻）
- 検証レポートで、いろいろな選び方（REC・REC2・期待値の基準・オッズの上限・券種・各レース1点・下落を見込まない 等）を同じレースで比べる。
  **回収率**（運で大きく上下）だけでなく、**確定オッズでの期待値**（確率 × 実際の確定オッズ。1.00超なら長く続けて勝てる見込み）と
  **的中の予想と実際**を並べる。良かった選び方は事前登録してから確かめる（何十通りも比べると偶然よく見えるものが出るため）
- 推奨の基準は**確定予想の期待値1.00以上**（確定オッズで1.0を超える見込みがあれば推奨。資金管理の設定で変えられる）。REC3 として 20261118 から事前登録
- 掲示板の「📈 今日の推奨（REC3）」: 締切2分前に推奨した組を記録し、結果が出しだい（発走6分後から1分おき）100円ずつ買ったとして精算して、
  点数・的中（予想）・回収率をその日のうちに表示（`data/bets/recs_live.json`）

## 毎日の検証（期待値ボードの 📊検証）

- 毎朝 7:53 の台帳ジョブで `analysis/daily_report.py` が `data/ledger/daily_report.json` を作り、8:13 以降の期待値ボードに暗号化して載せる
- 内容: 前日の推奨（REC = ボードの推奨と同じ条件を事前登録）と結果、買い方ごとの累計・直近7日、
  「推奨を全部買った場合」と「実際に賭けた分」の比較、推奨にない記録の点数、予想の確率の当たり方
- ⚠（異常の自動チェック）が出るのは、運では説明できないずれの時だけ:
  確率のずれ |z|≥3（2車複・ワイド、期待値の帯ごと）、融合がオッズより当たらない（直近7日 z≥3）、
  買い方の負け確定（200点以上で90%区間の上限<100%）、記録漏れ（判定できたレースが7割未満）、精算の遅れ（2日以上）
- **ルール: 1日の結果ではロジックを直さない。⚠ が出た時だけ原因を調べて直す。月1回、十分な点数で学習し直す**

## 1点買いの記録と資金管理（期待値ボードの 🎯今の推奨・💰資金管理）

- 一覧の一番上の **🎯 今の推奨（1点・100円）** に、全場の「これから締切」の推奨を締切の早い順に出す
  （検証済みの券種のうち、確定予想の期待値が設定値（既定 1.10）以上・当たる確率 0.5% 以上・300倍以下）。各場の欄はこれまで通り
- 実際に買ったら金額を確かめて **［賭けた］**。記録は非公開の `datebase` の `data/bets/bets_YYYYMM.json` に保存（端末をまたいで共有）。
  間違えた時は **取消**（精算前のみ）
- **レースごとの即時精算**: 記録ジョブ（`live_settle.py`）が、今日のレースで［賭けた］記録があるものについて、発走6分後から
  2分おきに結果ページを見て、払戻が出たらすぐ精算し `data/bets/settled_live.json` に書いて公開する（数分で資金管理に反映、「速報」と表示）。
  取得するのは記録があるレースの結果だけ。発走から2時間で出なければ夜の精算に任せる
- 夜（その日の最後のレースの後）・毎朝の台帳ジョブ（netkeirin-ledger.yml）が `analysis/settle_bets.py` で正式に精算し `data/bets/settled.json` を書く
  （確定オッズも付く。ページは正式な方を優先）。資金管理は、結果待ちがある間は1分ごとに読み直す
  （的中 = 払戻 × 金額 ÷ 100、レース中止・欠車を含む買い目は返還、結果が無いレースは保留）
- **レース画面の各組の［賭けた］**: 推奨以外の買い目や、締切後・終わったレースの買い目も記録できる（金額を入力）
- **💰資金管理 の「＋ 買い目を追加（過去のレースも）」**: 日付・場・レース・券種・組・1点の金額を入れて記録（ボードに載っていない日のレースも）。組は「2-134」（2車単 2-1・2-3・2-4）や「3-1-456」（3連単）のように各位置に複数の車番を書くと、まとめて記録（2車複・ワイド・3連複は順番をそろえて重複を除く）。追加した後も日付・場・レース・券種・金額は残る。
  精算の時に、外れも含めて確定オッズ（的中は払戻の倍率）を付け、履歴に「確定 ○倍」と出す
- **💰資金管理**: 残高・収支・回収率・的中・最大連敗・最大の下落幅・今日の成績・残高の推移・履歴。
  設定（最初の資金・1点の金額・推奨にする期待値・1日の損失の上限・1レースの上限）は `data/bets/settings.json`。
  上限を超える記録は確認を出す
- 初回のみ（端末ごと）: GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** で
  「Only select repositories = datebase」「Contents: Read and write」だけのトークンを作り、💰資金管理 の画面に貼る。
  トークンはサイトのパスワードで暗号化して端末にだけ保存する（チャットなどには貼らない）。有効期限が切れたら作り直して貼り直す

## ガールズの補正（1番人気の番手を狙って2着を拾う傾向）

- 調べ方: `python3 analysis/girls_study.py --data <datebase>/data/2026`（結果は analysis/out/girls_study.md・公開しない）
- ガールズで1番人気が1着の時、**得点4位以下の選手が2着に来る回数は予想の約1.2〜1.25倍**、**逃げ型は約0.7倍**、得点2位は約0.9倍。
  4〜7月で推定した補正が、8〜11月（163R）でも当たり方を改善した。A級・S級では改善しないので、補正はガールズだけ
- keirin_engine.py の `girls_adjust`: 1番人気が1着の組の中だけで2着の確率を配り直す（1番人気が勝つ確率は変えない）。
  期待値ボード・通知・台帳はすべてこの補正後の確率を使い、選手メモに説明が出る
- 事前登録: G2Q110（ガールズ 2車複 EV1.10以上）・G2T110（ガールズ 2車単 EV1.10以上）。検証の点数が少ないので通知はしない（台帳で成績を見る）
- 選手ごとの傾向（各選手の「予想より勝つ・2着が多い」の過去の実績）は、その後の成績をほとんど予測しなかった（相関 ±0.02 未満）。
  オッズがすでに織り込んでいるので、補正には入れていない

## 事前登録した買い方の成績台帳（analysis/snapshot_ledger.py / .github/workflows/netkeirin-ledger.yml）

`analysis/strategies.json` に登録した買い方（全券種）を、発走前に記録したオッズ（締切2分前 C-2 が本番、締切10分前 C-10 は参考）で判定し、
翌朝に収集した実際の払戻と合わせて台帳に積み上げる。毎朝 07:53 JST に前日分を追加。

- 出力（データ用リポジトリのみ）: `data/ledger/ledger.csv`（買い目1点1行）、`data/ledger/report.md`（買い方ごとの成績）
- 登録日より前のレースは数えない。条件を変える時は新しい id で追加する（後から都合よく選ばないため）
- 過去データでの買い方の検証は `analysis/all_types_study.py`（月ごとに学習し直すローリング予測）と
  `analysis/calibrate_types.py`（券種ごとの第2段の較正）。出力は `analysis/out/`（公開リポジトリには入れない）

## 手元での実行

```bash
python netkeirin_collector.py --date 20261114 --out-dir ../datebase/data   # 非公開リポジトリの中へ
python netkeirin_collector.py --from 20261114 --to 20261120 --out-dir ../datebase/data
python tests/netkeirin_test.py   # 架空のHTMLでの単体テスト（通信なし）
```

`--out-dir` を省略するとこのリポジトリの `data/` に書き込みます。**その場合は絶対にコミットしないこと。**
手元では公開リポジトリの外（非公開リポジトリのフォルダ）を指定してください。

## 残課題

1. オッズ API はページの JavaScript から読み取った非公開の仕様なので、予告なく変わる可能性があります。
   変わった場合はログの「オッズ取得失敗」が増えます（出走表・着順・払戻はそのまま保存されます）。
2. line_type は推定値（上記）。race_type は対応表（RACE_TYPE_MAP）に無い表記だと WINTICKET と異なる表記のまま残る。
3. 天候・風速は取れない。
4. サイト構造の変更で解析が失敗した場合、ログの「失敗」件数が増えます。その時は解析処理の修正が必要です。

## 本紙の印・3連単ユーザー支持率・本紙の買い目（marks.csv / honshi_picks.csv）

出走表ページに無料で載っている「netkeirin本紙」の予想だけを保存する。有料予想（ウマい車券）には触れない。
見解の文章は著作物なので保存しない。

| ファイル | 列 | 内容 |
|---|---|---|
| data/YYYY/marks.csv | race_id, car_number, hs_mark, sup1, sup2, sup3 | hs_mark: 本紙の印（1=◎ 2=○ 3=▲ 4=△ 5=☆ 100=無印、無ければ空欄）／ sup1〜3: 3連単ユーザー支持率の1着・2着・3着（%） |
| data/YYYY/honshi_picks.csv | race_id, date, bet_type, combination, kind | 本紙の買い目を1点ずつに展開（順不同の券種は昇順）。kind は元の表記（例「フォーメーション:5→1→2,6,7」） |

- 印の参照先は marks.csv に一本化している。`parse_racecard` の行には hs_mark・sup1〜3 が入るが、racecards.csv には書かない
  （WINTICKET 由来の racecards.csv を書き換えないため）。
- 毎日の収集（collect_date）で、その日のレースの marks.csv・honshi_picks.csv も書く。
- 過去分は `netkeirin_backfill.py --marks-from 20260401 --marks-budget-min 60` で、marks.csv に無いレースの出走表ページを
  1レース1リクエストで取り直す。取得に失敗したレースは data/netkeirin_backfill.json に記録し、繰り返さない。
- 2026/04/01 の出走表ページにも印・支持率・買い目が載っていることを確認済み。支持率は締切時点の値（確定後のページで表示されるもの）。
