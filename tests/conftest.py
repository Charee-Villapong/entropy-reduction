"""Shared fixtures and sample-payload builders for the acceptance tests.

These tests were written from docs/SPEC.md only, before any implementation
existed.  Where the SPEC does not pin down an exact field name for a unit
*input* (outputs are pinned by SPEC section 3), the builders below use the
names documented in each test module's docstring.  Those choices are part
of the questions reported back with this test suite.

Design note: no test module imports ``entropy_reduction.*`` at module
level.  Every test loads the unit it needs through the ``unit`` fixture
(inside the test body), so with no implementation present pytest still
*collects* every test and each one fails with ModuleNotFoundError --
a clean "red" state caused only by the missing implementation.
"""

import copy
import importlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures"

for _candidate in (REPO_ROOT, REPO_ROOT / "src"):
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

# ---------------------------------------------------------------------------
# Sample data (all values are fictional test data, no real values anywhere)
# ---------------------------------------------------------------------------

VALID_CONFIG = {
    "bloat_md_lines": 300,
    "unused_days": 90,
    "preservation_pass_line": 1.0,
    "harness_state_dir": str(FIXTURES / "empty_state"),
    "ledger_target": "task-ledger",
}

SNAPSHOT = {
    "kind": "snapshot",
    "version": 1,
    "taken_at": "2026-11-08",
    "totals": {
        "repos": 2,
        "code_lines": 200,
        "md_lines": 60,
        "crons": 3,
        "hooks": 2,
        "custom_skills": 4,
        "ledger_open_rows": 5,
        "open_issues": 6,
    },
    "sources": [
        {
            "type": "harness_run",
            "run_id": "run-20261108",
            "candidates_by_state": {"needs_review": 2, "done": 5},
        }
    ],
}

CANDIDATES = {
    "kind": "candidates",
    "version": 1,
    "items": [
        {
            "id": "cand-1",
            "source": "inventory",
            "target": "docs/big.md",
            "category": "bloat",
            "title": "大きな手順書",
            "detail": "md 450行",
        },
        {
            "id": "cand-2",
            "source": "weekly",
            "target": "tools/dup-b.py",
            "category": "duplicate",
            "title": "重複したツール",
            "detail": "tools/dup-a.py と同一シグネチャ",
        },
    ],
}

FULL_EVIDENCE_DELETE = {
    "last_used": "2026-08-01",
    "references": ["docs/index.md"],
    "impact": "参照は索引1件のみで移行不要",
    "proposed_verdict": "delete",
    "migration_target": None,
    "reason": "未使用期間が長く参照も限定的",
}

FULL_EVIDENCE_MERGE = {
    "last_used": "2026-10-01",
    "references": [],
    "impact": "統合先へ寄せれば利用側への影響なし",
    "proposed_verdict": "merge",
    "migration_target": "tools/dup-a.py",
    "reason": "同一シグネチャの重複",
}

JUDGED = {
    "kind": "judged",
    "version": 1,
    "items": [
        {
            "candidate_id": "cand-1",
            "status": "judged",
            "evidence": {
                "last_used": "2026-08-01",
                "references": ["docs/index.md"],
                "impact": "参照は索引1件のみで移行不要",
            },
            "verdict": "delete",
            "migration_target": None,
            "reason": "未使用期間が長く参照も限定的",
        },
        {
            "candidate_id": "cand-2",
            "status": "needs_check",
            "evidence": {
                "last_used": "2026-10-01",
                "references": [],
                "impact": None,
            },
            "verdict": None,
            "migration_target": None,
            "reason": "証拠が不足している",
        },
    ],
}

LEDGER_ROWS = {
    "kind": "ledger_rows",
    "version": 1,
    "rows": [
        {
            "task": "大きな手順書 docs/big.md",
            "status": "未着手",
            "reason": "",
            "check_method": "",
            "due_date": "2026-11-15",
            "link": "cand-1",
            "updated_at": "2026-11-08",
        }
    ],
}

EXECUTION_RECORD = {
    "kind": "execution_record",
    "version": 1,
    "executed_at": "2026-11-08",
    "items": [{"candidate_id": "cand-1", "action": "delete", "result": "done"}],
    "counts": {"total": 1, "executed": 1},
}

