競輪ロジック v104（実績学習モデル × 市場融合 × 一括ケリー）

【ファイル構成】
1. index.html
   → ロジック本体です。Chromeなどのブラウザで開いて使用してください。
     GitHub Pages を有効にすると https://london2311.github.io/saikyoukeirinlogic/ で開けます。

2. 使用説明書_競輪ロジックv104.md
   → 基本操作と、v103/v104 統合エンジンの見方の説明書です。

3. 検証結果_概要.md
   → v104 統計モデルの検証結果（過去1,470レース）と v102 の検証結果の概要です。

4. analysis/
   train_model.py … 過去データからモデルを学習・検証するスクリプト
   model_v104.json … 学習済み係数（index.html に埋め込み済み）
   report_v104.md … 検証レポート（全数値）

5. tests/engine_test.js … エンジンの自動テスト（node tests/engine_test.js）

【注意】
本ツールは的中・利益を保証するものではありません。
投票は自己責任で、無理のない範囲で行ってください。
