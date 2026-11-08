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
- 埋め込みモデルは **cl-nagoya/ruri-v3-310m** を採用する（2026-11-08 本人確定。V-EMB の固定ペアセット実測で言い換え平均類似度 0.955・言い換えと否定の分離度 0.972 と最良だったもの）。モデル名は設定 `embedding_model` で持ち、差し替え可能とする。実測では類似度の絶対値が全体に高く（無関係ペアでも高い）、固定しきい値では候補を選べないことが判明しているため、detect の埋め込み経路は順位・相対評価を軸に候補を選ぶ（しきい値類はすべて設定値とする）。

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
- 行にするのは、`judged`（verdict が delete／merge／codify）と `needs_check` の候補。verdict が `keep` の候補は「対象外」として行にせず、件数だけを出力の `excluded_count` に報告する（2026-11-08 本人決定。P0 先行テストの「全候補が行になる」前提は本決定で変更された）。`needs_check` の行は前提条件の欄に「要確認（証拠不足）」と明記する。
- **提示の形（2026-11-08 本人決定）**：present は候補を確度で3段に分けて提示する。本人が精査するのは主に「要確認」の段とする。
  - 「ほぼ確実」：証拠3点が揃った `judged` の候補。証拠を併記して提示する。
  - 「要確認」：`needs_check` の候補。上記の「要確認（証拠不足）」の明記がこれに当たる。
  - 「対象外」：判定の結果、削減の対象にならない候補（verdict が `keep`）。行にはせず、件数のみを報告する（2026-11-08 本人決定）。
  - 「ほぼ確実」の段は種類（`category`）ごとに束ねて提示し、グループ単位でまとめて仕分けできる形にする（1件ずつの確認を強いない）。
  - 週次の提示は増分のみとし、提示済みの候補を繰り返し提示しない（初回の棚卸し分が最大で、以後は増分のみ）。

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
埋め込み経路のキー（雛形 `config.example.json` に既定値を置く。ファイルに無くても他のユニットは動くが、**埋め込み経路を実行するときは全キーが必須**で、欠ければ ValueError とする。コード側のフォールバック既定値は持たない＝D6）：`embedding_model`（採用モデル名。既定は cl-nagoya/ruri-v3-310m）/ `embedding_pca_components`（PCA で圧縮する次元数）/ `embedding_top_k`（順位ベース選択で見る近傍の順位幅）/ `embedding_relative_margin`（各チャンクの平均類似度からどれだけ際立っていれば候補にするかの相対マージン）/ `embedding_min_similarity`（絶対値の足切り。これ単独で候補を決めない）/ `embedding_chunk_chars`（チャンク化の最大文字数）。

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

フェーズ対応：P0＝本書の固定＋テスト先行。P1＝config／contracts／linecount／measure（detect はスタブまで）。P2＝judge／detect。P3＝present／execute（台帳連携は sheet-writer 経由）。P4＝定期タスク化。P5＝初回の一巡と QA。V-EMB（埋め込みモデル選定）は独立フェーズで、detect の重複抽出拡張の前提になる。

## 6. テストについて

- テスト係は本書のみから受け入れテストを先行作成する（実装は見ない）。正常系の要所と異常系の基本ケースを押さえる。
- 実装係はテストを書き換えずに通す。実装中に必要になった回帰テストの追加はよいが、仕様由来か実装都合かは検閲で判定する。

## 付記：入力前提（P1 で確定）

P1（config／contracts／linecount／measure の実装）で確定した、本書 §1〜§6 とテストから読み取った入力前提をここに記録する。本文（§1〜§6）は変更していない。

