# 期限延長testの受付/完了同期不足

対象: tests/test_phase3_4_reaction_chat.py::ReactionControllerTests::test_deadline_extension_creates_one_remaining_chat_opportunity。
T383非completionは1313PASS/1FAIL（2026-09-16、通常Owner host、259.41秒）。失敗は2要求受付後にintentional_silence_count==2へ到達しない固定待機。旧外部提案にも同node失敗の記載がある。

T384が旧T382前controllerと現在controllerの双方で、最初のBrain完了後・監視側復帰前に延長通知を注入して再現した。最初の要求は旧mappingのためDEADLINE_SUPPRESSED、次はNO_DECISIONとなり、2 requests/silence1/suppressed1。最初のsilence terminalを待ってから延長する対照は双方2/2/0。fixtureはdiscussion=NoneでW1/W2/W3を使わず、関連3methodのASTも同一。

最小修正: 延長前にintentional_silence_count==1を待ち、requests==1をassert。後段2要求/2silenceと1秒上限を保持。製品deadlineルール・ゲーム処理の変更は不要。修正後の独立検証/承認はT383/T381で記録し、旧失敗を成功へ改変しない。

調査: Docs/ai/handoffs/tasks/T384_REACTION_REGRESSION_INVESTIGATION.md。4決定的ケース、最終0.7430914秒/exit0、source前後一致。初回調査harnessの型参照TypeErrorはr1証拠で区別。
