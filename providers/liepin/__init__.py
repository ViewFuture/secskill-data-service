"""猎聘只读岗位搜索 Provider。"""

from providers.liepin.provider import collect_liepin_jobs, liepin_health_snapshot

__all__ = ["collect_liepin_jobs", "liepin_health_snapshot"]