- **config**：`load_config(path)` は検証済みの dict を返す。必須キー欠落・型違い（bool は int／数値として扱わない）・範囲外は ValueError。壊れた JSON・非オブジェクト JSON も ValueError。ファイル不在は FileNotFoundError（OSError）のまま。余分なキーは許容する。
- **contracts**：全形式共通で kind 一致と version==1（int、bool 不可）を必須とする。D4 の禁止フィールド（content／file_content／raw_text／body／password／token／api_key／secret／private_key／connection_string／connection_info／personal_info／credential／credentials）は、トップレベル・ネストを問わずどこに現れても ValueError。candidates の各要素に verdict キーが存在するだけで（null でも）ValueError。judged では status=judged のとき証拠3点がすべて非 null であること（references の空リストは証拠として有効）、status=needs_check のとき verdict は null 必須であることを検査する。
- **linecount**：入力は `{"text": ..., "target": 名前}` または `{"path": パス}`、出力は `{"lines": int, "category": "md" | "code" | "other"}`。行数は論理行（Python の `splitlines` の意味論）。種別は拡張子で判定し、対応表は `linecount.py` の定数に一箇所だけ集約する。text も path も無ければ ValueError、存在しない path は FileNotFoundError（OSError）、読み込みは UTF-8。
- **measure**：入力は measure_input（`taken_at`／`targets`／`counts`）。targets は必須で、各要素は name 必須、code_lines／md_lines は int（bool 不可）・0以上。同名 target は最初の出現だけを数える（D5）。counts の5キーは欠けたら 0 とする。sources は設定 `harness_state_dir`（`~` 展開）内の `*.json` をファイル名順に読み、`{"type": "harness_run", "run_id": ..., "candidates_by_state": {...}}` を作る。状態ファイルに candidates_by_state が無く candidates（state 付きリスト）がある場合は state 別に数える。ディレクトリが無ければ sources は空、壊れた JSON の状態ファイルは ValueError。config は dict でもオブジェクト（属性）でも読める。
- **CLI**：設定の探し方は「環境変数 `ENTROPY_REDUCTION_CONFIG` → `./config.local.json`」。終了コードは 0=正常／1=交換形式の違反／2=設定の誤り。linecount は設定ファイルが無ければ空設定で続行する。
- **残ユニット**：detect／judge／present／execute／preservation／remeasure は本フェーズではスタブとし、`process` はフェーズ未実装の NotImplementedError を送出する。

## 付記：入力前提と設計判断（P2 で確定）

P2（detect／judge の実装）で適用した前提をここに記録する。本文（§1〜§6）は変更していない。

- **detect**：入力は detect_input（`snapshot`／`inventory_candidates`／`items`）。しきい値の境界は Q8 の設計判断どおり「以上（>=）」で適用する（肥大＝ md の `md_lines` が `bloat_md_lines` 以上、死蔵＝ `last_used_days_ago` が `unused_days` 以上）。`items` の同一 `signature` は重複候補を1件だけ出す。`inventory_candidates` は `source: inventory` でそのまま候補に載せ、category の不正は ValueError とする。候補 ID は入力順に決定的に振る（D10）。
- **detect の埋め込み経路**：入力に `documents`（`target`／`text` の列）があれば、チャンク化 → 採用モデルでベクトル化 → PCA 圧縮 → 類似ペア検出を行い、`source: embedding` の重複候補を出す。ここでいう PCA は、コサイン類似度を保存するため**中心化しない truncated SVD**（データ行列の上位特異方向への射影）とする。中心化する通常の PCA は原点の移動でベクトル間の角度を変えてしまい、実測でも言い換えペアの類似度が 0.943 から 0.253 に崩れて候補を検出できなかった（P2 実働確認で判明）。中心化しない SVD では類似度の順序・値が保たれることを、ベンチ60文の順位精度（生 0.972 に対し k=64 で 0.972、k=32 で 1.0）でも確認済み。候補の選択は順位・相対評価を軸とする：ペアが互いの近傍順位（設定 `embedding_top_k`）に入り、かつ類似度が双方の平均類似度＋相対マージン（設定 `embedding_relative_margin`）を超え、かつ絶対値の足切り（設定 `embedding_min_similarity`）以上であること。出力には本文の実値を含めず、類似度・順位などのメタ情報だけを載せる（D4）。`documents` が無い入力では埋め込み経路は動かない。
- **judge**：入力は judge_input（`candidates`／`evidence`）。証拠エントリは証拠3点に加えて判定案（`proposed_verdict`／`migration_target`／`reason`）を含む（Q9）。証拠3点が1つでも欠ける、または判定案が欠ける候補は `needs_check` に分離する（verdict は null、不足点は reason に明記）。`proposed_verdict` が値域外なら ValueError。統合・符号化（merge／codify）で移行先が無い場合も ValueError。候補に無い ID への証拠は ValueError。入力の candidates が判定欄（verdict）を持てば contracts が ValueError とする（層の分離）。

