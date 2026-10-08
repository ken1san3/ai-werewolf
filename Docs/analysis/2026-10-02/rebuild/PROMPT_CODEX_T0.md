<!--
Codex（実行役）への最初のプロンプト（T0: 巻き戻し）。
使い方: Codex で <repo-root> を開き、下の「---- ここから ----」より下をそのまま貼り付ける。
Claude（監督役）経由で渡す場合も同じ本文を使う。
入力資料の保存場所: <repo-root>\logs\rebuild_materials_2026-10-02\（Gitの対象外。ブランチを切り替えても消えない）
-->

---- ここから ----

<role>
あなたはこのプロジェクトの実行役（実装・テスト・ゲーム実行）です。
計画と確認はユーザーと監督役（Claude）が行います。指示された範囲だけを作業し、結果を正確に報告してください。
</role>

<override>
<repo-root> にある既存の AGENTS.md は、廃止する旧運用ルール（6役割、Design Gate、独立レビューの連鎖、
task packet、ai_status.py の実行など）です。このタスクでは旧 AGENTS.md と Docs/ai 配下の運用文書には従わず、
このプロンプトの指示に従ってください。新しい AGENTS.md は、このタスクの中で作ります。
</override>

<task>
再構築の第0段階（T0）を行う。
旧状態をGitタグで保存し、main から新しいブランチ rebuild/simple-agent を作り、
マニフェストに従って旧AIクライアント・旧スクリプト・旧運用文書を作業ツリーから外し、
新しい AGENTS.md・README.md・ROADMAP.md・WORKLOG.md を置き、残したテストがすべて通ることを確かめて、
commit と push までを行う。

作業場所: <repo-root>（現在 experiment/speech-act-kind-first-20260919 をチェックアウト中。未追跡ファイルが多数ある）

入力資料（読み取りのみ。編集・削除・移動しない）: <repo-root>\logs\rebuild_materials_2026-10-02\
- rebuild\rebuild_manifest_main_5a5635f.tsv … 全ファイルの扱い（列: path / disposition / lines / reason。disposition は KEEP / EDIT / DELETE）
- rebuild\REBUILD_MANIFEST.md … 人が読む版
- rebuild\AGENTS_NEW.md、rebuild\README_NEW.md、rebuild\ROADMAP_NEW.md … 新しい文書の原稿
- フォルダ全体 … 新ブランチへコピーする分析資料
</task>

<user_decisions>
- 新ブランチ rebuild/simple-agent への commit: 許可（1回にまとめる）
- push: 許可。対象は rebuild/simple-agent ブランチと、手順2の4つのタグだけ。通常の push のみ（force push は不可）
- main と他の既存ブランチ: 変更しない（commit・merge・push・削除をしない）
- 初期構想書 Docs/ai/spec/AI_WEREWOLF_CODEX_HANDOFF.md: 外す（マニフェストどおり）
- ai_client/network と ai_client/world: 外す（マニフェストどおり）
</user_decisions>

<steps>
1. 事前確認（読み取りのみ。ここでは何も変更しない）
   - 上の入力資料がすべて読めること（TSV は 1,468 行＋見出し1行）。
   - `git -C <repo-root> status --porcelain=v1` に、追跡ファイルの変更（"??" 以外の行）が無いこと。
   - `git -C <repo-root> remote get-url origin` が https://github.com/ken1san3/AIwolf.git であること。
   - 次のブランチ先頭が一致すること:
     main = 5a5635ff52d5f38cfcb08f80ddce69d18a290259
     experiment/speech-act-kind-first-20260919 = ee80895063c98f4e689133fb7fe5ef6ed675f5ed
     codex/context-audit-t575 = 4df84c6b6cccab4e774430d35f3bafa5d4adb0e1
     fix/capture-catchup-20260921 = b425cd12b8d5484054cd6dd2bb0861275077a764
   - 下記のタグとブランチ rebuild/simple-agent が、ローカルにも origin にもまだ存在しないこと（`git ls-remote origin` で確認）。
   - 未追跡ファイルの一覧（`git status --porcelain=v1 --untracked-files=normal` の "??" 行）を控えておく。
   どれか一つでも違えば、何も変更せずに止まり、違いをそのまま報告する。

2. 注釈付きタグを作る（この時点ではまだ push しない）
   - archive/2026-10-02-main → 5a5635ff52d5f38cfcb08f80ddce69d18a290259
   - archive/2026-10-02-experiment → ee80895063c98f4e689133fb7fe5ef6ed675f5ed
   - archive/2026-10-02-codex-t575 → 4df84c6b6cccab4e774430d35f3bafa5d4adb0e1
   - archive/2026-10-02-capture-catchup → b425cd12b8d5484054cd6dd2bb0861275077a764
   メッセージ: "Archive before the 2026-10 rebuild"

3. ブランチを作る
   - `git switch -c rebuild/simple-agent 5a5635ff52d5f38cfcb08f80ddce69d18a290259`
   - 切り替えに失敗したら止まって報告する（未追跡ファイルを消したり退避したりして解決しない）。

