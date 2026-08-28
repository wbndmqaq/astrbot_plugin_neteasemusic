from __future__ import annotations

from . import auth_cmds, detail_cmds, explore_cmds, play_cmds, share_cmds, system_cmds
from .base import Route, install

ALL_ROUTES: list[Route] = [
    *play_cmds.ROUTES,
    *explore_cmds.ROUTES,
    *detail_cmds.ROUTES,
    *auth_cmds.ROUTES,
    *system_cmds.ROUTES,
    *share_cmds.ROUTES,
]

__all__ = ["ALL_ROUTES", "Route", "install"]
