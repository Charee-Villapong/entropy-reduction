# SPEC — 完了の定義とユニット契約

AI組織 Issue #19「エントロピー削減の仕組み」の正本仕様。P0 で固定する。
本書と、テスト係が本書のみから先行作成した受け入れテスト（`tests/`）が、実装の完了条件になる。
実装中に本書と実装のズレが見つかったら、テストは書き換えず、ズレは仕様判断として別件に上げる（固定ルール）。

## 1. 前提（本人の決定済み事項）

- 判定は3層：①コードで拾う（判断を入れない）→ ②専用の削減係が証拠つきで判定する → ③本人が台帳で仕分け、実行は組織側が行う。
- 置き場所は専用の定期タスク「エントロピー削減タスク」（週次実行＋大作業直後の臨時実行、履歴が残る形）。
- 指標：行数・削減率は目標にしない。意味の保存率を絶対の制約とし、到達可能性100%を保つ。削減量・総量は結果として記録するだけ。
- 月次棚卸し＝検出と深い点検、本仕組み＝実行の一本窓口。棚卸しの検出結果は候補入力として受け取る。候補の提示先はタスク台帳に一本化する。
- 疎結合：下記ユニットはそれぞれ独立した tool とし、単体でテスト・差し替え・再実行できる形にする。受け渡しは JSON ファイル（本書の交換形式）で行い、境界は contracts の検証で検査できる形にする。
- 費用は0円。処理はすべてローカルで完結し、外部送信はしない（台帳への書き込みは sheet-writer 経由のみ）。

## 2. ユニット構成と契約

パッケージ `entropy_reduction`。各ユニットは純粋関数 `process(payload, config)` を持ち、CLI は `python -m entropy_reduction.<unit> <input.json> <output.json>` の形で同じ処理をファイル入出力に接続する。終了コード：0=正常、1=交換形式の違反、2=設定の誤り。

| ユニット | 役割 | 入力 → 出力 |
|---|---|---|
| `config` | 設定の読み込みと検証 | 設定ファイル → Config |
| `contracts` | 交換形式の検証 | dict → 検証（違反は ValueError） |
| `linecount` | 行数計測（共通部品。棚卸し側からも呼べる） | テキスト／パス → 行数と種別 |
| `measure` | 第1層：総量の計測 | 対象一覧＋棚卸しラン状態＋実数 → snapshot |
| `detect` | 第1層：候補の機械抽出（判断を入れない） | snapshot＋棚卸し候補＋項目一覧 → candidates |
| `judge` | 第2層：証拠つき判定 | candidates＋証拠 → judged |
| `present` | 第3層：仕分け提示（台帳の行を作る唯一の経路） | judged → ledger_rows |
| `execute` | 第3層：削減の実行（ガードつき） | 本人が仕分けた台帳行 → execution_record |
| `preservation` | 保存率テスト（合否の担保） | 確認問題セット＋回答結果＋項目の所在 → preservation_result |
| `remeasure` | 再計測と Before/After 報告 | before/after の snapshot＋preservation_result → remeasure_report |

## 3. 交換形式（すべて `"kind"` と `"version": 1` を持つ）

### snapshot（measure の出力）
- `taken_at`：計測日（YYYY-MM-DD）
- `totals`：`repos` / `code_lines` / `md_lines` / `crons` / `hooks` / `custom_skills` / `ledger_open_rows` / `open_issues`（すべて整数。総量は推移の記録用で、評価の目標ではない）
- `sources`：入力の出所の一覧（例：`{"type": "harness_run", "run_id": ..., "candidates_by_state": {...}}`）

### candidates（detect の出力）
- `items[]`：`id` / `source`（`inventory` | `weekly` | `embedding`）/ `target` / `category`（`duplicate` | `dead` | `bloat` | `other`）/ `title` / `detail`
- **判定の欄（verdict 等）を含んではいけない**（第1層は判断を入れない）。

### judged（judge の出力）
- `items[]`：`candidate_id` / `status`（`judged` | `needs_check`）/ `evidence`（`last_used` / `references` / `impact`）/ `verdict`（`delete` | `merge` | `codify` | `keep`）/ `migration_target`（統合・符号化の移行先。削除・保持では null）/ `reason`
- **証拠3点（last_used・references・impact）がすべて揃っている候補だけが `judged` になれる**。1つでも欠ければ `needs_check` に分離し、`verdict` は null とする。

