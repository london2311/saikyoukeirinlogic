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
そこで各レースの **発走15分前・10分前・5分前・4分前** に、全券種（3連単・3連複・2車単・2車複・ワイド）のオッズを1回ずつ記録する。

- 保存先: データ用リポジトリの `data/YYYY/odds_snap/snap_YYYYMMDD.csv`（公開側には何も書かない）
- 列: race_id, date, race_number, snap（T-15 / T-10 / T-5 / T-4。2026/11/16 は T-12 / T-6 も）, snap_at（実際に取得した時刻）, start_at, official_dt,
  bet_type, combination, odds（ワイドは最低）, odds_max, popularity, absent
- 1日を3つの時間帯（08:13〜13:30 / 13:07〜18:50 / 18:41〜24:20 JST）に分けて実行。1時間ごとにコミット
- 1レースあたり4リクエスト＋出走表1回＋レース一覧の再取得（30分ごと）。間隔は3秒のまま
- 予定より3分以上遅れた記録は取らない（一番遅い4分前は取る）。途中から再開しても記録済みは取り直さない

## 過去分の補充（netkeirin_backfill.py / .github/workflows/netkeirin-backfill.yml）

`data/YYYY/races.csv` のレース数が120未満の日（2026/07 以降）を、古い日から順に補充する。
1日4回、1回あたり約2時間。取得済みのレース（WINTICKET 由来を含む）は取り直さない。
補充を試みた日は `data/netkeirin_backfill.json` に記録し、繰り返さない。全部埋まれば何もせず終わる。

## 通知メール（netkeirin_alert.py）

締切前オッズの記録（netkeirin-snapshot）の中で、発走5分前・4分前のオッズで `analysis/strategies.json` の
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

締切前オッズの記録（15分前・10分前・5分前・4分前）のたびに、全券種・全組の「当たる確率（v105 融合）・今のオッズ・
期待値（EV）・この倍率以上なら買い」を計算し、**AES-256-GCM で暗号化**してこのリポジトリの `site-data` ブランチに置く。
閲覧ページ `https://<ユーザー名>.github.io/saikyoukeirinlogic/live/` でパスワードを入れると、ブラウザの中だけで復号して表示する。

- 設定: Secrets に `SITE_PASSWORD`（**16文字以上を推奨**、12文字未満なら作らない）を登録する
- 鍵は PBKDF2-SHA256（20万回）。塩は日ごとに作り直す。暗号化されたファイルは公開の場所にあるが、パスワード無しでは読めない
- `site-data` ブランチは毎回1コミットに作り直して強制 push する（履歴をためない）。1〜2分ごとに更新
- 場ごとのタブ → レース → 時点（15/10/5/4分前）を切り替え。券種ごとに期待値の高い順（既定は確率0.5%以上・300倍以下の上位15組、全組も表示可）
- ★ = 事前登録した買い方（strategies.json の alert）に該当。2車複・ワイド以外は過去の検証で負けていたため「参考」表示
- パスワードを変えた場合は、次の時間帯の実行から新しいパスワードで読めるようになる（その日の分は作り直し）

## 事前登録した買い方の成績台帳（analysis/snapshot_ledger.py / .github/workflows/netkeirin-ledger.yml）

`analysis/strategies.json` に登録した買い方（全券種）を、発走前に記録したオッズ（4分前が本番、10分前は参考）で判定し、
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
