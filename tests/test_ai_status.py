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


class CoordinationRecoveryTests(unittest.TestCase):
    """Adversarial recovery fixtures use no temporary filesystem or product runtime."""

    def record(self, state: str = "IN_PROGRESS") -> dict[str, str]:
        return {"Task ID": "T900", "State": state,
                "Handoff path": "Docs/ai/handoffs/tasks/T900.md"}

    def test_returned_pass_does_not_close_or_redispatch_in_progress(self) -> None:
        record = self.record()
        with patch.object(Path, "is_file", return_value=True):
            warnings = ai_status.coordination_warnings(
                "Active task: T900\nTask state: IN_PROGRESS\n", [record])
        self.assertEqual(record["State"], "IN_PROGRESS")
        self.assertTrue(any("do not redispatch or auto-close" in w for w in warnings))
        self.assertTrue(any("host ownership UNKNOWN" in w for w in warnings))

    def test_done_task_in_stale_active_pointer_is_not_redispatched(self) -> None:
        warnings = ai_status.coordination_warnings(
            "Active task: T900\nTask state: IN_PROGRESS\n", [self.record("DONE")])
        self.assertTrue(any("TASKS owns lifecycle" in w for w in warnings))
        self.assertTrue(any("do not redispatch from an old pointer" in w for w in warnings))

    def test_missing_and_duplicate_authority_need_reconciliation(self) -> None:
        for records in ([], [self.record(), self.record()]):
            with patch.object(Path, "is_file", return_value=False):
                warnings = ai_status.coordination_warnings("Active task: T900\n", records)
            self.assertTrue(any("missing/ambiguous" in w for w in warnings))

    def test_ready_with_old_result_requires_inspection(self) -> None:
        with patch.object(Path, "is_file", return_value=True):
            warnings = ai_status.coordination_warnings(
                "Active task: T900\nTask state: READY\n", [self.record("READY")])
        self.assertTrue(any("handoff exists" in w for w in warnings))

    def test_evidence_hash_and_claims_do_not_expose_old_instructions(self) -> None:
        import hashlib
        body = b"Status: COMPLETE\nVerdict: **PASS**\nNext action: dispatch T899\n"
        with patch.object(Path, "read_bytes", return_value=body):
            summary = ai_status.handoff_summary(self.record())
        self.assertIn(hashlib.sha256(body).hexdigest(), summary)
        self.assertIn("Verdict: **PASS**", summary)
        self.assertIn("not integrated approval", summary)
        self.assertNotIn("dispatch T899", summary)

    def test_unreadable_evidence_is_not_absent_worker(self) -> None:
        with patch.object(Path, "read_bytes", side_effect=PermissionError):
            self.assertIn("missing or unreadable", ai_status.handoff_summary(self.record()))

    def test_default_cold_start_is_selective_and_surfaces_hold(self) -> None:
        state = ("## Current Phase\nPhase 9 fixture\n## Current Target\n"
                 "Active task: T900\nTask state: IN_PROGRESS\n"
                 "## Continuation Hold\nHuman review before product resumption.\n"
                 "## Current Blockers\nReturned result needs reconciliation.\n"
                 "## Critical Path\nVerify result before required independent review.\n"
                 "## Next Integration Action\nHonor hold.\n")
        board = ("## T900\nTask ID: T900\nState: IN_PROGRESS\n"
                 "Task packet: Docs/ai/tasks/T900.md\n"
                 "Handoff path: Docs/ai/handoffs/tasks/T900.md\n")
        sources = {"CURRENT_STATE.md": state, "TASKS.md": board, "T900.md": "## Canonical references\n",
                   "REVIEW_INBOX.md": "", "OPEN_QUESTIONS.md": ""}

        def read(path: Path) -> str:
            # Fails on preload of history, RUNBOOK, unrelated packets or docs.
            return sources[path.name]

        output = io.StringIO()
        with (patch.object(ai_status, "read", side_effect=read),
              patch.object(ai_status, "git_summary", return_value="fixture HEAD; dirty"),
              patch.object(ai_status, "print_design_gate"),
              patch.object(Path, "is_file", return_value=True),
              patch.object(Path, "read_bytes", return_value=b"Status: COMPLETE\nVerdict: PASS\n"),
              patch("sys.argv", ["ai_status.py", "integrate"]), redirect_stdout(output)):
            self.assertEqual(ai_status.main(), 0)
        rendered = output.getvalue()
        for expected in ("Phase 9 fixture", "T900 [IN_PROGRESS]", "CONTINUATION HOLD",
                         "Human review", "host ownership UNKNOWN", "CONTEXT READ SET",
                         "do not redispatch", "TASKS lifecycle", "Honor hold"):
            self.assertIn(expected, rendered)
        self.assertNotIn("RUNBOOK:", rendered)
        self.assertNotIn("Verdict: PASS", rendered)


