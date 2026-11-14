"""Authenticated read and control endpoints for durable map jobs."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import Select, select

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.json import JsonValue, parse_json
from telescreen.core.permissions import Permission
from telescreen.db.models import MapItemRow, MapJobRow, MapPartitionRow
from telescreen.workflow.mapreduce.reduce import MapReduceHandler
from telescreen.workflow.mapreduce.repository import status_counts
from telescreen.workflow.mapreduce.service import MapJobService


@dataclass(frozen=True)
class MapJobsRouterDeps:
    """Dependencies needed by map job routes."""

    ctx: ApiContext
    service: MapJobService
    reducer: MapReduceHandler


def build_map_jobs_router(deps: MapJobsRouterDeps) -> APIRouter:
    """Build authenticated map job listing, details, and controls."""
    router: APIRouter = APIRouter()

    @router.get("/api/map-jobs")
    async def list_jobs(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.MAP_JOBS_READ))
        ],
        origin: str | None = None,
        status: str | None = None,
        collector_config_id: str | None = None,
    ) -> JSONResponse:
        """List jobs with count-only metadata."""
        del principal
        query: Select[tuple[MapJobRow]] = (
            select(MapJobRow).order_by(MapJobRow.created_at.desc()).limit(200)
        )
        if origin is not None:
            query = query.where(MapJobRow.origin == origin)
        if status is not None:
            query = query.where(MapJobRow.status == status)
        if collector_config_id is not None:
            query = query.where(MapJobRow.collector_config_id == collector_config_id)
        async with deps.ctx.sessions() as session:
            rows: list[MapJobRow] = list(await session.scalars(query))
        return JSONResponse(
            content={
                "items": [
                    {
                        "id": row.id,
                        "origin": row.origin,
                        "status": row.status,
                        "collector_config_id": row.collector_config_id,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in rows
                ],
                "next_cursor": None,
            }
        )

    @router.get("/api/map-jobs/{job_id}")
    async def get_job(
        job_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.MAP_JOBS_READ))
        ],
    ) -> JSONResponse:
        """Return one job's state and bounded failed shard metadata."""
        del principal
        async with deps.ctx.sessions() as session:
            job: MapJobRow | None = await session.get(MapJobRow, job_id)
            if job is None:
                return JSONResponse(status_code=404, content={"error": {"code": "not_found"}})
            counts: tuple[tuple[str, int], ...] = await status_counts(session, job_id)
            partitions: list[MapPartitionRow] = list(
                await session.scalars(
                    select(MapPartitionRow).where(MapPartitionRow.map_job_id == job_id)
                )
            )
            items: list[MapItemRow] = list(
                await session.scalars(
                    select(MapItemRow)
                    .where(MapItemRow.map_job_id == job_id, MapItemRow.status == "failed")
                    .limit(100)
                )
            )
            finding_count: int = 0
            if job.result_json is not None:
                encoded: JsonValue | None = parse_json(job.result_json).get("findings_total")
                if encoded is not None:
                    count_value: int | None = encoded.as_int()
                    finding_count = count_value if count_value is not None else 0
            return JSONResponse(
                content={
                    "id": job.id,
                    "origin": job.origin,
                    "status": job.status,
                    "collector_config_id": job.collector_config_id,
                    "counts": {key: value for key, value in counts},
                    "partitions_done": sum(1 for row in partitions if row.status == "done"),
                    "partitions_total": len(partitions),
                    "findings_so_far": finding_count,
                    "paused_until": job.paused_until.isoformat() if job.paused_until else None,
                    "failed_shards": [
                        {"shard_key": row.shard_key, "error_code": row.error_code} for row in items
                    ],
                    "result_json": job.result_json,
                }
            )

    @router.post("/api/map-jobs/{job_id}/cancel")
    async def cancel_job(
        job_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.MAP_JOBS_WRITE))
        ],
    ) -> JSONResponse:
        """Cancel active work for a job."""
        del principal
        async with deps.ctx.sessions.begin() as session:
            await deps.reducer.cancel(session, job_id)
        return JSONResponse(content={"id": job_id, "status": "cancelled"})

    @router.post("/api/map-jobs/{job_id}/retry-failed")
    async def retry_failed(
        job_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.MAP_JOBS_WRITE))
        ],
    ) -> JSONResponse:
        """Reset failed shards for an administrator-triggered retry."""
        del principal
        async with deps.ctx.sessions.begin() as session:
            await deps.reducer.retry_failed(session, job_id)
        return JSONResponse(content={"id": job_id, "status": "running"})

    return router
