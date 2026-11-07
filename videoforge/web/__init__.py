"""Web 层：FastAPI 服务 + 任务队列。"""

from .jobs import MANAGER, Job, JobManager
from .server import app, create_app

__all__ = ["app", "create_app", "MANAGER", "Job", "JobManager"]
