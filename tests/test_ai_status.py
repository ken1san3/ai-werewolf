from __future__ import annotations

import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import ai_status, check_docs


class DesignGateTests(unittest.TestCase):
    def test_integrator_bootstrap_sections_exist(self) -> None:
        state = ai_status.read(ai_status.AI / "CURRENT_STATE.md")
        for heading in (
            "Current Phase",
            "Current Target",
            "Current Blockers",
            "Test Status",
            "Next Integration Action",
        ):
            self.assertTrue(ai_status.section(state, heading), heading)

    def test_t001_packet_declares_approved_design_gate(self) -> None:
        packet = ai_status.read(
            ai_status.AI / "tasks" / "T001_PHASE3_4_REACTION_CHAT.md"
        )
        self.assertIn("Task ID: T001", packet)
        self.assertIn("Design Gate status: APPROVED", packet)

    def test_all_responsibility_entries_map_to_runbook_sections(self) -> None:
        self.assertEqual(
            ai_status.ROLE_SECTIONS,
            {
                "integrate": "1",
                "architect": "2",
                "design": "2",
                "implement": "3",
                "review": "4",
                "fix": "5",
                "test": "6",
                "investigate": "7",
            },
        )

    def test_task_records_are_model_neutral_board_records(self) -> None:
        board = (
            "## T001\n\n"
            "Task ID: T001\n"
            "Title: bounded work\n"
            "Role: Implementer\n"
            "State: READY\n"
            "Task packet: `Docs/ai/tasks/T001.md`\n"
        )
        self.assertEqual(
            ai_status.task_records(board),
            [
                {
                    "Header ID": "T001",
                    "Task ID": "T001",
                    "Title": "bounded work",
                    "Role": "Implementer",
                    "State": "READY",
                    "Task packet": "`Docs/ai/tasks/T001.md`",
                }
            ],
        )

    def test_decision_required_remains_visible_without_blocking_ready_tasks(self) -> None:
        board = (
            "## T001\n\nTask ID: T001\nTitle: waiting\nRole: Architect\n"
            "State: DECISION_REQUIRED\nTask packet: `Docs/ai/tasks/T001.md`\n\n"
            "## T002\n\nTask ID: T002\nTitle: independent\nRole: Tester\n"
            "State: READY\nTask packet: `Docs/ai/tasks/T002.md`\n"
        )
        live = [
            record["Task ID"]
            for record in ai_status.task_records(board)
            if record.get("State") in ai_status.LIVE_TASK_STATES
        ]
        self.assertEqual(live, ["T001", "T002"])

    def test_current_task_board_passes_consistency_checks(self) -> None:
        check_docs.problems.clear()
        self.addCleanup(check_docs.problems.clear)

        check_docs.check_task_board()

        self.assertEqual(check_docs.problems, [])

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

    def test_closed_approved_request_is_excluded_and_multiple_open_requests_are_supported(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            design_dir = Path(temporary_directory) / "design"
            design_dir.mkdir()
            network_request = design_dir / "PHASE3_1_NETWORK_CLIENT_REQUEST.md"
            completion_request = design_dir / "PHASE3_1_COMPLETION_EVIDENCE_REQUEST.md"
            network_design = design_dir / "PHASE3_1_NETWORK_CLIENT_DESIGN.md"
            completion_design = design_dir / "PHASE3_1_COMPLETION_EVIDENCE_DESIGN.md"
            network_request.write_text(
                "Status: REQUESTED\nRequest status: CLOSED\nDESIGN: REQUIRED\n",
                encoding="utf-8",
            )
            completion_request.write_text(
                "Status: REQUESTED\nDESIGN: REQUIRED\n",
                encoding="utf-8",
            )
            network_design.write_text("Status: APPROVED\n", encoding="utf-8")
            completion_design.write_text("Status: IN_REVIEW\n", encoding="utf-8")

            with patch.object(ai_status, "AI", Path(temporary_directory)):
                self.assertEqual(
                    ai_status.design_gate_documents("3.1"), [completion_request]
                )
                network_request.write_text(
                    "Status: REQUESTED\nRequest status: OPEN\nDESIGN: REQUIRED\n",
                    encoding="utf-8",
                )
                self.assertEqual(
                    ai_status.design_gate_documents("3.1"),
                    [completion_request, network_request],
                )

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
                        "Target subphase: 3.1\n"
                        "Target design: PHASE3_1_NETWORK_CLIENT_DESIGN.md\n"
                        "Design gate: REQUIRED\n"
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
                    "Target subphase: 3.1\nTarget design: PHASE3_1_NETWORK_CLIENT_DESIGN.md\n"
                    "Design gate: REQUIRED\n"
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

    def test_docs_checker_accepts_top_level_phase_heading(self) -> None:
        check_docs.problems.clear()
        self.addCleanup(check_docs.problems.clear)
        with (
            patch.object(check_docs, "read", return_value="# Phase 4 — Local LLM\n"),
            patch.object(check_docs.ai_status, "target_subphase", return_value="4"),
            patch.object(check_docs.ai_status, "design_gate", return_value="REQUIRED"),
            patch.object(check_docs.ai_status, "design_gate_documents", return_value=[]),
        ):
            check_docs.check_design_target()
        self.assertFalse(any("ROADMAP 節が無い" in problem for problem in check_docs.problems))
        self.assertTrue(any("REQUEST が無い" in problem for problem in check_docs.problems))

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
        self.assertTrue(any("REQUEST が無い" in problem for problem in check_docs.problems))

        check_docs.problems.clear()
        with (
            patch.object(check_docs, "read", return_value="## 3.1\n"),
            patch.object(check_docs.ai_status, "target_subphase", return_value="3.1"),
            patch.object(check_docs.ai_status, "design_gate", return_value="NOT REQUIRED"),
            patch.object(check_docs.ai_status, "design_gate_documents", return_value=[request]),
        ):
            check_docs.check_design_target()
        self.assertTrue(any("NOT REQUIRED" in problem for problem in check_docs.problems))

    def test_docs_checker_accepts_multiple_open_requests_when_target_is_named(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            docs = root / "Docs" / "ai"
            design_dir = docs / "design"
            design_dir.mkdir(parents=True)
            state = docs / "CURRENT_STATE.md"
            roadmap = docs / "ROADMAP.md"
            network_request = design_dir / "PHASE3_1_NETWORK_CLIENT_REQUEST.md"
            completion_request = design_dir / "PHASE3_1_COMPLETION_EVIDENCE_REQUEST.md"
            network_request.write_text(
                "Status: REQUESTED\nRequest status: CLOSED\nDESIGN: REQUIRED\n",
                encoding="utf-8",
            )
            completion_request.write_text(
                "Status: REQUESTED\nDESIGN: REQUIRED\n",
                encoding="utf-8",
            )
            (design_dir / "PHASE3_1_NETWORK_CLIENT_DESIGN.md").write_text(
                "Status: APPROVED\n", encoding="utf-8"
            )
            (design_dir / "PHASE3_1_COMPLETION_EVIDENCE_DESIGN.md").write_text(
                "Status: IN_REVIEW\n", encoding="utf-8"
            )
            state.write_text(
                "Target subphase: 3.1\n"
                "Target design: PHASE3_1_COMPLETION_EVIDENCE_DESIGN.md\n"
                "Design gate: REQUIRED\n",
                encoding="utf-8",
            )
            roadmap.write_text("## 3.1\n", encoding="utf-8")

            with (
                patch.object(check_docs, "CURRENT_STATE", state),
                patch.object(check_docs, "DOCS", docs),
                patch.object(ai_status, "AI", docs),
            ):
                check_docs.problems.clear()
                check_docs.check_design_target()
                self.assertEqual(check_docs.problems, [])

                network_request.write_text(
                    "Status: REQUESTED\nRequest status: OPEN\nDESIGN: REQUIRED\n",
                    encoding="utf-8",
                )
                check_docs.problems.clear()
                check_docs.check_design_target()
                self.assertEqual(check_docs.problems, [])

    def test_print_design_gate_uses_named_target_when_multiple_requests_are_open(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            design_dir = root / "design"
            design_dir.mkdir()
            network_request = design_dir / "PHASE3_1_NETWORK_CLIENT_REQUEST.md"
            completion_request = design_dir / "PHASE3_1_COMPLETION_EVIDENCE_REQUEST.md"
            completion_design = design_dir / "PHASE3_1_COMPLETION_EVIDENCE_DESIGN.md"
            network_request.write_text(
                "Status: REQUESTED\nDESIGN: REQUIRED\n", encoding="utf-8"
            )
            completion_request.write_text(
                "Status: REQUESTED\nDESIGN: REQUIRED\n", encoding="utf-8"
            )
            (design_dir / "PHASE3_1_NETWORK_CLIENT_DESIGN.md").write_text(
                "Status: DRAFT\n", encoding="utf-8"
            )
            completion_design.write_text(
                "Status: APPROVED\n", encoding="utf-8"
            )
            output = io.StringIO()
            state = (
                "Target subphase: 3.1\n"
                "Target design: PHASE3_1_COMPLETION_EVIDENCE_DESIGN.md\n"
                "Design gate: REQUIRED\n"
            )
            with patch.object(ai_status, "AI", root), redirect_stdout(output):
                ai_status.print_design_gate(state)

        rendered = output.getvalue()
        self.assertIn("open request documents:", rendered)
        self.assertIn(f"target design: {completion_design.as_posix()}", rendered)
        self.assertIn("approved: yes", rendered)
        self.assertIn("implementation: allowed", rendered)

    def test_print_design_gate_omits_historical_executor_attribution(self) -> None:
        output = io.StringIO()
        request = Path("PHASE3_4_REACTION_CHAT_REQUEST.md")
        design = Path("PHASE3_4_REACTION_CHAT_DESIGN.md")
        state = (
            "Target subphase: 3.4\n"
            "Target design: PHASE3_4_REACTION_CHAT_DESIGN.md\n"
            "Design gate: REQUIRED\n"
        )
        with (
            patch.object(ai_status, "design_gate_documents", return_value=[request]),
            patch.object(ai_status, "target_design_request", return_value=request),
            patch.object(Path, "is_file", return_value=True),
            patch.object(
                ai_status,
                "design_status",
                return_value=("APPROVED — Reviewer / ExampleModel", True),
            ),
            redirect_stdout(output),
        ):
            ai_status.print_design_gate(state)

        rendered = output.getvalue()
        self.assertIn("Status: APPROVED", rendered)
        self.assertNotIn("ExampleModel", rendered)

    def test_required_gate_without_named_target_design_is_blocked(self) -> None:
        output = io.StringIO()
        with patch.object(ai_status, "design_gate_documents", return_value=[Path("request.md")]):
            with redirect_stdout(output):
                ai_status.print_design_gate(
                    "Target subphase: 3.1\nDesign gate: REQUIRED\n"
                )
        self.assertIn("Target design", output.getvalue())
        self.assertIn("implementation: blocked", output.getvalue())

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
            if path == check_docs.TASK_BOARD:
                return "# Task Board\n"
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

    def test_docs_checker_accepts_known_failure_with_live_investigator(self) -> None:
        check_docs.problems.clear()
        self.addCleanup(check_docs.problems.clear)
        state = "## Test Status\nKnown failing: T003 completion\n\n## Latest Review\n"
        board = (
            "## T003\n\nTask ID: T003\nTitle: isolate\nRole: Investigator\n"
            "State: IN_PROGRESS\nTask packet: `Docs/ai/tasks/T003.md`\n"
        )

        def read(path: Path) -> str:
            if path == check_docs.CURRENT_STATE:
                return state
            if path == check_docs.DOCS / "REVIEW_INBOX.md":
                return "## Open\n"
            if path == check_docs.TASK_BOARD:
                return board
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
