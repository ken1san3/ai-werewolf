# 公開前チェックリスト

このリポジトリは **作成中は非公開、完成後に成果物として公開**する予定。
Private → Public に切り替える前に以下を確認する。

## 法務

- [ ] `LICENSE` を追加する
      現状は未設定であり、**法的には全権利留保**。他人は利用も改変もできない状態。
      成果物として見せるだけなら問題ないが、フォークや流用を許すなら必要。
      MIT が最も一般的で短い。
- [ ] LICENSE の著作権者名を入れる（本名を入れる方針）
      **公開リポジトリでは誰でも見られる**点に注意。
      本名を出したくない場合は GitHub ユーザー名でも法的には成立する。
- [ ] `README.md` の「ライセンス」節を実際の内容へ更新する
- [ ] 参照実装のディスクレーマーが残っているか確認する
      （人狼ジャッジメント / そらいろ株式会社とは無関係である旨）
- [ ] `Docs/ai/spec/JUDGMENT_REFERENCE.md` が事実の要約に留まっており、
      公式ヘルプの原文を大量に転載していないか確認する

## 個人情報・秘密情報

- [ ] **git の author 情報を自分のものへ直す**
      現在の履歴は `ken <ken@local>` になっている。このままだと
      GitHub 上でコミットが自分のアカウントに紐づかず、草も生えない。
      公開前に GitHub の登録メール（noreply メールでも可）へ設定し直す。

```bash
git config user.name  "<GitHub の表示名>"
git config user.email "<ID>+<username>@users.noreply.github.com"
```

      過去の履歴も直す場合は `git filter-repo --mailmap` などを使う。
      個人プロジェクトなので、直さずに「ここから自分の名義」でも実害は小さい。
- [ ] `git log --format='%an %ae' | sort -u` で、意図しない名前・メールが
      履歴に入っていないか確認する
- [ ] APIキー・トークン・`.env` が履歴に含まれていないか確認する
- [ ] `_to_delete/` を削除する（`.gitignore` 済みだが実体が残っている）
- [ ] ローカル絶対パスが混入していないか確認する

```bash
git log --format='%an %ae' | sort -u
git grep -nEi "api[_-]?key|secret|token|password" -- . ':!Docs' || echo "clean"
grep -rn "C:\\\\Users\|/home/[a-z]" --include='*.py' --include='*.md' . | head
```

## 見せる前提の整備

- [ ] CI が緑になっている（`.github/workflows/ci.yml`）
- [ ] README の「現在の状態」が最新（Phase / テスト数）
- [ ] `python -m pip install -e ".[dev]" && python -m pytest` が
      クリーンな環境で通ることを確認する
- [ ] リポジトリの Description と Topics を設定する
      （例: `werewolf`, `llm`, `multi-agent`, `game-server`, `python`）
- [ ] AI エージェントによる開発であることを README に明記してあるか確認する
      `Docs/ai/` と `AGENTS.md` が公開される以上、隠すことはできない。
      **役割分担と運用設計そのものを見せる**方が評価される想定で書いてある。

## 就活で見せる場合に効く点

読み手が短時間で判断するのは「動くか」ではなく「なぜそう作ったか」なので、
以下が入口になるよう README を構成してある。

- `Docs/ai/decisions/`（D001〜）— 却下した案とその理由も残している
- `Docs/ai/SPEC_REVIEW.md` — 元仕様の欠陥を自分で見つけて直した記録
- `Docs/ai/REVIEW_INBOX.md` — 指摘と修正の往復
- `Docs/ai/RUNBOOK.md` — 3つの指示だけで回る開発運用

口頭で説明できるようにしておくと強い箇所:

- なぜ役職属性を5軸に分けたか（狂人・大狼・妖狐の反例）
- 公開死因をフェーズから導出した理由（対応表方式だと追記漏れが漏洩になる）
- 猫又の道連れ条件を絞ることで無限ループ対策が不要になった経緯
