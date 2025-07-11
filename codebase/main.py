from datetime import datetime
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi_events.middleware import EventHandlerASGIMiddleware
from fastapi_events.handlers.local import local_handler
from functools import lru_cache
from starlette.middleware.sessions import SessionMiddleware
from fastapi_pagination import add_pagination
import asyncio

from env_variables import setting
from apis import authentication, market_data, satellites, weather, farm, user, cache, report
    
# from redis_listener import redis_listener


description = """
GenxAI APIs for managing agricultural data and services.
"""
app = FastAPI(
    title="GenxAI",
    docs_url="/",
    redoc_url=None,
    description=description,
    version="0.0.1",
    swagger_ui_parameters={"defaultModelsExpandDepth": -1},
)

add_pagination(app)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="GenxAI",
        version="0.1.0",
        description="agricultural data and services APIs",
        routes=app.routes,
    )
    app.openapi_schema = openapi_schema
    return app.openapi_schema



app.openapi = custom_openapi

# System middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=setting.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["captcha-id"],
)
app.add_middleware(SessionMiddleware, secret_key=setting.secret_key)

app.include_router(authentication.route)
app.include_router(market_data.route)
app.include_router(satellites.route)
app.include_router(weather.route)
app.include_router(farm.route)
app.include_router(user.route)
app.include_router(cache.route)
app.include_router(report.route)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat() + 'Z'}
