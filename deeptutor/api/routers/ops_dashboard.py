"""Ops dashboard API (RIC-720, D3) — read-only aggregation endpoints.

Admin-gated, aggregation-only: the dashboard repurposes data the engine
already writes (order ledger, session store, mastery-path progress). No new
event engine, no writes.

    GET /api/ops/dashboard                    full report
    GET /api/ops/dashboard/export?fmt=csv|json  report rows for download
    GET /api/ops/learners                     account registry (admin)
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response

from deeptutor.api.routers.auth import require_admin
from deeptutor.ops.aggregates import convert_to_csv
from deeptutor.ops.ranges import resolve_range
from deeptutor.ops.source import build_dashboard

router = APIRouter(dependencies=[Depends(require_admin)])


def _report_rows(report: Any) -> list[dict[str, Any]]:
    """Flatten the report into export rows (metrics + funnel + mastery totals)."""
    rows: list[dict[str, Any]] = []
    for metric in report.metrics:
        rows.append(
            {
                "view": "metric",
                "key": metric["key"],
                "label": metric["label"],
                "value": metric["value"],
                "unit": metric.get("unit", ""),
            }
        )
    funnel = report.funnel
    rows.append(
        {
            "view": "funnel",
            "key": "funnel",
            "label": "Funnel",
            "value": (
                f"visitors={funnel.get('visitors', 0)} "
                f"active={funnel.get('active', 0)} "
                f"engaged={funnel.get('engaged_sessions', 0)} "
                f"converted={funnel.get('converted', 0)}"
            ),
            "unit": "",
        }
    )
    mastery_totals = report.mastery.get("totals") if isinstance(report.mastery, dict) else {}
    if isinstance(mastery_totals, dict):
        rows.append(
            {
                "view": "mastery",
                "key": "mastery",
                "label": "Mastery totals",
                "value": (
                    f"paths={mastery_totals.get('path_count', 0)} "
                    f"kps={mastery_totals.get('kp_count', 0)} "
                    f"mastered={mastery_totals.get('mastered_kp_count', 0)} "
                    f"rate={mastery_totals.get('mastery_rate_pct', 0)}%"
                ),
                "unit": "",
            }
        )
    return rows


@router.get("", tags=["ops-dashboard"])
async def get_dashboard(
    range: str = Query(default="30d", pattern="^(7d|30d|90d|all)$"),
) -> dict[str, Any]:
    """Full ops dashboard report for the requested range."""
    trange = resolve_range(range)
    report = await build_dashboard(trange)
    return report.to_dict()


@router.get("/export", tags=["ops-dashboard"])
async def export_dashboard(
    range: str = Query(default="30d", pattern="^(7d|30d|90d|all)$"),
    fmt: Literal["csv", "json"] = Query(default="csv"),
) -> Response:
    """Download the report as flat CSV or JSON (paid perk surface)."""
    trange = resolve_range(range)
    report = await build_dashboard(trange)
    if fmt == "json":
        payload = _report_rows(report)
        import json

        body = json.dumps(payload, ensure_ascii=False, indent=2)
        return Response(
            content=body,
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="ops_dashboard_{trange.key}.json"'
            },
        )
    rows = _report_rows(report)
    body = convert_to_csv(rows)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="ops_dashboard_{trange.key}.csv"'
        },
    )


@router.get("/learners", tags=["ops-dashboard"])
async def get_learners() -> dict[str, Any]:
    """Account registry the dashboard uses for per-learner views."""
    report = await build_dashboard(resolve_range("all"))
    return {"learners": report.learners, "count": len(report.learners)}


__all__ = ["router"]