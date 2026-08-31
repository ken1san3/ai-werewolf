from __future__ import annotations

import unittest

from server.aiwolf_core.rejections import ActionRejected, PLAYER_ACTION_REJECTION_REASONS


class ActionRejectedTests(unittest.TestCase):
    def test_accepts_registered_reason(self) -> None:
        rejected = ActionRejected("action_unavailable", "local detail")

        self.assertEqual(rejected.reason, "action_unavailable")
        self.assertEqual(str(rejected), "local detail")

    def test_rejects_unregistered_reason(self) -> None:
        self.assertIn("action_unavailable", PLAYER_ACTION_REJECTION_REASONS)
        with self.assertRaisesRegex(ValueError, "unregistered rejection reason"):
            ActionRejected("misspelled_reason")


if __name__ == "__main__":
    unittest.main()