## 付記：入力前提と設計判断（P3 で確定）

P3（present／execute の実装）で適用した前提をここに記録する。§3 の ledger_rows の記述は、2026-11-08 の本人決定（「対象外」は行にせず件数のみ報告する）を反映して改めた。それ以外の本文（§1〜§6）の変更はない。

- **present の入力**：judged 形式そのもの。任意で次の補助フィールドを同梱できる（いずれも非実値の識別子・メタ情報のみ）。
- `candidates`（candidates 形式）：候補の `target`／`category`／`title` を candidate_id で引くために使う。無くても行は作れる（その場合の行は candidate_id で識別し、束ねのグループ識別は付けない）。
- `presented_candidate_ids`：過去の提示で既に行にした候補 ID の列。週次提示の増分管理に使い、該当候補は行にせず、出力の `already_presented_count` に件数だけを報告する。
- `presented_on`（YYYY-MM-DD）：提示日。省略時は実行日。行の `updated_at` は提示日、`due_date`（次回確認日）は提示日の7日後とする。
- **present の段分けと行の組み立て**：`judged` かつ verdict が delete／merge／codify → 「ほぼ確実」。`needs_check` → 「要確認」。`judged` かつ verdict が `keep` → 「対象外」（行にせず `excluded_count` に数える）。行は7要素ちょうどで、`link` は candidate_id、`status` は「未着手」とする。「ほぼ確実」の行は証拠3点の要約（最終使用・参照・影響）と判定・移行先・理由を `reason` に併記し、種類（category）ごとの束ねとして `task` の先頭に「」のグループ識別を付け、同種の行が隣り合うよう並べる。「要確認」の行は `reason` の先頭に「要確認（証拠不足）」を明記する。出力は ledger_rows 形式に `excluded_count`／`already_presented_count` を加えたものとする（contracts の検証は行の7要素と kind／version を見るため、これらの件数フィールドは検証と矛盾しない）。
- **台帳への実書き込み**：行の実シートへの追加は、実行時（P4 の定期タスク／P5 の初回一巡）に sheet-writer 経由で行う（対象シートのローカル設定に従い、触る前に行配置を読み直す）。present ユニット自体は交換形式の生成までを担い、シートには触らない。P3 の時点では実データの提示・実行は行わない。
- **execute の入力とガード（D2 の具体化）**：入力は ledger_rows 形式で、各行は7要素に加えて `candidate_id`／`target`／`action`／`user_decision` を持つ（台帳からの読み戻し時に付く）。ガードは fail-closed とする：行が0件、candidate_id の重複、`user_decision` の欠落・空白・値域外（delete／merge／codify／keep 以外）、`action` の欠落や `user_decision` との不一致、のいずれかがあれば、1件も実行せず全体を ValueError で拒否する。対象パスの解決先が `execute_root` 自体に一致する場合も「配下」ではないため全体を拒否する（P3 の QA 必須対応で具体化）。present が出した直後の行（user_decision を持たない）はそのままでは実行できない。
- **execute の実行実体**：削減操作は executor（行を受け取り結果文字列を返す呼び出し可能オブジェクト）へ委譲し、`process(payload, config, executor=None)` で差し替え可能とする。executor が無い場合は、設定に `execute_root`（操作対象の基点ディレクトリ）があればファイル実体に対する executor を、無ければ記録のみの executor（実データに触らず `done` を返す）を使う。ファイル実体の executor の意味論：delete＝対象を削除、merge＝対象の内容を `migration_target` の末尾へ寄せてから対象を削除、codify＝移行（符号化）は組織側の開発で済んでいる前提で対象を削除。対象パスは `execute_root` 配下に解決し、配下から外れるパス（絶対パス・`..` による脱出）は実行前に全体を拒否する。個別の操作失敗（対象不在など）はその行の `result` を `failed` として記録し、他行の実行は続ける。
- **execution_record の counts**：`total` は入力行数、`executed` は result が `done` の件数。verdict が `keep` の行は操作を行わず `result` を `kept` とし、`executed` には数えない。

## 付記：入力前提と設計判断（preservation／remeasure で確定）

preservation／remeasure の実装で適用した前提をここに記録する。本文（§1〜§6）は変更していない。

