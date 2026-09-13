"""core 模块初始化。

各模块按域划分，请直接从具体模块导入（如 ``from .core.service import MusicService``）——
在这里再导出会让任意 ``core.*`` 导入都把整条 service 依赖链拉进来。
"""
