from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Route:
    pattern: str | re.Pattern | None
    name: str
    doc: str
    run: Callable[..., Awaitable]
    admin: bool = False
    priority: int = 0
    event_message_type: Any = None


def install(cls, flt, module_path: str, routes: list[Route]) -> int:
    """把路由安装到 Star 插件类上，重写 __module__ 以配合 AstrBot 插件加载机制。

    priority 必须挂在**第一个**装饰器上：AstrBot 的 get_handler_or_create() 以
    ``__module__`` + ``__name__`` 为键，第二次调用发现已注册就直接返回旧元数据，
    之后传入的 kwargs 会被静默丢弃——所以 admin 路由的 priority 原先全部失效。
    """
    installed = 0
    for route in routes:

        async def handler(self, event, _route=route):
            await _route.run(self.service, event)

        handler.__name__ = route.name
        handler.__qualname__ = f"{cls.__name__}.{route.name}"
        handler.__doc__ = route.doc
        handler.__module__ = module_path

        if route.admin:
            handler = flt.permission_type(
                flt.PermissionType.ADMIN, priority=route.priority
            )(handler)

        if route.event_message_type is not None:
            handler = flt.event_message_type(
                route.event_message_type, priority=route.priority
            )(handler)
        elif route.pattern is not None:
            handler = flt.regex(route.pattern, priority=route.priority)(handler)

        setattr(cls, route.name, handler)
        installed += 1
    return installed