class ContextDietTests(unittest.TestCase):
    def render(self, *args: str, state_override: str | None = None,
               packet_override: str | None = None) -> tuple[int, str, list[str]]:
        state = state_override or (
            "## Current Phase\nPhase fixture\n## Current Target\nActive task: T900\n"
            "Task state: IN_PROGRESS\n## Continuation Hold\nSTOP GAME; docs only\n"
            "## Current Blockers\nCurrent blocker\n## Next Integration Action\nSelected work\n"
            "## History\nDO NOT PRELOAD HISTORICAL CONTENT\n")
        board = ("## T900\nTask ID: T900\nRole: Integrator\nState: IN_PROGRESS\n"
                 "Task packet: `Docs/ai/tasks/T900.md`\nHandoff path: `old-private-handoff.md`\n"
                 "## T901\nTask ID: T901\nRole: Reviewer\nState: READY\n"
                 "Task packet: `Docs/ai/tasks/T901.md`\n"
                 "## T899\nTask ID: T899\nState: DONE\nTask packet: `archived.md`\n")
        sources = {"CURRENT_STATE.md": state, "TASKS.md": board,
                   "T900.md": packet_override if packet_override is not None else
                       "## Canonical references\n- `Docs/ai/OPERATIONS.md`\n"
                       "## Evidence location\n`old-private-handoff.md`\n",
                   "T901.md": "## Canonical references\n- `Docs/ai/INDEX.md`\n",
                   "RUNBOOK.md": "## 1. Integrator\nDetails explicitly requested\n"}
        reads: list[str] = []

        def read(path: Path) -> str:
            reads.append(path.name)
            return sources[path.name]  # unrelated canonical/history/hand-off reads fail the test

        output = io.StringIO()
        with (patch.object(ai_status, "read", side_effect=read),
              patch.object(ai_status, "git_summary", return_value="fixture HEAD"),
              patch.object(ai_status, "print_design_gate") as gate,
              patch.object(ai_status, "handoff_summary", return_value="EXPLICIT EVIDENCE") as evidence,
              patch.object(Path, "is_file", return_value=True),
              patch("sys.argv", ["ai_status.py", "integrate", *args]), redirect_stdout(output)):
            result = ai_status.main()
            if "--details" not in args:
                gate.assert_not_called()
                evidence.assert_not_called()
        return result, output.getvalue(), reads

    def test_default_reads_only_current_packet_and_never_opens_handoff(self) -> None:
        result, output, reads = self.render()
        self.assertEqual(result, 0)
        self.assertEqual(reads, ["CURRENT_STATE.md", "TASKS.md", "T900.md"])
        self.assertIn("STOP GAME; docs only", output)
        self.assertIn("Docs/ai/OPERATIONS.md", output)  # pointer, not the file contents
        self.assertNotIn("old-private-handoff", output)
        self.assertNotIn("T901", output)
        self.assertNotIn("T899", output)
        self.assertNotIn("HISTORICAL CONTENT", output)

    def test_assigned_worker_reads_its_packet_not_active_main_packet(self) -> None:
        result, output, reads = self.render("--task", "T901")
        self.assertEqual(result, 0)
        self.assertEqual(reads, ["CURRENT_STATE.md", "TASKS.md", "T901.md"])
        self.assertIn("ACTIVE TASK: T900; SELECTED TASK: T901", output)
        self.assertIn("STOP GAME", output)
        self.assertIn("Docs/ai/INDEX.md", output)

    def test_explicit_all_live_lists_pointers_without_expanding_other_packets(self) -> None:
        result, output, reads = self.render("--all-live")
        self.assertEqual(result, 0)
        self.assertIn("T901 [READY]", output)
        self.assertNotIn("T899", output)
        self.assertNotIn("T901.md", reads)

    def test_unknown_and_done_task_do_not_fallback_or_read_archive(self) -> None:
        for task in ("T998", "T899"):
            with self.subTest(task=task):
                result, output, reads = self.render("--task", task)
                self.assertEqual(result, 1)
                self.assertEqual(reads, ["CURRENT_STATE.md", "TASKS.md"])
                self.assertNotIn("CONTEXT READ SET", output)

    def test_duplicate_active_declaration_is_not_silently_selected(self) -> None:
        result, _, reads = self.render(state_override="Active task: T900\nActive task: T901\n")
        self.assertEqual(result, 1)
        self.assertEqual(reads, ["CURRENT_STATE.md", "TASKS.md"])

    def test_details_is_opt_in_and_limited_to_selected_context(self) -> None:
        result, output, reads = self.render("--details")
        self.assertEqual(result, 0)
        self.assertIn("EXPLICIT EVIDENCE", output)
        self.assertIn("RUNBOOK: integrate", output)
        self.assertEqual(reads, ["CURRENT_STATE.md", "TASKS.md", "T900.md", "RUNBOOK.md"])

    def test_read_set_rejects_escape_and_deduplicates_explicit_files(self) -> None:
        outside = (ai_status.ROOT.parent / "not-a-canonical-file.md").as_posix()
        result, output, _ = self.render(packet_override=f"## Canonical references\n`{outside}`\n")
        self.assertEqual(result, 1)
        self.assertIn("READ SET ERROR", output)
        with (patch.object(ai_status, "read", return_value=
                  "## Canonical references\n`Docs/ai/OPERATIONS.md` `Docs/ai/OPERATIONS.md`\n"),
              patch.object(Path, "is_file", return_value=True)):
            self.assertEqual(ai_status.packet_read_set({"Task packet": "`Docs/ai/tasks/T900.md`"}),
                             ["Docs/ai/tasks/T900.md", "Docs/ai/OPERATIONS.md"])

    def test_missing_reference_fails_closed(self) -> None:
        with (patch.object(ai_status, "read", return_value="## Canonical references\n`missing.md`\n"),
              patch.object(Path, "is_file", side_effect=lambda path: path.name != "missing.md", autospec=True)):
            with self.assertRaisesRegex(ValueError, "reference is missing"):
                ai_status.packet_read_set({"Task packet": "`Docs/ai/tasks/T900.md`"})


