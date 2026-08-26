"""周期回顾调度器（FR-09）：APScheduler 定时生成周记/月报。

- 周记：每周一 08:00 生成上一周回顾。
- 月报：每月 1 日 08:00 生成上月回顾。
- 复用 ReviewService.generate_review 单一入口；无 DASHSCOPE_API_KEY 时自动跳过（不报错）。
- 随 FastAPI lifespan 启停；调度失败不影响主流程。
"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.logging import get_logger
from app.db.engine import session_scope
from app.services.review_service import ReviewService

log = get_logger("scheduler.review")


def _generate(period_type: str) -> None:
    """后台任务：生成指定周期回顾（自带 DB 会话）。"""
    if not settings.dashscope_api_key:
        log.info("未配置 DASHSCOPE_API_KEY，跳过周期回顾（%s）", period_type)
        return
    try:
        svc = ReviewService()
        with session_scope() as db:
            result = svc.generate_review(db, period_type)
        log.info("周期回顾完成：%s", result)
    except Exception as e:  # 调度任务失败不影响其他
        log.warning("周期回顾失败（%s）：%s", period_type, e)


class ReviewScheduler:
    """封装 APScheduler 后台调度器，随应用启停。"""

    def __init__(self) -> None:
        self._scheduler: BackgroundScheduler | None = None

    def start(self) -> None:
        if self._scheduler is not None and self._scheduler.running:
            return
        try:
            self._scheduler = BackgroundScheduler(daemon=True)
            # 每周一 08:00 周记
            self._scheduler.add_job(
                _generate, CronTrigger(day_of_week="mon", hour=8, minute=0),
                args=["week"], id="weekly-review", replace_existing=True,
            )
            # 每月 1 日 08:00 月报
            self._scheduler.add_job(
                _generate, CronTrigger(day=1, hour=8, minute=0),
                args=["month"], id="monthly-review", replace_existing=True,
            )
            self._scheduler.start()
            log.info("周期回顾调度已启动（周记：周一 08:00；月报：每月 1 日 08:00）")
        except Exception as e:  # pragma: no cover
            log.warning("周期回顾调度启动失败：%s", e)
            self._scheduler = None

    def stop(self) -> None:
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:  # pragma: no cover
                pass
            self._scheduler = None
