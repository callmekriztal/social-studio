import logging
import signal
import sys
import time
from datetime import datetime
from typing import Callable, List, Optional
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.scheduler.service import SchedulerService
from app.services.publishing_service import PublishOutcome

logger = logging.getLogger(__name__)


class SchedulerWorker:
    """Background worker daemon that polls for and processes due schedule slots."""

    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        poll_interval: Optional[float] = None,
        batch_size: Optional[int] = None,
    ):
        settings = get_settings()
        self.session_factory = session_factory
        self.poll_interval = (
            poll_interval
            if poll_interval is not None
            else settings.SCHEDULER_POLL_INTERVAL_SECONDS
        )
        self.batch_size = (
            batch_size
            if batch_size is not None
            else settings.SCHEDULER_BATCH_SIZE
        )
        self._running = False
        self._stop_requested = False

    def run_once(self, current_time: Optional[datetime] = None) -> List[PublishOutcome]:
        """Execute a single polling iteration for due slots.

        Opens a dedicated database session, queries and processes due slots,
        and guarantees session cleanup.
        """
        db = self.session_factory()
        try:
            return SchedulerService.process_due_slots(
                db=db,
                current_time=current_time,
                batch_size=self.batch_size,
            )
        finally:
            if self.session_factory is SessionLocal:
                db.close()

    def start(self) -> None:
        """Start the worker polling loop until stopped."""
        self._running = True
        self._stop_requested = False
        self._setup_signals()
        logger.info(
            "Scheduler worker started (poll_interval=%.1fs, batch_size=%d).",
            self.poll_interval,
            self.batch_size,
        )

        while not self._stop_requested:
            try:
                outcomes = self.run_once()
                if outcomes:
                    logger.info("Processed %d due slots in this cycle.", len(outcomes))
            except Exception as e:
                logger.error("Error in scheduler loop: %s", e, exc_info=True)

            # Interruptible sleep
            elapsed = 0.0
            sleep_step = 0.2
            while elapsed < self.poll_interval and not self._stop_requested:
                time.sleep(sleep_step)
                elapsed += sleep_step

        self._running = False
        logger.info("Scheduler worker stopped cleanly.")

    def stop(self) -> None:
        """Signal the worker to stop gracefully."""
        logger.info("Graceful shutdown requested for scheduler worker...")
        self._stop_requested = True

    def _setup_signals(self) -> None:
        """Attach SIGINT and SIGTERM handlers for graceful termination."""
        def handler(signum, frame):
            logger.info("Received termination signal (%s). Shutting down...", signum)
            self.stop()

        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except (ValueError, AttributeError):
            # Non-main thread or unsupported environment (e.g. within certain test runners)
            pass


def run_worker() -> None:
    """CLI entry point for running the scheduler worker daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    worker = SchedulerWorker()
    worker.start()


if __name__ == "__main__":
    run_worker()
