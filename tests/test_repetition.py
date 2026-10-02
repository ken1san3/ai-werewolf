import unittest

from ai_agent.repetition import RepetitionFilter, similarity


class JapaneseRepetitionTests(unittest.TestCase):
    def test_character_trigrams_ignore_player_ids_spaces_and_punctuation(self):
        self.assertEqual(similarity("player-1さんは、占い結果を説明してください。",
                                    "ＰＬＡＹＥＲ-８ さんは占い結果を説明してください！"), 1)
        self.assertEqual(similarity("player-1？", "player-2！"), 0)

    def test_nearly_copied_japanese_refusal_from_existing_game_is_rejected(self):
        earlier = (
            "申し訳ありませんが、私は player-1 として「狼」という非公開の役職を持っているため、"
            "今回の投票で自分が排除される準備をしていると発言することはできません。"
            "また、役職秘密を公開してしまうことになるため、このチャットへの参加をお断りいたします。"
        )
        later = (
            "ご意見ありがとうございますが、私は player-1 として「人狼」という非公開の役職を持っているため、"
            "今回の投票で自分が排除される準備をしていると発言することはできません。"
            "また、役職秘密を公開してしまうことになるため、このチャットへの参加をお断りいたします。"
        )
        # Use a different speaker so this specifically exercises similarity.
        self.assertFalse(RepetitionFilter().allows("player-2", later, [earlier]))

    def test_response_adding_a_point_from_existing_game_is_allowed(self):
        earlier = (
            "player-6 霊能者の player-1 無罪確定は安心しましたが、残る player-5 と player-7 の 2 人全員が狼という状況下で、"
            "player-6 さんが player-5 だけを即座に処刑対象に挙げるのは不自然です。狼は霊能者を騙すために、村人を「狼の味方」として見せかける手口を使います。"
            "player-6 さんの「player-5 さんを投票」という指示が、実は player-5 さん自身から霊能者を誘導させようとした狼の罠ではないか疑っています。"
            "player-7 さんが player-8 さんへの警戒を口実に player-5 さんを攻撃しているのも、狼同士の連携に見えます。"
            "霊能者の player-6 さん、player-5 さんの占いや行動履歴を再確認して、本当に player-5 さんが狼か慎重に判断してください。"
        )
        later = (
            "player-6 さん霊能者の player-1 無罪確定は安心しましたが、残る player-5 と player-7 の 2 人全員が狼という状況下で、"
            "player-6 さんが player-5 を即座に処刑対象に挙げるのは不自然です。\n\n"
            "player-7 さんが player-8 さんへの警戒を口実に player-5 を攻撃している様子は、狼同士の連携に見えるどころか、"
            "霊能者を騙して狼を逃がす罠ではないか疑います。player-6 さん、player-5 さんの占いや行動履歴を再確認して本当に player-5 さんが狼か慎重に判断してください。"
        )
        self.assertTrue(RepetitionFilter().allows("player-3", later, [earlier]))

    def test_reusing_a_sentence_from_own_previous_message_is_rejected(self):
        repetition = RepetitionFilter()
        repetition.reserve("player-1", "player-1です。投票の理由を教えてください。")
        # Whole messages differ and the sentence's global count is only one.
        self.assertFalse(repetition.allows("player-1", "player-1です。護衛先の説明をお願いします。", []))
        self.assertFalse(repetition.allows("player-1", "新しい質問です。投票の理由を教えてください。", []))
        self.assertTrue(repetition.allows("player-2", "player-1です。別の意見を聞かせてください。", []))

    def test_previous_sentence_comparison_uses_normalization_and_only_last_message(self):
        repetition = RepetitionFilter()
        repetition.reserve("player-1", "ＰＬＡＹＥＲ-１　です。投票を相談します。")
        self.assertFalse(repetition.allows("player-1", "player-1 です。占い結果を確認します。", []))
        repetition.reserve("player-1", "今日は護衛について相談しましょう。")
        self.assertTrue(repetition.allows("player-1", "player-1 です。明日の予定を決めましょう。", []))