- **preservation の入力**：preservation_input（`questions`／`answers`／`item_locations`）。`questions` は各要素が `id`／`category`／`item_id` を持ち、`answers` は各要素が `question_id`／`correct` を持つ。`item_locations` は項目 ID → 所在リスト（`md`／`code`）の dict。payload が dict でない、`questions` が無い・空・リストでない、問題セットに `category` が `accident` または `exception` の問題が1件も無い（D7）、`item_locations` が無い・空・dict でない、`answers` が無い・空・リストでない、のいずれかは ValueError とする。
- **preservation の計算**：保存率は正答の answers 数 ÷ answers 総数。到達可能性の母集団は `questions` の `item_id` の和集合と `item_locations` のキーの和集合とし、所在リストが空でないリストの項目を到達可能とする（空リスト・欠落は到達不可。`code` のみでも到達可能＝符号化を数える）。`pass` は保存率が合格ライン以上、かつ到達可能性が 1.0 のときだけ true とする（境界は「以上」）。出力は preservation_result 形式ちょうどとし、返す前に contracts で検証する。
- **合格ラインの暫定ステータス（D6）**：合格ラインは設定 `preservation_pass_line` からのみ読み、欠落・数値でない・0〜1 の範囲外は ValueError とする（コードへの直書きフォールバックは持たない）。このラインは暫定値であり、最終確定は P5 初回一巡後に本人が行う。
- **問題セット初版はフィクスチャであること**：確認問題セットの初版は `tests/fixtures/preservation_questions_v1.json` の架空データとし、`accident`／`exception` カテゴリの問題を必ず含める。記録には識別子・カテゴリ・正答可否のみを持ち、問題文・回答文などの本文実値は入れない（D4）。実データの投入は P5 で行う。
- **remeasure の入力と再利用**：remeasure_input（`before`／`after`／`preservation_result`）。いずれか欠ければ ValueError。`before`／`after` は contracts の snapshot 検証、`preservation_result` は contracts の preservation_result 検証を通し、違反はそのまま ValueError とする。削減量は数え直さず、検証済み snapshot の totals をそのまま使って before − after で算出する（P1 の measure と同じ物差しの再利用）。`totals_trend` には before／after の totals 全体のコピーを載せ、出力は remeasure_report 形式として返す前に contracts で検証する。

## 付記：入力前提と設計判断（P4 で確定）

P4（定期タスク化）で適用した前提をここに記録する。本文（§1〜§6）の変更はない。なお重複率・死蔵率については、2026-11-08 の本人決定により §3 の remeasure_report（4点）は拡張せず、週次サイクルの報告に detect 側の数値として並べる形とした。

