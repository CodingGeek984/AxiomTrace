"""Configuration and storage hygiene tests."""

from __future__ import annotations

from config import get_config
from services.project_service import (
    count_execution_logs,
    get_or_create_project,
    prune_execution_logs,
    record_execution,
)


class TestConfiguration:
    def test_environment_selects_the_config(self):
        assert get_config("testing").TESTING is True
        assert get_config("development").DEBUG is True
        assert get_config("production").DEBUG is False

    def test_unknown_environment_raises(self):
        try:
            get_config("nope")
        except KeyError:
            return
        raise AssertionError("expected KeyError")

    def test_sandbox_budget_is_strict(self):
        config = get_config("testing")
        assert config.SANDBOX_TIMEOUT_SECONDS <= 2.0
        assert config.SANDBOX_MAX_INSTRUCTIONS > 0
        assert config.SANDBOX_MEMORY_LIMIT_MB > 0

    def test_memory_hygiene_knobs_are_bounded(self):
        config = get_config("testing")
        assert config.EXECUTION_LOG_RETENTION > 0
        assert config.MAX_GRAPH_NODES > 0
        assert config.MAX_GRAPH_EDGES > 0
        assert config.AI_CONTEXT_MAX_CHARS > 0


class TestExecutionLogRetention:
    @staticmethod
    def _reset_logs() -> None:
        from database.connection import db
        from database.models import ExecutionLogModel

        ExecutionLogModel.query.delete()
        db.session.commit()

    def test_pruning_removes_the_oldest_rows(self, app):
        with app.app_context():
            self._reset_logs()
            for index in range(12):
                record_execution(
                    node_id=f"n{index}",
                    input_payload={"i": index},
                    output_payload={"ok": True},
                    status="success",
                    runtime_ms=1.0,
                )
            assert count_execution_logs() == 12

            removed = prune_execution_logs(limit=5)

            assert removed == 7
            assert count_execution_logs() == 5

    def test_records_are_written_as_a_ring(self, app):
        with app.app_context():
            self._reset_logs()
            for index in range(3):
                record_execution(
                    node_id=f"ring{index}",
                    input_payload={},
                    output_payload={},
                    status="error",
                    runtime_ms=2.0,
                )
            assert count_execution_logs() == 3
            assert prune_execution_logs(limit=2) == 1

    def test_pruning_is_a_noop_below_the_ceiling(self, app):
        with app.app_context():
            self._reset_logs()
            record_execution(
                node_id="single",
                input_payload={},
                output_payload={},
                status="success",
                runtime_ms=1.0,
            )
            assert prune_execution_logs(limit=1000) == 0
            assert count_execution_logs() == 1

    def test_project_lookup_is_idempotent(self, app):
        with app.app_context():
            project, created = get_or_create_project("retention", "/tmp/retention")
            again, created_again = get_or_create_project("retention", "/tmp/retention")
            assert created is True
            assert created_again is False
            assert project.id == again.id
