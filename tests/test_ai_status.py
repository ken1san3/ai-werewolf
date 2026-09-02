from __future__ import annotations

import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import ai_status, check_docs


class DesignGateTests(unittest.TestCase):
    def test_status_requires_exact_vocabulary(self) -> None:
        path = Path("design.md")
        for value in ("APPROVED 待ち", "DESIGN REVIEW: APPROVED", "おはよう"):
            with patch.object(ai_status, "read", return_value=f"Status: {value}\n"):
                self.assertEqual(ai_status.design_status(path), (value, None))
        with patch.object(ai_status, "read", return_value="Status: APPROVED — reviewed\n"):
            self.assertEqual(
                ai_status.design_status(path),
                ("APPROVED — reviewed", True),
            )

    def test_gate_filters_requests_to_target_subphase(self) -> None:
        phase_31 = Path("PHASE3_1_NETWORK_CLIENT_REQUEST.md")
        phase_41 = Path("PHASE4_1_LLM_REQUEST.md")
        with patch.object(
            ai_status,
            "design_request_documents",
            return_value=[phase_31, phase_41],
        ):
            self.assertEqual(ai_status.design_gate_documents("3.1"), [phase_31])

    def test_missing_target_is_unknown_and_blocked(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            ai_status.print_design_gate("")
        self.assertIn("UNKNOWN", output.getvalue())
        self.assertIn("implementation: blocked", output.getvalue())
        self.assertNotIn("NOT REQUIRED", output.getvalue())

    def test_missing_design_is_unknown_and_blocked(self) -> None:
        output = io.StringIO()
        with TemporaryDirectory() as temporary_directory:
            request = Path(temporary_directory) / "PHASE3_1_NETWORK_CLIENT_REQUEST.md"
            request.write_text("DESIGN: REQUIRED\n", encoding="utf-8")
            with patch.object(ai_status, "design_gate_documents", return_value=[request]):
                with redirect_stdout(output):
                    ai_status.print_design_gate(
                        "Target subphase: 3.1\nDesign gate: REQUIRED\n"
                    )
        self.assertIn("_DESIGN.md が無い", output.getvalue())
        self.assertIn("implementation: blocked", output.getvalue())

    def test_not_required_is_allowed_without_request(self) -> None:
        output = io.StringIO()
        with patch.object(ai_status, "design_gate_documents", return_value=[]):
            with redirect_stdout(output):
                ai_status.print_design_gate(
                    "Target subphase: 1.4\nDesign gate: NOT REQUIRED\n"
                )
        self.assertIn("DESIGN: NOT REQUIRED", output.getvalue())
        self.assertIn("implementation: allowed", output.getvalue())

    def test_required_without_request_is_unknown_and_blocked(self) -> None:
        output = io.StringIO()
        with patch.object(ai_status, "design_gate_documents", return_value=[]):
            with redirect_stdout(output):
                ai_status.print_design_gate(
                    "Target subphase: 3.1\nDesign gate: REQUIRED\n"
                )
        self.assertIn("UNKNOWN", output.getvalue())
        self.assertIn("implementation: blocked", output.getvalue())

    def test_missing_or_invalid_gate_is_unknown_and_blocked(self) -> None:
        declarations = (
            "",
            "Design gate: LATER\n",
            "Design gate: REQUIRED\nDesign gate: NOT REQUIRED\n",
        )
        for declaration in declarations:
            output = io.StringIO()
            with redirect_stdout(output):
                ai_status.print_design_gate(f"Target subphase: 3.1\n{declaration}")
            self.assertIn("UNKNOWN", output.getvalue())
            self.assertIn("implementation: blocked", output.getvalue())

    def test_docs_checker_rejects_invalid_status(self) -> None:
        check_docs.problems.clear()
        self.addCleanup(check_docs.problems.clear)
        with patch.object(
            check_docs.ai_status,
            "design_status",
            return_value=("おはよう", None),
        ):
            check_docs.check_design_gate_status()
        self.assertTrue(any("許可語彙外" in problem for problem in check_docs.problems))

    def test_docs_checker_allows_not_required_without_request(self) -> None:
        check_docs.problems.clear()
        self.addCleanup(check_docs.problems.clear)
        with (
            patch.object(check_docs, "read", return_value="## 1.4\n"),
            patch.object(check_docs.ai_status, "target_subphase", return_value="1.4"),
            patch.object(check_docs.ai_status, "design_gate", return_value="NOT REQUIRED"),
            patch.object(check_docs.ai_status, "design_gate_documents", return_value=[]),
        ):
            check_docs.check_design_target()
        self.assertEqual(check_docs.problems, [])

    def test_docs_checker_rejects_gate_request_mismatches(self) -> None:
        check_docs.problems.clear()
        self.addCleanup(check_docs.problems.clear)
        request = Path("PHASE3_1_NETWORK_CLIENT_REQUEST.md")
        with (
            patch.object(check_docs, "read", return_value="## 3.1\n"),
            patch.object(check_docs.ai_status, "target_subphase", return_value="3.1"),
            patch.object(check_docs.ai_status, "design_gate", return_value="REQUIRED"),
            patch.object(check_docs.ai_status, "design_gate_documents", return_value=[]),
        ):
            check_docs.check_design_target()
        self.assertTrue(any("REQUEST が1件無い" in problem for problem in check_docs.problems))

        check_docs.problems.clear()
        with (
            patch.object(check_docs, "read", return_value="## 3.1\n"),
            patch.object(check_docs.ai_status, "target_subphase", return_value="3.1"),
            patch.object(check_docs.ai_status, "design_gate", return_value="NOT REQUIRED"),
            patch.object(check_docs.ai_status, "design_gate_documents", return_value=[request]),
        ):
            check_docs.check_design_target()
        self.assertTrue(any("NOT REQUIRED" in problem for problem in check_docs.problems))

    def test_docs_checker_rejects_review_id_reused_in_archive(self) -> None:
        check_docs.problems.clear()
        self.addCleanup(check_docs.problems.clear)
        archive = Path("2026-09.md")
        inbox_text = "## R-20260902-99 [OPEN] Medium\n"
        archive_text = "## R-20260902-99 [FIXED] Medium\n"

        def read(path: Path) -> str:
            if path == check_docs.DOCS / "REVIEW_INBOX.md":
                return inbox_text
            if path == archive:
                return archive_text
            raise AssertionError(f"unexpected read: {path}")

        with (
            patch.object(check_docs, "read", side_effect=read),
            patch.object(check_docs, "review_archive_files", return_value=[archive]),
        ):
            check_docs.check_review_inbox()

        self.assertTrue(any("R-20260902-99 が重複" in problem for problem in check_docs.problems))

    def test_docs_checker_requires_active_review_for_known_failure(self) -> None:
        check_docs.problems.clear()
        self.addCleanup(check_docs.problems.clear)
        state = "## Test Status\nKnown failing: F006\n\n## Latest Review\n"

        def read(path: Path) -> str:
            if path == check_docs.CURRENT_STATE:
                return state
            if path == check_docs.DOCS / "REVIEW_INBOX.md":
                return "## Open\n\n現在、OPEN / IN_PROGRESS の指摘はありません。\n"
            raise AssertionError(f"unexpected read: {path}")

        with patch.object(check_docs, "read", side_effect=read):
            check_docs.check_known_failures_have_active_review()

        self.assertTrue(any("既知の失敗" in problem for problem in check_docs.problems))

    def test_docs_checker_accepts_known_failure_with_active_review(self) -> None:
        check_docs.problems.clear()
        self.addCleanup(check_docs.problems.clear)
        state = "## Test Status\nKnown failing: F006\n\n## Latest Review\n"
        inbox = "## R-20260902-116 [OPEN] High\n"

        def read(path: Path) -> str:
            if path == check_docs.CURRENT_STATE:
                return state
            if path == check_docs.DOCS / "REVIEW_INBOX.md":
                return inbox
            raise AssertionError(f"unexpected read: {path}")

        with patch.object(check_docs, "read", side_effect=read):
            check_docs.check_known_failures_have_active_review()

        self.assertEqual(check_docs.problems, [])

    def test_docs_checker_accepts_current_world_event_catalog(self) -> None:
        check_docs.problems.clear()
        self.addCleanup(check_docs.problems.clear)

        check_docs.check_world_core_event_catalog()

        self.assertEqual(check_docs.problems, [])

    def test_docs_checker_rejects_missing_and_extra_world_event_catalog_entries(self) -> None:
        check_docs.problems.clear()
        self.addCleanup(check_docs.problems.clear)
        reducer = 'KNOWN_CORE_TYPES = frozenset(\n    {"EVENT_B"}\n)\n'

        with (
            patch.object(check_docs, "read", return_value=reducer),
            patch.object(check_docs, "core_event_types", return_value={"EVENT_A"}),
        ):
            check_docs.check_world_core_event_catalog()

        self.assertTrue(any("EVENT_A" in problem for problem in check_docs.problems))
        self.assertTrue(any("EVENT_B" in problem for problem in check_docs.problems))


if __name__ == "__main__":
    unittest.main()
