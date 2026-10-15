"""Collection status, failure propagation, and cron safety contracts."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from scripts.normalize import to_okf
from scripts import run_pipeline


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ingest.yml"


class _OfflineAdapter:
    offline = True

    def __init__(self, *args, **kwargs):
        pass

    def collect(self):
        return [{"id": "fixture:one", "name": "fixture"}]


class _BrokenAdapter:
    def __init__(self, *args, **kwargs):
        pass

    def collect(self):
        raise RuntimeError("upstream unavailable")


class _IncompleteLiveAdapter:
    offline = False
    collection_mode = "live"
    snapshot_complete = False
    complete = False
    truncated = True

    def __init__(self, rows):
        self.rows = rows

    def collect(self):
        return self.rows


class TestCollectionStatus(unittest.TestCase):
    def test_offline_fixture_is_explicit_and_writes_collection_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = to_okf.SOURCES_LOG_DIR
            old_status = to_okf.COLLECTION_STATUS_PATH
            to_okf.SOURCES_LOG_DIR = Path(tmp)
            to_okf.COLLECTION_STATUS_PATH = Path(tmp) / "collection-status.json"
            try:
                with mock.patch.dict(to_okf.ADAPTERS, {"fixture": _OfflineAdapter}, clear=False), \
                     mock.patch.object(to_okf, "sources", return_value=[{"key": "fixture", "enabled": True}]), \
                     mock.patch.object(to_okf.geocode, "enrich", side_effect=lambda rows, **_: rows), \
                     mock.patch.object(to_okf, "upsert", return_value={"created": 1, "updated": 0, "skipped": 0, "archived": 0}):
                    summary = to_okf.run(["fixture"], use_network=False)
            finally:
                to_okf.SOURCES_LOG_DIR = old_dir
                to_okf.COLLECTION_STATUS_PATH = old_status

            self.assertEqual(summary["sources"]["fixture"]["mode"], "fixture")
            self.assertEqual(summary["sources"]["fixture"]["collected"], 1)
            status = json.loads((Path(tmp) / "collection-status.json").read_text(encoding="utf-8"))
            row = status["sources"]["fixture"]
            self.assertEqual(row["mode"], "fixture")
            self.assertEqual(row["collected"], 1)
    def test_live_failure_is_reported_and_does_not_upsert_empty_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = to_okf.SOURCES_LOG_DIR
            old_status = to_okf.COLLECTION_STATUS_PATH
            to_okf.SOURCES_LOG_DIR = Path(tmp)
            to_okf.COLLECTION_STATUS_PATH = Path(tmp) / "collection-status.json"
            (Path(tmp) / "collection-status.json").write_text(json.dumps({
                "sources": {"broken": {"last_success_at": "2026-10-14T01:00:00+09:00"}}
            }), encoding="utf-8")
            try:
                with mock.patch.dict(to_okf.ADAPTERS, {"broken": _BrokenAdapter}, clear=False), \
                     mock.patch.object(to_okf, "sources", return_value=[{"key": "broken", "enabled": True}]), \
                     mock.patch.object(to_okf, "upsert") as upsert:
                    summary = to_okf.run(["broken"], use_network=True)
            finally:
                to_okf.SOURCES_LOG_DIR = old_dir
                to_okf.COLLECTION_STATUS_PATH = old_status

            row = summary["sources"]["broken"]
            self.assertEqual(row["mode"], "error")
            self.assertEqual(row["last_success_at"], "2026-10-14T01:00:00+09:00")
            self.assertEqual(row["error"], "RuntimeError")
            self.assertTrue(summary["failed_live"])
            upsert.assert_not_called()

    def test_collection_exception_status_exposes_type_not_request_credentials(self):
        class _CredentialFailureAdapter:
            offline = False
            collection_mode = "live"

            def collect(self):
                raise RuntimeError("GET https://user:pass@example.org/api?api_key=secret-token")

        with tempfile.TemporaryDirectory() as tmp:
            old_dir = to_okf.SOURCES_LOG_DIR
            old_status = to_okf.COLLECTION_STATUS_PATH
            to_okf.SOURCES_LOG_DIR = Path(tmp)
            to_okf.COLLECTION_STATUS_PATH = Path(tmp) / "collection-status.json"
            try:
                with mock.patch.dict(to_okf.ADAPTERS, {"credential": _CredentialFailureAdapter}, clear=False), \
                     mock.patch.object(to_okf, "sources", return_value=[{"key": "credential", "enabled": True}]), \
                     mock.patch.object(to_okf, "upsert") as upsert:
                    summary = to_okf.run(["credential"], use_network=True)
            finally:
                to_okf.SOURCES_LOG_DIR = old_dir
                to_okf.COLLECTION_STATUS_PATH = old_status

        row = summary["sources"]["credential"]
        self.assertEqual(row["mode"], "error")
        self.assertEqual(row["error"], "RuntimeError")
        self.assertNotIn("secret-token", json.dumps(summary, ensure_ascii=False))
        self.assertNotIn("user:pass", json.dumps(summary, ensure_ascii=False))
        upsert.assert_not_called()

    def test_truncated_live_snapshot_upserts_without_processed_source_archival(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = to_okf.SOURCES_LOG_DIR
            old_status = to_okf.COLLECTION_STATUS_PATH
            to_okf.SOURCES_LOG_DIR = Path(tmp)
            to_okf.COLLECTION_STATUS_PATH = Path(tmp) / "collection-status.json"
            try:
                with mock.patch.dict(to_okf.ADAPTERS, {"partial": lambda: _IncompleteLiveAdapter([{"id": "partial"}])}, clear=False), \
                     mock.patch.object(to_okf, "sources", return_value=[{"key": "partial", "enabled": True}]), \
                     mock.patch.object(to_okf, "upsert", return_value={"created": 1}) as upsert:
                    summary = to_okf.run(["partial"], use_network=True)
            finally:
                to_okf.SOURCES_LOG_DIR = old_dir
                to_okf.COLLECTION_STATUS_PATH = old_status
        self.assertFalse(summary["failed_live"])
        self.assertEqual(summary["sources"]["partial"]["truncated"], True)
        self.assertEqual(upsert.call_args.kwargs["processed_sources"], None)

    def test_second_page_error_preserves_existing_events_and_does_not_upsert(self):
        class _PageError(_IncompleteLiveAdapter):
            collection_errors = (RuntimeError("page 2 unavailable"),)
            truncated = False

        with tempfile.TemporaryDirectory() as tmp:
            old_dir = to_okf.SOURCES_LOG_DIR
            old_status = to_okf.COLLECTION_STATUS_PATH
            to_okf.SOURCES_LOG_DIR = Path(tmp)
            to_okf.COLLECTION_STATUS_PATH = Path(tmp) / "collection-status.json"
            try:
                with mock.patch.dict(to_okf.ADAPTERS, {"partial": lambda: _PageError([{"id": "partial"}])}, clear=False), \
                     mock.patch.object(to_okf, "sources", return_value=[{"key": "partial", "enabled": True}]), \
                     mock.patch.object(to_okf, "upsert") as upsert:
                    summary = to_okf.run(["partial"], use_network=True)
            finally:
                to_okf.SOURCES_LOG_DIR = old_dir
                to_okf.COLLECTION_STATUS_PATH = old_status
        self.assertTrue(summary["failed_live"])
        self.assertEqual(summary["sources"]["partial"]["mode"], "error")
        upsert.assert_not_called()

    def test_live_empty_with_prior_active_events_is_error_and_preserves_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = to_okf.SOURCES_LOG_DIR
            old_status = to_okf.COLLECTION_STATUS_PATH
            to_okf.SOURCES_LOG_DIR = Path(tmp)
            to_okf.COLLECTION_STATUS_PATH = Path(tmp) / "collection-status.json"
            try:
                with mock.patch.dict(to_okf.ADAPTERS, {"empty": lambda: _IncompleteLiveAdapter([])}, clear=False), \
                     mock.patch.object(to_okf, "sources", return_value=[{"key": "empty", "enabled": True}]), \
                     mock.patch.object(to_okf, "_source_has_active_events", return_value=True), \
                     mock.patch.object(to_okf, "upsert") as upsert:
                    summary = to_okf.run(["empty"], use_network=True)
            finally:
                to_okf.SOURCES_LOG_DIR = old_dir
                to_okf.COLLECTION_STATUS_PATH = old_status
        self.assertTrue(summary["failed_live"])
        self.assertEqual(summary["sources"]["empty"]["error"], "EmptyCollection")
        upsert.assert_not_called()


    def test_pipeline_passes_current_public_rows_and_enforces_sqlite_count_parity(self):
        events = [{"id": "public-1"}, {"id": "public-2"}]
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            (out_dir / "events.json").write_text(json.dumps(events), encoding="utf-8")
            with mock.patch.object(run_pipeline.build_index, "OUT_DIR", out_dir), \
                 mock.patch.object(run_pipeline.to_okf, "run", return_value={"failed": False}), \
                 mock.patch.object(run_pipeline.subprocess, "call", return_value=0), \
                 mock.patch.object(run_pipeline.build_index, "build"), \
                 mock.patch.object(run_pipeline.build_pages, "build"), \
                 mock.patch.object(run_pipeline.build_sqlite, "build", return_value={"events": 2}) as sqlite, \
                 mock.patch.object(run_pipeline.wiki_index, "build"):
                self.assertEqual(run_pipeline.main(["fixture"]), 0)
        self.assertEqual(sqlite.call_args.kwargs["events"], events)

    def test_pipeline_malformed_public_json_fails_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            (out_dir / "events.json").write_text("{not-json", encoding="utf-8")
            with mock.patch.object(run_pipeline.build_index, "OUT_DIR", out_dir), \
                 mock.patch.object(run_pipeline.to_okf, "run", return_value={"failed": False}), \
                 mock.patch.object(run_pipeline.subprocess, "call", return_value=0), \
                 mock.patch.object(run_pipeline.build_index, "build"), \
                 mock.patch.object(run_pipeline.build_pages, "build") as pages, \
                 mock.patch.object(run_pipeline.wiki_index, "build") as wiki:
                self.assertNotEqual(run_pipeline.main(["fixture"]), 0)
        pages.assert_not_called()
        wiki.assert_not_called()

    def test_pipeline_sqlite_failure_fails_before_wiki_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            (out_dir / "events.json").write_text(json.dumps([{"id": "public-1"}]), encoding="utf-8")
            with mock.patch.object(run_pipeline.build_index, "OUT_DIR", out_dir), \
                 mock.patch.object(run_pipeline.to_okf, "run", return_value={"failed": False}), \
                 mock.patch.object(run_pipeline.subprocess, "call", return_value=0), \
                 mock.patch.object(run_pipeline.build_index, "build"), \
                 mock.patch.object(run_pipeline.build_pages, "build") as pages, \
                 mock.patch.object(run_pipeline.build_sqlite, "build", side_effect=RuntimeError("sqlite unavailable")), \
                 mock.patch.object(run_pipeline.wiki_index, "build") as wiki:
                self.assertNotEqual(run_pipeline.main(["fixture"]), 0)
        pages.assert_not_called()
        wiki.assert_not_called()

    def test_link_check_opt_in_orders_rebuild_sqlite_pages_and_wiki(self):
        order = []
        events = [{"id": "public-1"}]
        with mock.patch.dict(os.environ, {"NOTCHIMA_CHECK_LINKS": "1"}, clear=False), \
             mock.patch.object(run_pipeline.to_okf, "run", return_value={"failed": False}), \
             mock.patch.object(run_pipeline.subprocess, "call", side_effect=lambda *args: order.append("validate") or 0), \
             mock.patch.object(run_pipeline.build_index, "build", side_effect=lambda: order.append("index")), \
             mock.patch.object(run_pipeline, "_run_link_checks", side_effect=lambda: order.append("links") or 0), \
             mock.patch.object(run_pipeline, "_load_public_events", return_value=events), \
             mock.patch.object(run_pipeline.build_sqlite, "build", side_effect=lambda **kwargs: order.append("sqlite") or {"events": len(kwargs["events"])}), \
             mock.patch.object(run_pipeline.build_pages, "build", side_effect=lambda **kwargs: order.append("pages")), \
             mock.patch.object(run_pipeline.wiki_index, "build", side_effect=lambda: order.append("wiki")):
            self.assertEqual(run_pipeline.main([]), 0)
        self.assertEqual(order, ["validate", "index", "links", "index", "sqlite", "pages", "wiki"])

    def test_pipeline_propagates_live_collection_failure_before_build_outputs(self):
        with mock.patch.object(run_pipeline.to_okf, "run", return_value={"failed": True, "failed_live": True, "sources": {"x": {"mode": "error"}}}), \
             mock.patch.object(run_pipeline.subprocess, "call", return_value=0), \
             mock.patch.object(run_pipeline.build_index, "build") as build_index, \
             mock.patch.object(run_pipeline.build_pages, "build") as build_pages:
            rc = run_pipeline.main(["x"])
        self.assertNotEqual(rc, 0)
        build_index.assert_not_called()
        build_pages.assert_not_called()


class TestIngestWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        # PyYAML 5/6 resolves the YAML 1.1 key `on` to True.
        cls.raw = raw
        cls.workflow = raw.get("on", raw.get(True))
        cls.job = raw["jobs"]["ingest"]
        cls.steps = cls.job["steps"]

    def test_schedule_has_one_daily_kst_run_and_bounded_concurrency(self):
        schedules = self.workflow["schedule"]
        self.assertEqual([entry["cron"] for entry in schedules], ["0 18 * * *"])
        concurrency = self.raw["concurrency"]
        self.assertTrue(concurrency.get("group"))
        self.assertFalse(concurrency.get("cancel-in-progress"))
        timeout = self.job.get("timeout-minutes")
        self.assertIsInstance(timeout, int)
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 120)

    def test_permissions_and_staged_outputs_are_explicit(self):
        self.assertEqual(self.job["permissions"].get("contents"), "write")
        rendered = "\n".join(str(step.get("run", "")) for step in self.steps)
        self.assertIn("knowledge/", rendered)
        self.assertIn("web/public/data", rendered)
        self.assertIn("git add", rendered)

    def test_link_check_is_opt_in_for_scheduled_pipeline(self):
        pipeline = next(step for step in self.steps if "run_pipeline" in str(step.get("run", "")))
        self.assertIn("NOTCHIMA_CHECK_LINKS", pipeline.get("env", {}))
        self.assertIn("github.event_name", pipeline["env"]["NOTCHIMA_CHECK_LINKS"])
        self.assertNotIn("check_links.py", str(pipeline.get("run", "")))

        with mock.patch.object(run_pipeline.subprocess, "call", return_value=0) as call:
            with mock.patch.dict(os.environ, {"NOTCHIMA_CHECK_LINKS": "0"}, clear=False):
                self.assertEqual(run_pipeline._run_link_checks(), 0)
            call.assert_not_called()
            with mock.patch.dict(os.environ, {"NOTCHIMA_CHECK_LINKS": "1"}, clear=False):
                self.assertEqual(run_pipeline._run_link_checks(), 0)
            call.assert_called_once_with([sys.executable, "-m", "scripts.ops.check_links"])

    def test_failures_are_not_swallowed_by_workflow_shell_or_steps(self):
        for step in self.steps:
            self.assertFalse(step.get("continue-on-error", False), step.get("name"))
            run = str(step.get("run", ""))
            self.assertNotIn("|| echo", run)
            self.assertNotIn("|| true", run)
        pipeline = next(step for step in self.steps if "run_pipeline" in str(step.get("run", "")))
        self.assertNotIn("||", str(pipeline["run"]))


if __name__ == "__main__":
    unittest.main()