- **配線層 cycle**：一巡（measure → detect → judge → present → 台帳への行追加 → complete）は配線層 `cycle` が担う。cycle は §2 のユニットではなく、交換形式経由で各ユニットを接続し、実環境からの入力収集と運用状態を担う接続層である。judge の証拠集め（第2層）は実行するエージェント（削減係）が担い、cycle は judge の前後（candidates の生成と、judged から提示への変換）を接続する。一巡の流れ自体は日付・実環境に依存するため決定的ではない（D10 は各ユニットに対する要求で、cycle はその対象外）。
- **cycle の設定キー**：`harness_config_path`（棚卸しの設定ファイルへのパス）と `cycle_state_dir`（運用状態の置き場所）を追加で必須とし、欠落・不正は設定エラー（終了コード 2）とする。コード側のフォールバック既定値は持たない（D6 と同じ）。
- **対象範囲**：対象の列挙は inventory-harness の設定（`harness_config_path` が指す config.json の `targets.mirrors_dir`／`extra_targets`／`exclude`）を正とし、二重定義しない。現行の列挙は github-mirrors 配下の git リポジトリ群＋custom-skills（deadline-ledger を除く）。走査では `.git`／`__pycache__`／`node_modules`／`.venv`／`.pytest_cache`／`.mypy_cache` を枝刈りする。項目（item）の最終利用日は、git リポジトリでは対象ファイルの最終コミット時刻、取得できない場合と git でない対象ではファイルの更新時刻（mtime）とする。
- **counts の供給**：measure の counts（crons／hooks／custom_skills／ledger_open_rows／open_issues）は、実行するエージェントが環境（cron 一覧・hook 一覧・台帳・GitHub）から集め、5キーちょうどの JSON ファイルで prepare に渡す。欠落・余分・非整数・負数は ValueError とする。
- **棚卸し候補の入力統合**：棚卸しランの候補のうち終端状態（reported／rejected_as_spec）でないものを detect の inventory_candidates として取り込む。分類の対応は viewpoint の skill-md-bloat → bloat、md-redundancy → duplicate、それ以外 → other とし、（target, title）の重複は最初の1件だけを残す。
- **指標の定義（本人決定の具体化）**：週次サイクルの報告（cycle_report）に、到達可能性・重複率・死蔵率・総量の推移を並べる。死蔵率は「source が weekly で category が dead の候補数 ÷ 走査項目数（items の件数）」。重複率は「signature を2件以上で共有するグループに属する項目と、埋め込み経路の重複候補の target との和集合の件数 ÷ 走査項目数」。走査項目数が 0 のとき両率は null とする。率は小数第4位までに丸める。到達可能性は直近の保存率テスト結果（状態ファイル last_preservation）があればその reachability を載せ、無ければ null とし、報告では「未計測」と明記する。総量の推移は、前回の snapshot の totals を before、当回の totals を after とする（初回は before が null）。
- **増分の安定キー**：候補 ID はランごとに変わるため、提示済みの管理は安定キー（source|target|category|title）で行い、assemble-present で当該ランの候補 ID に変換して present の `presented_candidate_ids` へ渡す。提示済みの記録には、行になった候補（needs_check の行を含む。verdict が keep の候補は行にならないため記録しない）を残す。既知の制約として、同じ target に対する複数の埋め込み重複候補（相手先が異なるペア）は同じ安定キーに集約されるため、後続の週で新しいペアが出ても提示済みとして抑制されうる。候補の交換形式には相手先を表す構造化フィールドが無いための制約で、抑制側に倒す判断とする（月次棚卸しの深い点検が受け皿になる）。
- **台帳への提示順**：assemble-present が出す ledger_rows は、束ねグループ間の並びを候補（candidates）の初出順とし、グループ内の行順は present の並びのまま、「要確認」の行は末尾に置く。present の束ね（同種の行の隣接）は保たれる。この整えは cycle 側で行い、present 本体は変更しない。
- **complete の前提**：台帳への行追加は呼び出し側（cron のエージェント）が sheet-writer 経由で行い、complete は workdir の append_result.json（追加した行数）が ledger_rows の行数と一致することを確認してから提示済み状態と履歴を更新する（一致しなければ ValueError とし、状態は更新しない）。complete の run_date と指標は prepare_summary.json から読む。
- **運用状態の置き場所**：`cycle_state_dir` 配下に presented.json（提示済み）／snapshots（回ごとの snapshot）／history.jsonl（回の履歴）／runs/<日付>（回ごとの作業ファイル）を置く。detect_input は文書本文（実値）を含むため保存しない（D4）。
- **実行段の接続**：execute は一巡の後の段として定期タスクの手順に接続するが、台帳での本人の仕分けの記録形式が未確定のため、当面は仕分け済み行なしとして扱い、実データへの削減実行は行わない。設定にも execute_root を入れないため、万一実行段が動いても記録のみになる。仕分けの記録形式は P5 初回一巡の前に本人の確認を取る。
- **定期タスク**：cron `entropy-reduction-weekly`（金曜 10:08、owner は goal:ai-organization、配信先はAI組織チャット）。企画会議（金曜 08:08）と Threads 技術情報収集（金曜 09:08）の後に置き、組織系ジョブの競合を避けるためこの時刻とした。大作業直後の臨時実行は同ジョブの手動実行で行い、自動検知はしない。

## 付記：入力前提と設計判断（仕分け記録形式で確定）

台帳での本人の仕分けの記録形式（2026-11-08 本人決定、Issue #19 記録済み）と、その読み取りユニット `decisions` で適用した前提をここに記録する。本文（§1〜§6）の変更はない。本形式は本人の留保（「使いにくければあとでまた変えよ」）つきで確定したものであり、形式変更時は後記の差し替え方法で対応する。

