
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from requests import Session

from cache import get_redis
from database import get_db


route = APIRouter(prefix="/api", tags=["cache"])


@route.post("/cache/clear",)
def cache_clear(db: Session = Depends(get_db)):
    
    timestamp = datetime.now(timezone.utc).isoformat()

    try:
        # Get the redis cache
        cache = get_redis()
        cache.flushall()

        return {
            "message": "Cache cleared successfully",
            "status_code": 200,
            "data": {},
            "timestamp": timestamp,
        }
    except Exception as e:
        print("Cache not connected", e)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "message": "Failed to connect to cache",
                "status_code": 500,
                "error_code": "CACHE_CONNECTION_ERROR",
                "data": {"error_contents": str(e)},
                "timestamp": timestamp,
            },
        )

