"""The HTTP service boundary works under real disjoint PostgreSQL login roles.

Owner interim note (2026-10-16): the inventory-only remote mode these flows exercised
was removed by the service split (design-service-split.md section 3, default 5), so
its runtime fixtures no longer exist. svc-p08-verification-deploy rewrites this module
for the three-service topology; until then it is collected as an explicit skip.
"""

import pytest

pytest.skip(
    "inventory-only remote mode removed; svc-p08 rewrites this PostgreSQL flow",
    allow_module_level=True,
)