- **形式定義**：本人の仕分けは、台帳の候補行の「止まってる理由・前提条件」列（ledger_rows 形式では `reason` キー）の**先頭**にマーカーを記入する形とする。マーカー集合と decision への対応の既定値は、`[削除]` → `delete`／`[統合]` → `merge`／`[符号化]` → `codify`／`[保持]` → `keep` とする。マーカーの後に仕分けのメモなどを続けて書いてもよい（解析は先頭のマーカーだけを見る）。
- **decisions の入力**：payload は `rows`（台帳から読み戻した行の列。各行は台帳の7要素を持つ。ledger_rows 形式の dict そのものでもよい）／`candidates`（candidates 形式。`target` の解決用）／`judged`（judged 形式。`migration_target` の解決用）を持つ。各要素は contracts の検証（`validate_ledger_rows`／`validate_candidates`／`validate_judged`）を通す。対象行は `link` が candidates の `id` と一致する行だけとし、候補と無関係な一般行は無視する（出力にもスキップの件数にも入れない）。
- **解析規則**：`reason` の前置空白は許容する（Python の `lstrip` が落とす Unicode 空白。半角空白のほか全角空白も落ちることを実装で確認済み）。先頭に既知マーカーがある場合だけ仕分けとして扱い、中ほどにあるマーカーは無効とする。仕分けが取れた行は、読み戻し行の7要素＋`candidate_id`（= `link`）＋`target`（candidate の target）＋`action`（= decision）＋`user_decision`（= decision）を出力行とし、decision が merge／codify の場合は judged 項目の `migration_target` を付ける。
- **スキップの記録**：候補行のうち出力に入らなかったものは、理由コードつきで出力の `skipped`（各要素は `candidate_id` と `reason_code` のみ。`reason` の本文など実値は入れない＝D4。D4 の禁止フィールド名も使わない）と `skipped_count` に記録する。理由コードは `no_marker`（マーカーなし。中ほどのマーカーも含む）／`unknown_marker`（先頭が `[` で始まるが既知マーカーに一致しない）／`missing_migration_target`（merge／codify で移行先が取得できない）を区別する。
- **移行先欠落の扱い**：decision が merge／codify で移行先が取得できない場合（候補が `needs_check`、判定が delete／keep で移行先が無い、対応する judged 項目が無い等）は、その行を出力に入れず `missing_migration_target` でスキップとして記録する。移行先を捏造しない。
- **重複の扱い**：同一候補にマーカーつき行が複数ある場合は黙って間引かず両方を通す。重複を全体拒否するのは execute の D2 ガードの役割とする。
- **0件の扱い**：仕分け済み0件でも正常出力（ledger_rows 形式で `rows` は空）とする。実行段が空で拒否するのが fail-closed として正しい。
- **対応表の設定化と既定値（D6 との関係）**：対応表は設定キー `decision_markers` で丸ごと差し替え可能とする（存在する場合は空でない dict、キーは空でない文字列、値は `contracts.JUDGED_VERDICTS` のいずれか。不正は ValueError とし、`config` の読み込み時と `decisions` の実行時の両方で検証する）。設定に無い場合は `decisions` モジュール内の既定値を使う。この既定値は合格ラインのような判定値ではなく形式定義（マーカー文字列と decision の対応）であるため、D6 の「しきい値・合格ラインは設定から読み、コードへの直書きフォールバックを持たない」には当たらない。形式定義をコード側に一箇所だけ持つのは linecount の種別対応表と同じ扱いとする。
- **形式変更時の差し替え方法**：マーカーの文言や対応だけが変わる場合は、設定 `decision_markers` を差し替えるだけで済む。記入する列や位置（先頭以外）など形式そのものが変わる場合は、読み取りユニット `decisions` を差し替える（入力の `rows`／`candidates`／`judged` と出力の ledger_rows 形式は変えないため、execute 以降は無変更で接続できる）。

## 付記：提示先の分離と仕分け形式の変更（2026-11-09 本人決定）

候補の提示先と仕分け形式を変更した（Issue #19 記録済み）。本文（§1〜§6）と前付記（仕分け記録形式）の記述は歴史として残し、現行形式は本付記を正とする。