4. マニフェストの DELETE 行を外す
   - TSV の disposition が DELETE の path を一時ファイルに書き出し、`git rm -q --pathspec-from-file=<一時ファイル>` で外す。
   - 一時ファイルは作業ツリーの外（%TEMP%）に作り、終わったら消す。
   - KEEP / EDIT の行は外さない。

5. 分析資料をコピーする
   - 入力資料フォルダ全体を、新ブランチの Docs/analysis/2026-10-02/ へコピーする（元のフォルダは残す）。
   - Windows では docs と Docs が同じフォルダになるため、必ず大文字の Docs を使う。

6. 文書と設定を書き換える
   - AGENTS.md ← rebuild\AGENTS_NEW.md の内容で全面置換
   - README.md ← rebuild\README_NEW.md の内容で全面置換
   - ROADMAP.md（新規、リポジトリ直下）← rebuild\ROADMAP_NEW.md の内容
   - WORKLOG.md（新規、リポジトリ直下）: 見出し「# 作業記録」と、このT0の記録を1〜3行（日付、やったこと、テスト結果）
   - pyproject.toml: `[tool.setuptools.packages.find] include` を `["server*", "ai_agent*"]` に、
     pytest の markers は `completion` だけ残して `windows_private` を削除
   - tests/conftest.py: `_COMPLETION_TESTS` を tests/test_phase2_completion.py の1件だけにする（他の行は削除）
   - .gitignore: `games/` を追加
   - Docs/ai/TEST_POLICY.md: 見出し「## Phase 6 テストアーキテクチャ［Phase 6］」から次の「## 」見出しの直前までを削除（他は変えない）

7. 確認する
   - `python -m pytest -q` を実行。期待値は 204 passed / 661 subtests passed（同じ手順を別のコピーで試して確認済み）。
     tests/test_phase2_completion.py は負荷で時々落ちるので、それだけ失敗したら1回だけ単独で再実行し、両方の結果を報告する。
   - `git ls-files` の集合が「TSV の KEEP と EDIT の path」＋「ROADMAP.md、WORKLOG.md、Docs/analysis/2026-10-02/ 配下」と一致すること。
     差があれば一覧で報告する（2026-10-02 時点の想定: KEEP+EDIT 126件＋新規2件＋分析資料43件＝171件）。
   - Python ファイルに `ai_client` や `scripts` の import が残っていないこと（`git grep -nE "^\s*(from|import)\s+(ai_client|scripts)\b" -- "*.py"` が空）。
   - 未追跡ファイルの一覧が手順1で控えたものと同じであること（消えたもの・増えたものが無い）。
   ここで一つでも期待と違えば、commit・push をせずに止まって報告する。

8. commit と push
   - commit メッセージ1行目: "Rebuild T0: archive the old AI client and process docs, keep the game core"
   - 本文: 削除・書換え・新規の件数とテスト結果を数行で
   - `git push -u origin rebuild/simple-agent`
   - `git push origin archive/2026-10-02-main archive/2026-10-02-experiment archive/2026-10-02-codex-t575 archive/2026-10-02-capture-catchup`
   - `git ls-remote origin rebuild/simple-agent "refs/tags/archive/2026-10-02-*"` で、ブランチ1つとタグ4つが origin にあることを確かめる。
   - push が認証やネットワークで失敗したら、再試行や別の方法での回避をせず、ローカルの commit とタグが残っていることを確かめて報告する。
</steps>

<action_safety>
- このタスクの範囲外の変更をしない。リファクタリング、整形、ついでの修正をしない。
- 未追跡ファイル（logs/、.pytest-*、.review-*、.tmp/、.claude/ など）を削除・移動・上書きしない。入力資料フォルダも読むだけにする。
- 他のworktree（<repo-root>\.claude\worktrees\*、<repo-root>\logs\t448-quality-cycles\ci-capture-worktree）と既存ブランチに触らない。
- `git reset --hard`、`git clean`、rebase、force push、タグやブランチの削除、main への push・merge をしない。
- LLMサーバの起動、ゲームの実行、GitHub Actions、pip install は行わない。
- 手順の途中で想定と違う状態になったら、その時点で止まり、何をして何をしていないかを報告する。自分で回避策を作らない。
</action_safety>

<missing_context_gating>
リポジトリの状態を推測しない。必要な事実はコマンドで確かめ、確かめられないことは「未確認」と書く。
</missing_context_gating>

<verification_loop>
報告の前に、手順7と手順8の各確認を実際に行い、結果が期待どおりかを見直す。期待と違う項目は成功と書かない。
</verification_loop>

<structured_output_contract>
日本語で、次の順に短く報告する。
1. 結論（完了／途中で停止。停止ならその理由と、どの手順まで済んだか）
2. 作ったタグとブランチ（名前とコミット）
3. 件数: 削除したファイル数、書き換えたファイル、新規ファイル
4. テスト: 実行コマンドと結果（passed/failed/subtests、所要時間）。再実行した場合は両方
5. 確認項目の結果（ls-files の一致、import の残り、未追跡ファイルの変化）
6. commit のハッシュと push の結果（origin にあるブランチとタグ）
7. 想定外だったこと、やらなかったこと
</structured_output_contract>