class DispatchDocumentTests(unittest.TestCase):
    """User-specified routing examples are documentation checks, not an agent router."""

    def test_five_axes_keep_independence_without_automatic_role_chain(self) -> None:
        doc = ai_status.read(ai_status.AI / "decisions/D075_RISK_BASED_DISPATCH_AND_CONTEXT.md")
        rows = {}
        for line in ai_status.section(doc, "Dispatch examples").splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 9:
                rows[cells[0]] = cells[1:]
        expected = {
            "known_local_bug": ["implement+focused", "no", "no", "no", "yes", "no", "no", "no"],
            "unknown_after_one_diagnosis": ["bound", "no", "yes", "no", "no", "no", "no", "no"],
            "real_game_acceptance": ["coordinate", "no", "no", "yes", "yes", "canonical", "conditional", "binding_only"],
            "reviewer_did_previous_task_only": ["integrate", "no", "no", "no", "existing", "no", "no", "yes"],
            "reviewer_authored_target": ["coordinate", "no", "no", "no", "independent", "yes", "no", "no"],
            "canonical_fresh_required": ["coordinate", "no", "no", "no", "independent", "yes", "no", "no"],
            "same_approved_bytes_context": ["verify_hash", "no", "no", "no", "no", "no", "no", "yes"],
            "same_bytes_new_acceptance": ["coordinate", "yes", "no", "yes", "yes", "conditional", "conditional", "no"],
            "high_risk_second_opinion": ["coordinate", "conditional", "no", "conditional", "yes", "conditional", "conditional", "no"],
            "unchanged_unresolved_findings": ["fix", "no", "no", "no", "no", "no", "no", "unresolved"],
        }
        for case, columns in expected.items():
            with self.subTest(case=case):
                self.assertEqual(rows.get(case), columns)
        for case in ("status_only", "deterministic_saved_log"):
            self.assertEqual(rows[case][1:7], ["no"] * 6)

    def test_prior_decision_and_history_bytes_are_preserved(self) -> None:
        import hashlib
        expected = {
            "decisions/D053_TWO_REVIEWERS.md": "d3756bc203c1ac8781f8b1f81003cc6085b63ef3171674951b58f1f48801c49a",
            "history/2026-09-16-before-d075/CURRENT_STATE.md": "8fd04746e92bb12675542077a21131c419499609a80d91d2b72ea3249f6e4924",
            "history/2026-09-16-before-d075/TASKS.md": "01c3111a7f15cdddc52a49539783df55d1bea09bf403c870ccf7ee8bf46d3c0d",
        }
        for path, digest in expected.items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((ai_status.AI / path).read_bytes()).hexdigest(), digest)

    def test_context_checker_rejects_missing_hold_and_independence_fields(self) -> None:
        originals = {path: check_docs.read(path) for path in (
            check_docs.CURRENT_STATE,
            check_docs.DOCS / "tasks/TEMPLATE.md",
            check_docs.DOCS / "handoffs/tasks/REVIEW_TEMPLATE.md")}
        cases = (
            (check_docs.CURRENT_STATE, "## Continuation Hold", "## Removed hold", "Continuation Hold"),
            (check_docs.DOCS / "tasks/TEMPLATE.md", "## Required independence", "## Removed field", "Required independence"),
            (check_docs.DOCS / "handoffs/tasks/REVIEW_TEMPLATE.md", "Evidence:", "Removed:", "Evidence"),
        )
        self.addCleanup(check_docs.problems.clear)
        for path, old, new, expected in cases:
            with self.subTest(expected=expected):
                modified = dict(originals)
                modified[path] = originals[path].replace(old, new)
                check_docs.problems.clear()
                with patch.object(check_docs, "read", side_effect=lambda item: modified[item]):
                    check_docs.check_context_contract()
                self.assertTrue(any(expected in problem for problem in check_docs.problems))

    def test_bootstrap_discovers_state_and_preserves_human_boundary(self) -> None:
        prompt = ai_status.read(ai_status.AI / "MAIN_INTEGRATOR_PROMPT.md")
        self.assertNotRegex(prompt, r"T\d{3}|Phase \d")
        self.assertIn("ai_status.py integrate", prompt)
        self.assertIn("across packet/wave boundaries", prompt)
        self.assertIn("explicit hold", prompt)
        self.assertIn("irreconcilable authority", prompt)
        self.assertIn("missing external", prompt)


if __name__ == "__main__":
    unittest.main()