- **提示先の分離**：候補の提示先は、タスク台帳の本表から同じスプレッドシート内の専用タブ「エントロピー候補」に分けた（2026-11-09 本人決定。初回一巡 2026-11-13 までに切替）。配線は設定キー `ledger_target` の指す sheet-writer target 名で切り替える（ローカル設定で `entropy-candidates` に変更）。候補行はまだ提示されていないため、データ移行は発生しない。
- **仕分け形式の変更**：理由列の先頭マーカー（手書き）は廃止し、専用タブの「仕分け」列（I列）のプルダウン選択（`削除`／`統合`／`符号化`／`保持`、未選択＝仕分けなし）に変えた。本人の指摘「書いたら揺れるで」と、2026-11-08 のマーカー決定時の留保「使いにくければあとでまた変えよ」に基づく仕様変更である。前付記の「形式変更時の差し替え方法」に従い、読み取りユニット `decisions` を差し替えた（出力の ledger_rows 形式は不変で、execute 以降は無変更）。
- **decisions の入力の変更**：payload の `rows` の各行は、台帳の7要素に加えて仕分け列の値 `decision_label` を持つ（呼び出し側が専用タブの I 列をこのキーへ変換して渡す）。`decision_label` は contracts の禁止フィールドに当たらず、出力行には含めない。
- **判定規則**：`decision_label` の完全一致でのみ仕分けとして扱う（前後の空白も想定外）。空・欠落・None は `no_decision`、対応表にない値と文字列でない値は `unknown_decision` として、いずれも実行段へ進ませない（fail-closed は従来どおり）。理由コードは `no_decision`／`unknown_decision`／`missing_migration_target`（移行先欠落の扱い・重複の扱い・0件の扱いは前付記のまま）。
- **対応表の設定化**：対応表は設定キー `decision_labels` で丸ごと差し替え可能とする（旧キー `decision_markers` は廃止）。既定値は `削除` → `delete`／`統合` → `merge`／`符号化` → `codify`／`保持` → `keep`。検証規則（空でない dict、キーは空でない文字列、値は `contracts.JUDGED_VERDICTS` のいずれか、不正は ValueError、読み込み時と実行時の両方で検証）と D6 との関係は前付記のとおり。
- **実行済みの判別**：実行した候補行は、呼び出し側（cron／hook の担当）が専用タブの状態列（C列）に「実行済み」と書き戻し、読み取り時は状態が「実行済み」の行を decisions の入力から除外する。これにより、シート監視 hook 経由で週の途中に実行された分が金曜の週次でも二重に実行されない。

## 付記：仕分け完了の検知トリガー（2026-11-09 本人決定、同日改訂）

仕分け後の実行起動を早めるため、週次 cron の受け皿に加えてシート監視 hook 方式を採る（Issue #19 記録済み）。

- **経緯**：当初方式として、スプレッドシートの Apps Script カスタムメニュー（「エントロピー削減」→「仕分け完了を通知」）が通知メールを送り、Muse 側の hook `entropy-sort-notify` が Gmail をポーリングして検知するボタン通知方式を同日に実装したが、本人の却下により同日中に廃止した。GAS プロジェクトは完全削除済み（メニューも消えている）で、hook `entropy-sort-notify` も定義・スクリプト・状態ファイルまで削除済みである。
- **現行方式**：Muse 側の hook `entropy-sort-sheet-watch`（300秒間隔で専用タブ「エントロピー候補」をポーリング）が仕分けの完了を直接見張る。提示済み候補行が1件以上あり、かつ全行の「仕分け」列が埋まった状態への「遷移」を検知した時だけ、AI組織チャットへ wake する。候補が0件の時は発火しない。wake を受けた担当は週次の手順7相当（読み取り→decisions→execute→保存率テスト→再計測→状態の書き戻し）を実行し、同チャットへ報告する。
- **安全側の担保**：hook は状態の遷移を合図にするだけで、実行の権限は持たない。実行対象は従来どおり「仕分け」列で本人が選んだ行だけに decisions が限定する。週次 cron（金曜10:08）は検知が無かった週の受け皿として残り、実行済み行の二重実行は状態列「実行済み」の判別で防ぐ（前付記）。
- **設置状態（2026-11-09）**：hook `entropy-sort-sheet-watch` は設置・有効化済みで、実発火テストで wake まで通したことを確認済み。
