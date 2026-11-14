"""Compatibility composer for issue API routes."""

from fastapi import APIRouter

from telescreen.api.routers.issues_actions import build_issues_actions_router
from telescreen.api.routers.issues_comments import build_issues_comments_router
from telescreen.api.routers.issues_common import IssuesRouterDeps
from telescreen.api.routers.issues_detail import build_issues_detail_router
from telescreen.api.routers.issues_list import build_issues_list_router


def build_issues_router(deps: IssuesRouterDeps) -> APIRouter:
    """Build the issue REST API by composing its route modules.

    Routes are copied rather than nested with ``include_router``: FastAPI keeps included
    routers as lazy entries, which hides the routes from the route-permission walk.
    """
    router: APIRouter = APIRouter()
    for part in (
        build_issues_list_router(deps),
        build_issues_detail_router(deps),
        build_issues_actions_router(deps),
        build_issues_comments_router(deps),
    ):
        router.routes.extend(part.routes)
    return router