PRESERVATION_RESULT = {
    "kind": "preservation_result",
    "version": 1,
    "preservation_rate": 1.0,
    "reachability": 1.0,
    "pass": True,
}

REMEASURE_REPORT = {
    "kind": "remeasure_report",
    "version": 1,
    "preservation_pass": True,
    "reachability": 1.0,
    "reduced": {"code_lines": 100, "md_lines": 150},
    "totals_trend": {
        "before": {"code_lines": 1000, "md_lines": 500},
        "after": {"code_lines": 900, "md_lines": 350},
    },
}

# Real-value field names that must never appear in any exchange format (D4).
FORBIDDEN_VALUE_FIELDS = [
    "content",
    "file_content",
    "raw_text",
    "body",
    "password",
    "token",
    "api_key",
    "secret",
    "private_key",
    "connection_string",
    "connection_info",
    "personal_info",
    "credential",
    "credentials",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def unit():
    """Return a loader that imports entropy_reduction.<name> on call.

    The import happens inside the test body, so a missing implementation
    fails the test itself instead of breaking collection.
    """

    def _load(name):
        return importlib.import_module("entropy_reduction.%s" % name)

    return _load


@pytest.fixture
def cfg_get():
    """Read a value from a loaded Config, whether dict or object."""

    def _get(config, key):
        if isinstance(config, dict):
            return config[key]
        return getattr(config, key)

    return _get


@pytest.fixture
def load_config(tmp_path, unit):
    """Return a callable that writes a config JSON and loads it.

    Keyword arguments: ``overrides`` (merged into the valid config),
    ``raw`` (use this dict instead of the valid config), ``drop``
    (keys to remove before writing).
    """

    def _load(overrides=None, raw=None, drop=()):
        data = copy.deepcopy(VALID_CONFIG if raw is None else raw)
        if overrides:
            data.update(overrides)
        for key in drop:
            data.pop(key, None)
        path = tmp_path / "config.local.json"
        path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        return unit("config").load_config(str(path))

    return _load


@pytest.fixture
def snapshot():
    return copy.deepcopy(SNAPSHOT)


@pytest.fixture
def candidates_payload():
    return copy.deepcopy(CANDIDATES)


@pytest.fixture
def judged_payload():
    return copy.deepcopy(JUDGED)


@pytest.fixture
def make_measure_payload():
    def _make(targets=None, counts=None, taken_at="2026-11-08"):
        return {
            "kind": "measure_input",
            "version": 1,
            "taken_at": taken_at,
            "targets": copy.deepcopy(
                targets
                if targets is not None
                else [
                    {"name": "repo-a", "code_lines": 120, "md_lines": 40},
                    {"name": "repo-b", "code_lines": 80, "md_lines": 20},
                ]
            ),
            "counts": copy.deepcopy(
                counts
                if counts is not None
                else {
                    "crons": 3,
                    "hooks": 2,
                    "custom_skills": 4,
                    "ledger_open_rows": 5,
                    "open_issues": 6,
                }
            ),
        }

    return _make


@pytest.fixture
def make_detect_payload(snapshot):
    def _make(items=None, inventory_candidates=None, snap=None):
        return {
            "kind": "detect_input",
            "version": 1,
            "snapshot": copy.deepcopy(snap if snap is not None else snapshot),
            "inventory_candidates": copy.deepcopy(
                inventory_candidates if inventory_candidates is not None else []
            ),
            "items": copy.deepcopy(
                items
                if items is not None
                else [
                    {
                        "target": "docs/big.md",
                        "file_type": "md",
                        "md_lines": 450,
                        "code_lines": 0,
                        "last_used_days_ago": 10,
                        "signature": "sig-big",
                    },
                    {
                        "target": "docs/small.md",
                        "file_type": "md",
                        "md_lines": 100,
                        "code_lines": 0,
                        "last_used_days_ago": 10,
                        "signature": "sig-small",
                    },
                    {
                        "target": "tools/old.py",
                        "file_type": "code",
                        "md_lines": 0,
                        "code_lines": 200,
                        "last_used_days_ago": 200,
                        "signature": "sig-old",
                    },
                    {
                        "target": "tools/dup-a.py",
                        "file_type": "code",
                        "md_lines": 0,
                        "code_lines": 50,
                        "last_used_days_ago": 5,
                        "signature": "sig-dup",
                    },
                    {
                        "target": "tools/dup-b.py",
                        "file_type": "code",
                        "md_lines": 0,
                        "code_lines": 50,
                        "last_used_days_ago": 5,
                        "signature": "sig-dup",
                    },
                ]
            ),
        }

    return _make


@pytest.fixture
def make_judge_payload(candidates_payload):
    def _make(evidence=None, candidates=None):
        return {
            "kind": "judge_input",
            "version": 1,
            "candidates": copy.deepcopy(
                candidates if candidates is not None else candidates_payload
            ),
            "evidence": copy.deepcopy(
                evidence
                if evidence is not None
                else {
                    "cand-1": copy.deepcopy(FULL_EVIDENCE_DELETE),
                    "cand-2": copy.deepcopy(FULL_EVIDENCE_MERGE),
                }
            ),
        }

    return _make


@pytest.fixture
def make_execute_payload():
    def _make(rows=None):
        return {
            "kind": "ledger_rows",
            "version": 1,
            "rows": copy.deepcopy(
                rows
                if rows is not None
                else [
                    {
                        "candidate_id": "cand-1",
                        "task": "大きな手順書 docs/big.md",
                        "status": "未着手",
                        "reason": "",
                        "check_method": "",
                        "due_date": "2026-11-15",
                        "link": "cand-1",
                        "updated_at": "2026-11-08",
                        "target": "docs/big.md",
                        "action": "delete",
                        "user_decision": "delete",
                    },
                    {
                        "candidate_id": "cand-2",
                        "task": "重複したツール tools/dup-b.py",
                        "status": "未着手",
                        "reason": "",
                        "check_method": "",
                        "due_date": "2026-11-15",
                        "link": "cand-2",
                        "updated_at": "2026-11-08",
                        "target": "tools/dup-b.py",
                        "action": "merge",
                        "migration_target": "tools/dup-a.py",
                        "user_decision": "merge",
                    },
                ]
            ),
        }

    return _make


@pytest.fixture
def make_preservation_payload():
    def _make(questions=None, answers=None, item_locations=None):
        return {
            "kind": "preservation_input",
            "version": 1,
            "questions": copy.deepcopy(
                questions
                if questions is not None
                else [
                    {"id": "q1", "category": "accident", "item_id": "rule-1"},
                    {"id": "q2", "category": "exception", "item_id": "rule-2"},
                    {"id": "q3", "category": "normal", "item_id": "rule-3"},
                    {"id": "q4", "category": "normal", "item_id": "rule-4"},
                ]
            ),
            "answers": copy.deepcopy(
                answers
                if answers is not None
                else [
                    {"question_id": "q1", "correct": True},
                    {"question_id": "q2", "correct": True},
                    {"question_id": "q3", "correct": True},
                    {"question_id": "q4", "correct": False},
                ]
            ),
            "item_locations": copy.deepcopy(
                item_locations
                if item_locations is not None
                else {
                    "rule-1": ["md"],
                    "rule-2": ["code"],
                    "rule-3": ["md"],
                    "rule-4": ["md", "code"],
                }
            ),
        }

    return _make


@pytest.fixture
def make_remeasure_payload(snapshot):
    def _make(before=None, after=None, preservation_result=None):
        before_snap = copy.deepcopy(before if before is not None else snapshot)
        after_snap = copy.deepcopy(after if after is not None else snapshot)
        if before is None:
            before_snap["totals"].update({"code_lines": 1000, "md_lines": 500})
        if after is None:
            after_snap["totals"].update({"code_lines": 900, "md_lines": 350})
        return {
            "kind": "remeasure_input",
            "version": 1,
            "before": before_snap,
            "after": after_snap,
            "preservation_result": copy.deepcopy(
                preservation_result
                if preservation_result is not None
                else PRESERVATION_RESULT
            ),
        }

    return _make
