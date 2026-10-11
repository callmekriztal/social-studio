from fastapi import APIRouter

from app.api.routes import health, posts, publish_history, schedule_slots, scheduler, variants

api_router = APIRouter()

# Health router
api_router.include_router(health.router)

# Exact prompt endpoints (/api/posts, /api/variants, /api/schedule-slots, /api/publish-history, /api/scheduler)
api_router.include_router(posts.router, prefix="/api")
api_router.include_router(variants.router, prefix="/api")
api_router.include_router(schedule_slots.router, prefix="/api")
api_router.include_router(publish_history.router, prefix="/api")
api_router.include_router(scheduler.router, prefix="/api")

# Direct mount (/posts, /variants, /schedule-slots, /publish-history, /scheduler) so /api/v1/... works when mounted under /api/v1
api_router.include_router(posts.router)
api_router.include_router(variants.router)
api_router.include_router(schedule_slots.router)
api_router.include_router(publish_history.router)
api_router.include_router(scheduler.router)
