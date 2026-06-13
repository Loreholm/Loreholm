"""In-process Prometheus-style metrics for the local dashboard.

Phase 7.4 of the ArcadeDB migration plan. The dashboard has no external
metrics dependency (keep `requirements-local-dashboard.txt` lean), so we
maintain a tiny counter/gauge dict in memory and expose it over GET
`/metrics` in the Prometheus text exposition format.

Public surface:

- `inc_decision(database_id, decision)` — bump the per-decision counter
  each time the reconciler finalizes a Staging row.
- `set_lag(database_id, seconds)` — set the lag gauge (oldest pending
  Staging row age, per database).
- `set_staging_count(database_id, status, count)` — refresh the pending /
  needs_review / rejected gauges.
- `router` — FastAPI router mounted at `/metrics` by `main.py`.

All writes serialize behind `_lock` so the reconciler's async task and
the FastAPI worker scraping the endpoint don't corrupt the dicts.

No auth on `/metrics`. The local dashboard binds to localhost only, and
Prometheus scrape conventions expect the endpoint to be callable without
credentials from the host.
"""
from __future__ import annotations

import threading
from typing import Dict, Iterable, Tuple

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse


_lock = threading.Lock()

# decision → {database_id → count}
_decisions: Dict[str, Dict[str, int]] = {
    "merge": {},
    "promote": {},
    "needs_review": {},
    "rejected": {},
}

# database_id → lag seconds
_lag: Dict[str, float] = {}

# status → {database_id → count}
_staging: Dict[str, Dict[str, int]] = {
    "pending": {},
    "needs_review": {},
    "rejected": {},
}

# LLM-side dedup adoption:
# - `upsert_without_prior_search_total`: bumped when an upsert happens for an
#   entity name that was not preceded by a `search_similar_entities` call in
#   the same MCP session (per-session search cache lives in mcp_server.py).
# - `upsert_with_merge_into_total`: bumped when an upsert carries an
#   explicit `merge_into` hint.
_upsert_without_prior_search: Dict[str, int] = {}
_upsert_with_merge_into: Dict[str, int] = {}
_upsert_total: Dict[str, int] = {}


def _safe_db(database_id: object) -> str:
    text = str(database_id or "").strip()
    return text or "unknown"


def inc_decision(database_id: object, decision: str) -> None:
    bucket = _decisions.get(decision)
    if bucket is None:
        return
    db = _safe_db(database_id)
    with _lock:
        bucket[db] = bucket.get(db, 0) + 1


def set_lag(database_id: object, seconds: float) -> None:
    db = _safe_db(database_id)
    with _lock:
        _lag[db] = float(max(0.0, seconds))


def clear_lag(database_id: object) -> None:
    db = _safe_db(database_id)
    with _lock:
        _lag.pop(db, None)


def set_staging_count(database_id: object, status: str, count: int) -> None:
    bucket = _staging.get(status)
    if bucket is None:
        return
    db = _safe_db(database_id)
    with _lock:
        bucket[db] = int(max(0, count))


def inc_upsert(
    database_id: object,
    *,
    had_prior_search: bool,
    had_merge_into: bool,
) -> None:
    """Bump LLM-side dedup adoption counters for a single upsert proposal.

    Called once per `EntityInput` in `loreholm_upsert_entities`. The cloud
    MCP server is the only writer; the local dashboard imports the same
    metrics module via the cloud → dashboard sync proxy is *not* the path
    here, this is recorded cloud-side because that's where session
    tracking lives. (When the cloud and dashboard run in the same process
    during local dev, both call into this module.)
    """
    db = _safe_db(database_id)
    with _lock:
        _upsert_total[db] = _upsert_total.get(db, 0) + 1
        if not had_prior_search:
            _upsert_without_prior_search[db] = (
                _upsert_without_prior_search.get(db, 0) + 1
            )
        if had_merge_into:
            _upsert_with_merge_into[db] = (
                _upsert_with_merge_into.get(db, 0) + 1
            )


def upsert_counters(database_id: object) -> Dict[str, int]:
    db = _safe_db(database_id)
    with _lock:
        return {
            "upsert_total": int(_upsert_total.get(db, 0)),
            "upsert_without_prior_search_total": int(
                _upsert_without_prior_search.get(db, 0)
            ),
            "upsert_with_merge_into_total": int(
                _upsert_with_merge_into.get(db, 0)
            ),
        }


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_lines(
    metric: str,
    help_text: str,
    metric_type: str,
    samples: Iterable[Tuple[Dict[str, str], float]],
) -> Iterable[str]:
    yield f"# HELP {metric} {help_text}"
    yield f"# TYPE {metric} {metric_type}"
    emitted = False
    for labels, value in samples:
        emitted = True
        if labels:
            parts = ",".join(
                f'{key}="{_escape_label(val)}"' for key, val in labels.items()
            )
            yield f"{metric}{{{parts}}} {value}"
        else:
            yield f"{metric} {value}"
    if not emitted:
        yield f"{metric} 0"


def render_prometheus_text() -> str:
    with _lock:
        decisions_snapshot = {
            decision: dict(bucket) for decision, bucket in _decisions.items()
        }
        lag_snapshot = dict(_lag)
        staging_snapshot = {
            status: dict(bucket) for status, bucket in _staging.items()
        }
        upsert_total_snapshot = dict(_upsert_total)
        upsert_without_prior_search_snapshot = dict(_upsert_without_prior_search)
        upsert_with_merge_into_snapshot = dict(_upsert_with_merge_into)

    lines = []

    decision_samples: list[Tuple[Dict[str, str], float]] = []
    for decision, bucket in decisions_snapshot.items():
        for db, count in bucket.items():
            decision_samples.append(
                ({"database_id": db, "decision": decision}, float(count))
            )
    lines.extend(
        _format_lines(
            "reconciler_decisions_total",
            "Total reconciler decisions emitted by the staging sweep.",
            "counter",
            decision_samples,
        )
    )

    lag_samples = [({"database_id": db}, float(v)) for db, v in lag_snapshot.items()]
    lines.extend(
        _format_lines(
            "reconciler_pending_lag_seconds",
            "Age of the oldest pending Staging row in seconds.",
            "gauge",
            lag_samples,
        )
    )

    staging_samples: list[Tuple[Dict[str, str], float]] = []
    for status, bucket in staging_snapshot.items():
        for db, count in bucket.items():
            staging_samples.append(
                ({"database_id": db, "status": status}, float(count))
            )
    lines.extend(
        _format_lines(
            "reconciler_staging_rows",
            "Count of Staging vertices grouped by status.",
            "gauge",
            staging_samples,
        )
    )

    lines.extend(
        _format_lines(
            "upsert_total",
            "Total `loreholm_upsert_entities` proposals received.",
            "counter",
            [
                ({"database_id": db}, float(v))
                for db, v in upsert_total_snapshot.items()
            ],
        )
    )
    lines.extend(
        _format_lines(
            "upsert_without_prior_search_total",
            "Upserts where no `search_similar_entities` call preceded the "
            "proposal in the same MCP session.",
            "counter",
            [
                ({"database_id": db}, float(v))
                for db, v in upsert_without_prior_search_snapshot.items()
            ],
        )
    )
    lines.extend(
        _format_lines(
            "upsert_with_merge_into_total",
            "Upserts that carried an explicit `merge_into` dedup hint.",
            "counter",
            [
                ({"database_id": db}, float(v))
                for db, v in upsert_with_merge_into_snapshot.items()
            ],
        )
    )

    return "\n".join(lines) + "\n"


router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
def get_metrics() -> PlainTextResponse:
    return PlainTextResponse(
        content=render_prometheus_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