### ledger_rows（present の出力）
- `rows[]`：台帳の1行分（タスク／状態／止まってる理由・前提条件／前提の確認方法／期日・次回確認日／関連リンク／最終更新の7要素）
- `judged` と `needs_check` の全候補を行にする。`needs_check` の行は前提条件の欄に「要確認（証拠不足）」と明記する。

### execution_record（execute の出力）
- `executed_at` / `items[]`（`candidate_id` / `action` / `result`）/ `counts`
- 記録に残すのは件数・名称・日付などのメタ情報のみ。ファイルの内容などの実値は含めない（他の交換形式も同じ）。

### preservation_result（preservation の出力）
- `preservation_rate`：確認問題の固定セットへの正答割合（削減後の md だけで答えさせる）
- `reachability`：元のルール項目のうち、削減後も md 内またはコード内（符号化）から辿れる割合
- `pass`：`preservation_rate` が設定の合格ライン以上、かつ `reachability` が 1.0 のときだけ true
- 確認問題セットに事故・例外系（`category` が `accident` または `exception`）の問題が1件も含まれない場合はエラーとする。

### remeasure_report（remeasure の出力）
- `preservation_pass` / `reachability` / `reduced`（`code_lines`・`md_lines` の削減量。記録のみで目標ではない）/ `totals_trend`（before/after の totals）

## 4. 設定（config）

設定はコードに直書きせず、設定ファイル（`config.local.json`、git 管理外）から読む。雛形は `config.example.json`。
必須キー：`bloat_md_lines`（肥大とみなす md 行数の入口の目安）/ `unused_days`（死蔵とみなす未使用日数の入口の目安）/ `preservation_pass_line`（保存率テストの合格ライン）/ `harness_state_dir`（棚卸しランの状態ファイルの置き場所）/ `ledger_target`（台帳の対象名）。
しきい値は「候補として拾う入口の目安」であり、それだけで何も消えない（判定と仕分けを通る）。

## 5. 完了の定義（受け入れ条件）

全体：
- **D1 疎結合**：各ユニットは単体で実行でき、交換形式さえ守れば差し替えられる。他ユニットの内部実装に依存しない。
- **D2 実行ガード**：execute は、本人の仕分け（`user_decision`）が記録された行だけを実行対象にする。仕分けのない行が1件でも混ざれば、全体を拒否して何も実行しない。
- **D3 証拠の必須化**：judge は証拠3点が揃った候補だけを `judged` にし、それ以外は `needs_check` に分離する。
- **D4 記録の非実値化**：すべての交換形式・記録はメタ情報のみを持ち、ファイル内容・接続情報・個人情報の実値を含まない。
- **D5 入力統合（二重計測の禁止）**：measure は同じ対象を重ねて数えない（対象名で一意化する）。棚卸しランの状態ファイルを候補入力として読み取れる。
- **D6 設定値化**：しきい値と合格ラインは設定ファイルから読み、設定を変えれば判定の挙動が変わる。コードへの直書きはない。
- **D7 保存率テスト**：preservation は保存率と到達可能性を計算し、合格ラインと到達可能性100%で合否を出す。事故・例外系を含まない問題セットはエラーにする。
- **D8 再計測報告**：remeasure は「保存率（合否）／到達可能性／削減量（記録のみ）／総量の推移」の4点を含む報告を出す。
- **D9 提示の一本化**：候補を台帳の行にするのは present だけ。needs_check の行には「要確認」を明記する。
- **D10 再実行可能性**：同じ入力に対する各ユニットの出力は決定的（日付フィールドを除く）。

フェーズ対応：P0＝本書の固定＋テスト先行。P1＝measure／linecount／detect。P2＝judge。P3＝present／execute（台帳連携は sheet-writer 経由）。P4＝定期タスク化。P5＝初回の一巡と QA。V-EMB（埋め込みモデル選定）は独立フェーズで、detect の重複抽出拡張の前提になる。

## 6. テストについて

- テスト係は本書のみから受け入れテストを先行作成する（実装は見ない）。正常系の要所と異常系の基本ケースを押さえる。
- 実装係はテストを書き換えずに通す。実装中に必要になった回帰テストの追加はよいが、仕様由来か実装都合かは検閲で判定する。
