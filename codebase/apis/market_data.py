from fastapi import APIRouter, Depends, Response, Request, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from helper_functions.utility import has_permission
from database import get_db
from models import Market, User
import requests
from env_variables import setting
import json
from datetime import datetime, timedelta, timezone
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
import redis
from functools import lru_cache
import hashlib
import time
from typing import List, Dict, Any, Optional
from contextlib import contextmanager
import logging


def calculate_cache_expiry() -> int:
    """Calculate seconds until 10 AM next day"""
    now = datetime.now()
    if now.hour < 10:
        target = now.replace(hour=10, minute=0, second=0, microsecond=0)
    else:
        tomorrow = now + timedelta(days=1)
        target = tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)

    delta = target - now
    return int(delta.total_seconds())


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

redis_pool = redis.ConnectionPool(
    host=setting.redis_host,
    port=setting.redis_port,
    decode_responses=True,
    max_connections=10,
)

GOV_API_KEY = setting.GOV_API_KEY
RECORD_LIMIT = 5000
BATCH_SIZE = 1000
DEFAULT_PAGE_SIZE = 50

route = APIRouter(prefix="/api", tags=["Market"])


def get_redis_client():
    """Get a Redis client from the connection pool"""
    return redis.Redis(connection_pool=redis_pool)


@contextmanager
def redis_error_handling():
    """Context manager for handling Redis errors"""
    try:
        yield
    except redis.RedisError as e:
        logger.error(f"Redis error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in Redis operation: {e}")


def generate_cache_key(
    api_key: str,
    date: str,
    page: int,
    page_size: int,
    filters: Dict[str, Optional[str]] = None,
) -> str:
    """Generate a unique cache key based on API key, date, pagination params, and optional filters"""
    if filters is None:
        filters = {}
    filter_str = ":".join([f"{k}={v or 'all'}" for k, v in sorted(filters.items())])
    key_str = f"{api_key}:{date}:{page}:{page_size}:{filter_str}"
    return hashlib.md5(key_str.encode()).hexdigest()


def fetch_from_cache(
    api_key: str,
    today_date: str,
    previous_date: str,
    page: int,
    page_size: int,
    filters: Dict[str, Optional[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Attempt to fetch data from Redis cache with pagination and optional filter support"""
    if filters is None:
        filters = {}

    today_cache_key = generate_cache_key(api_key, today_date, page, page_size, filters)
    yesterday_cache_key = generate_cache_key(
        api_key, previous_date, page, page_size, filters
    )

    filter_str = ":".join([f"{k}={v or 'all'}" for k, v in sorted(filters.items())])
    today_meta_key = f"{api_key}:{today_date}:{filter_str}:metadata"
    yesterday_meta_key = f"{api_key}:{previous_date}:{filter_str}:metadata"

    with redis_error_handling():
        redis_client = get_redis_client()
        cached_today = redis_client.get(today_cache_key)
        cached_yesterday = redis_client.get(yesterday_cache_key)
        today_meta = redis_client.get(today_meta_key)
        yesterday_meta = redis_client.get(yesterday_meta_key)

        if cached_today and cached_yesterday and today_meta and yesterday_meta:
            logger.info(f"Returning cached data with pagination and filters: {filters}")
            return {
                "today": {
                    "items": json.loads(cached_today),
                    "metadata": json.loads(today_meta),
                },
                "yesterday": {
                    "items": json.loads(cached_yesterday),
                    "metadata": json.loads(yesterday_meta),
                },
                "source": "cache",
            }

    return None


def store_in_cache(
    api_key: str,
    today_date: str,
    previous_date: str,
    today_data: Dict,
    yesterday_data: Dict,
    page: int,
    page_size: int,
    filters: Dict[str, Optional[str]] = None,
) -> None:
    """Store fetched data in Redis cache with pagination and optional filter support"""
    if filters is None:
        filters = {}

    today_cache_key = generate_cache_key(api_key, today_date, page, page_size, filters)
    yesterday_cache_key = generate_cache_key(
        api_key, previous_date, page, page_size, filters
    )

    filter_str = ":".join([f"{k}={v or 'all'}" for k, v in sorted(filters.items())])
    today_meta_key = f"{api_key}:{today_date}:{filter_str}:metadata"
    yesterday_meta_key = f"{api_key}:{previous_date}:{filter_str}:metadata"

    cache_expiry = calculate_cache_expiry()

    with redis_error_handling():
        redis_client = get_redis_client()

        redis_client.setex(
            today_cache_key, cache_expiry, json.dumps(today_data["items"])
        )
        redis_client.setex(
            yesterday_cache_key, cache_expiry, json.dumps(yesterday_data["items"])
        )

        redis_client.setex(
            today_meta_key,
            cache_expiry,
            json.dumps(
                {
                    "total_items": today_data["metadata"]["total_items"],
                    "total_pages": today_data["metadata"]["total_pages"],
                }
            ),
        )
        redis_client.setex(
            yesterday_meta_key,
            cache_expiry,
            json.dumps(
                {
                    "total_items": yesterday_data["metadata"]["total_items"],
                    "total_pages": yesterday_data["metadata"]["total_pages"],
                }
            ),
        )

        expiry_time = (datetime.now() + timedelta(seconds=cache_expiry)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        logger.info(f"Data cached with filters: {filters} - expires at {expiry_time}")


def clean_old_data(db: Session, delete_date: datetime.date) -> None:
    """Clean up old market data"""
    try:
        rows_deleted = (
            db.query(Market).filter(Market.arrival_date == delete_date).delete()
        )
        db.commit()
        logger.info(f"Deleted {rows_deleted} old records from {delete_date}")
    except Exception as e:
        db.rollback()
        logger.error(f"Database cleanup error: {e}")


def fetch_from_gov_api() -> List[Dict]:
    """Fetch data from government API"""
    api_url = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"
    params = {
        "api-key": setting.GOV_API_KEY,
        "format": "json",
        "limit": RECORD_LIMIT,
    }

    try:
        logger.info("Fetching data from government API...")
        response = requests.get(api_url, params=params, timeout=15)

        if response.status_code != 200:
            logger.error(f"API error: {response.status_code} - {response.text}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "message": "GOV api error",
                    "status_code": 500,
                    "error_code": "INTERNAL_SERVER_ERROR",
                    "data": "{}",
                },
            )

        data = response.json()
        return data.get("records", [])
    except requests.RequestException as e:
        logger.error(f"Request error when fetching from gov API: {e}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "message": "An unexpected error connecting to the government API",
                "status_code": 500,
                "error_code": "INTERNAL_SERVER_ERROR",
                "data": str(e),
            },
        )

    except Exception as e:
        logger.error(f"Unexpected error when fetching from gov API: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "message": "An error occurred while fetching market data",
                "status_code": 500,
                "error_code": "INTERNAL_SERVER_ERROR",
                "data": str(e),
            },
        )


def batch_insert_to_db(
    db: Session, records: List[Dict], today_date: datetime.date
) -> None:
    """Insert records to database in batches"""
    try:
        existing_records = set(
            (
                item.state,
                item.district,
                item.market,
                item.commodity,
                item.variety,
                item.grade,
            )
            for item in db.query(Market).filter(Market.arrival_date == today_date).all()
        )

        new_entries = []
        total_inserted = 0

        for record in records[:RECORD_LIMIT]:
            record_key = (
                record.get("state", ""),
                record.get("district", ""),
                record.get("market", ""),
                record.get("commodity", ""),
                record.get("variety", ""),
                record.get("grade", ""),
            )

            if record_key not in existing_records:
                new_entry = Market(
                    arrival_date=today_date,
                    commodity=record.get("commodity", "") or "",
                    market=record.get("market", "") or "",
                    variety=record.get("variety", "") or "",
                    state=record.get("state", "") or "",
                    district=record.get("district", "") or "",
                    grade=record.get("grade", "") or "",
                    updated_date=datetime.now(timezone.utc),
                    min_price=float(record.get("min_price") or 0),
                    max_price=float(record.get("max_price") or 0),
                    modal_price=float(record.get("modal_price") or 0),
                )
                new_entries.append(new_entry)
                existing_records.add(record_key)

                if len(new_entries) >= BATCH_SIZE:
                    db.add_all(new_entries)
                    db.commit()
                    total_inserted += len(new_entries)
                    new_entries = []

        if new_entries:
            db.add_all(new_entries)
            db.commit()
            total_inserted += len(new_entries)

        logger.info(f"Inserted {total_inserted} new records to database")
    except Exception as e:
        db.rollback()
        logger.error(f"Database insertion error: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "message": "An error saving market data",
                "status_code": 500,
                "error_code": "INTERNAL_SERVER_ERROR",
                "data": str(e),
            },
        )


def serialize_market_data(item):
    """Serialize a market data item"""

    commodity_lower = item.commodity.lower() if item.commodity else ""

    result = {
        "state": item.state,
        "district": item.district,
        "market": item.market,
        "commodity": item.commodity,
        "variety": item.variety,
        "grade": item.grade,
        "arrival_date": item.arrival_date.isoformat(),
        "min_price": item.min_price,
        "max_price": item.max_price,
        "modal_price": item.modal_price,
        "updated_date": item.updated_date.isoformat(),
    }

    return result


def create_lookup_dict(items, key_fields):
    """
    Create a lookup dictionary from a list of items.
    The keys are composite keys created from specified fields.
    """
    lookup = {}
    for item in items:
        # Create composite key from specified fields
        composite_key = tuple(item.get(field, "") for field in key_fields)
        lookup[composite_key] = item
    return lookup


def calculate_price_differences(today_items, yesterday_items):
    """
    Calculate the difference between today's and yesterday's modal prices.
    Add this information to today's items.
    """
    # Define fields that uniquely identify a market item
    key_fields = ["state", "district", "market", "commodity", "variety", "grade"]

    # Create lookup dictionary for yesterday's items
    yesterday_lookup = create_lookup_dict(yesterday_items, key_fields)

    # Enhance today's items with price difference
    for today_item in today_items:
        # Create composite key for current item
        composite_key = tuple(today_item.get(field, "") for field in key_fields)

        # Find corresponding yesterday item if it exists
        yesterday_item = yesterday_lookup.get(composite_key)

        if yesterday_item:
            # Safely convert prices to float, handling None and non-numeric values
            try:
                today_price = float(today_item.get("modal_price", 0) or 0)
                yesterday_price = float(yesterday_item.get("modal_price", 0) or 0)
                print(yesterday_price)
                # Calculate absolute difference
                price_diff = today_price - yesterday_price
                print(price_diff)
                # Calculate percentage difference, avoiding division by zero
                if yesterday_price != 0:
                    percentage_diff = (price_diff / yesterday_price) * 100
                else:
                    # If yesterday was 0 and today is something, that's a 100% increase
                    # If both are 0, there's no percentage change
                    percentage_diff = 0 if price_diff == 0 else 100

                # Add difference data to today's item
                today_item["price_difference_percent"] = round(percentage_diff, 2)
            except (ValueError, TypeError):
                # Handle case where conversion to float fails
                logger.warning(
                    f"Failed to calculate price difference for item: {composite_key}"
                )
                today_item["price_difference_percent"] = 0
        else:
            # No matching yesterday item
            today_item["price_difference_percent"] = 0

    return today_items


def process_cached_data_with_price_differences(cached_data):
    """
    Process cached data to include price differences between today and yesterday.
    """
    if cached_data:
        today_items = cached_data["today"]["items"]
        yesterday_items = cached_data["yesterday"]["items"]

        # Calculate price differences and enhance today's data
        enhanced_today_items = calculate_price_differences(today_items, yesterday_items)

        # Update the cached data with enhanced items
        cached_data["today"]["items"] = enhanced_today_items

    return cached_data


def fetch_from_database(
    db: Session,
    today_date: datetime.date,
    previous_date: datetime.date,
    page: int,
    page_size: int,
    filters: Dict[str, Optional[str]] = None,
    skip_pagination: bool = False,
) -> Dict[str, Any]:
    """
    Fetch market data from database with pagination and optional filtering

    Args:
        filters: Dictionary with filter keys (state, district, commodity, search)
        skip_pagination: If True, return all results without pagination
    """
    if filters is None:
        filters = {}

    # Build the base query with filters
    today_query = db.query(Market).filter(Market.arrival_date == today_date)
    yesterday_query = db.query(Market).filter(Market.arrival_date == previous_date)

    # Apply state filter if provided
    if filters.get("state"):
        today_query = today_query.filter(Market.state.ilike(f"%{filters['state']}%"))
        yesterday_query = yesterday_query.filter(
            Market.state.ilike(f"%{filters['state']}%")
        )

    # Apply district filter if provided
    if filters.get("district"):
        today_query = today_query.filter(
            Market.district.ilike(f"%{filters['district']}%")
        )
        yesterday_query = yesterday_query.filter(
            Market.district.ilike(f"%{filters['district']}%")
        )

    # Apply commodity filter if provided
    if filters.get("commodity"):
        today_query = today_query.filter(
            Market.commodity.ilike(f"%{filters['commodity']}%")
        )
        yesterday_query = yesterday_query.filter(
            Market.commodity.ilike(f"%{filters['commodity']}%")
        )

    # Apply general search filter if provided (search across multiple fields)
    if filters.get("search"):
        search_term = f"%{filters['search']}%"
        today_query = today_query.filter(
            or_(
                Market.state.ilike(search_term),
                Market.district.ilike(search_term),
                Market.market.ilike(search_term),
                Market.commodity.ilike(search_term),
                Market.variety.ilike(search_term),
                Market.grade.ilike(search_term),
            )
        )
        yesterday_query = yesterday_query.filter(
            or_(
                Market.state.ilike(search_term),
                Market.district.ilike(search_term),
                Market.market.ilike(search_term),
                Market.commodity.ilike(search_term),
                Market.variety.ilike(search_term),
                Market.grade.ilike(search_term),
            )
        )

    # Get total counts for pagination
    today_total = today_query.count()
    yesterday_total = yesterday_query.count()

    today_total_pages = (
        (today_total + page_size - 1) // page_size if not skip_pagination else 1
    )
    yesterday_total_pages = (
        (yesterday_total + page_size - 1) // page_size if not skip_pagination else 1
    )

    # Apply ordering
    today_query = today_query.order_by(
        Market.state, Market.district, Market.market, Market.commodity
    )
    yesterday_query = yesterday_query.order_by(
        Market.state, Market.district, Market.market, Market.commodity
    )

    # Apply pagination only if not skipped
    if not skip_pagination:
        offset = (page - 1) * page_size
        today_query = today_query.offset(offset).limit(page_size)
        yesterday_query = yesterday_query.offset(offset).limit(page_size)

    # Fetch the data
    today_items = today_query.all()
    yesterday_items = yesterday_query.all()

    today_data = [serialize_market_data(item) for item in today_items]
    yesterday_data = [serialize_market_data(item) for item in yesterday_items]

    # Calculate price differences and enhance today's data
    enhanced_today_data = calculate_price_differences(today_data, yesterday_data)

    return {
        "today": {
            "items": enhanced_today_data,
            "metadata": {"total_items": today_total, "total_pages": today_total_pages},
        },
        "yesterday": {
            "items": yesterday_data,
            "metadata": {
                "total_items": yesterday_total,
                "total_pages": yesterday_total_pages,
            },
        },
        "source": "database",
    }


def ensure_fresh_data(
    db: Session, today_date: datetime.date, delete_date: datetime.date
) -> str:
    """
    Ensure we have fresh data in the database, fetching from API if needed
    Returns the data source: 'api' if freshly fetched, 'database' if using existing data
    """
    # Clean old data
    clean_old_data(db, delete_date)

    # Check if we need to fetch fresh data
    today_data_count = (
        db.query(func.count(Market.state))
        .filter(Market.arrival_date == today_date)
        .scalar()
    )

    data_source = "database"

    timestamp = datetime.utcnow().isoformat()

    if today_data_count == 0:
        try:
            api_records = fetch_from_gov_api()
            batch_insert_to_db(db, api_records, today_date)
            data_source = "api"
        except Exception as e:
            logger.error(f"Failed to fetch or process API data: {e}")
            import traceback

            logger.error(traceback.format_exc())

            # Return JSONResponse for API endpoint
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "message": "Failed to fetch or process data from government API. Please try again later",
                    "status_code": 500,
                    "error_code": "GOV_API_FETCH_ERROR",
                    "data": {},
                    "timestamp": timestamp,
                },
            )

    return data_source

def get_user_by_id(db: Session, user_id: str):
    user = db.query(User).filter(User.id == user_id, User.is_deleted == False).first()
    if not user:
        return None, JSONResponse(
            status_code=404,
            content={
                "status_code": 404,
                "message": f"User with id '{user_id}' not found",
                "error_code": "USER_NOT_FOUND",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + 'Z'
            }
        )
    return user, None


@route.get(
    "/get_market_price_list",
    summary="Fetch market data with optional filtering",
    response_description="Market data with optional filtering and pagination",
    operation_id="get_market_data",
)
def get_market_data(
    request: Request,
    user_id: str = Query(..., description="User ID for authentication"),
    state: Optional[str] = Query(None, description="Filter by state name"),
    district: Optional[str] = Query(None, description="Filter by district name"),
    commodity: Optional[str] = Query(None, description="Filter by commodity name"),
    search: Optional[str] = Query(None, description="General search across multiple fields"),
    page: int = Query(1, description="Page number", ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, description="Number of items per page", ge=1, le=100),
    response: Response = None,
    db: Session = Depends(get_db),
):
    """
    Fetches market data with optional filtering and pagination.
    """
    start_time = time.time()

    # Validate user
    user, error_response = get_user_by_id(db, user_id)
    if error_response:
        return error_response

    filters = {k: v for k, v in {
        "state": state,
        "district": district,
        "commodity": commodity,
        "search": search,
    }.items() if v is not None}

    is_filtered_request = bool(filters)

    today_date = datetime.now().date()
    previous_date = today_date - timedelta(days=1)
    delete_date = today_date - timedelta(days=2)

    try:
        cached_data = fetch_from_cache(
            user_id, str(today_date), str(previous_date), page, page_size, filters
        )

        if cached_data:
            enhanced_cached_data = process_cached_data_with_price_differences(cached_data)
            today_data = enhanced_cached_data["today"]

            response_time = (time.time() - start_time) * 1000
            cache_expiry = calculate_cache_expiry()
            expiry_time = (datetime.now() + timedelta(seconds=cache_expiry)).strftime("%Y-%m-%d %H:%M:%S")

            message = "Filtered market data fetched from cache!" if is_filtered_request else "Market data fetched from cache!"
            return JSONResponse(
                status_code=200,
                content={
                    "message": message,
                    "status_code": 200,
                    "data": {
                        "items": today_data["items"],
                        "page": page,
                        "page_size": page_size,
                        "total_pages": today_data["metadata"]["total_pages"],
                        "total_items": today_data["metadata"]["total_items"],
                    },
                    "timestamp": datetime.utcnow().isoformat(),
                    "response_time_ms": response_time,
                    "source": "cache",
                    "cache_expires_at": expiry_time,
                    **({"filters_applied": filters} if is_filtered_request else {}),
                },
            )

        data_source = ensure_fresh_data(db, today_date, delete_date)

        result_data = fetch_from_database(
            db, today_date, previous_date, page, page_size, filters
        )

        store_in_cache(
            user_id,
            str(today_date),
            str(previous_date),
            result_data["today"],
            result_data["yesterday"],
            page,
            page_size,
            filters,
        )

        today_data = result_data["today"]
        response_time = (time.time() - start_time) * 1000
        cache_expiry = calculate_cache_expiry()
        expiry_time = (datetime.now() + timedelta(seconds=cache_expiry)).strftime("%Y-%m-%d %H:%M:%S")

        message = "Filtered market data fetched successfully!" if is_filtered_request else "Market data fetched successfully!"
        return JSONResponse(
            status_code=200,
            content={
                "message": message,
                "status_code": 200,
                "data": {
                    "items": today_data["items"],
                    "page": page,
                    "page_size": page_size,
                    "total_pages": today_data["metadata"]["total_pages"],
                    "total_items": today_data["metadata"]["total_items"],
                },
                "timestamp": datetime.utcnow().isoformat(),
                "response_time_ms": response_time,
                "source": data_source,
                "cache_expires_at": expiry_time,
                **({"filters_applied": filters} if is_filtered_request else {}),
            },
        )

    except Exception as e:
        logger.error(f"Error in get_market_data: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "message": "Internal server error",
                "status_code": 500,
                "error_code": "INTERNAL_SERVER_ERROR",
                "error": str(e),
            },
        )


@route.get(
    "/market_data_by_region",
    summary="Fetch all market data for a specific state and commodity",
    response_description="Complete market data for the specified state and commodity",
    operation_id="get_market_data_by_state_commodity",
)
def get_market_data_by_state_commodity(
    request: Request,
    user_id: str = Query(..., description="User ID for authentication"),
    state: str = Query(None, description="State name to filter by"),
    commodity: str = Query(None, description="Commodity name to filter by"),
    response: Response = None,
    db: Session = Depends(get_db),
):
    """
    Fetches market data for a given state and commodity.
    """
    start_time = time.time()

    # Validate user
    user, error_response = get_user_by_id(db, user_id)
    if error_response:
        return error_response

    filters = {k: v for k, v in {"state": state, "commodity": commodity}.items() if v}

    today_date = datetime.now().date()
    previous_date = today_date - timedelta(days=1)
    delete_date = today_date - timedelta(days=2)

    filter_string = ""
    if state:
        filter_string += f":{state}"
    if commodity:
        filter_string += f":{commodity}"
    if not filter_string:
        filter_string = ":all"

    cache_key = f"{user_id}:{today_date}{filter_string}:all_data"
    meta_key = f"{user_id}:{today_date}{filter_string}:all_data_meta"

    try:
        with redis_error_handling():
            redis_client = get_redis_client()
            cached_data = redis_client.get(cache_key)
            meta_data = redis_client.get(meta_key)

            if cached_data and meta_data:
                today_items = json.loads(cached_data)
                metadata = json.loads(meta_data)
                response_time = (time.time() - start_time) * 1000
                cache_expiry = calculate_cache_expiry()
                expiry_time = (datetime.now() + timedelta(seconds=cache_expiry)).strftime("%Y-%m-%d %H:%M:%S")

                message = "Market data fetched from cache!"
                if filters:
                    parts = [f"{k}='{v}'" for k, v in filters.items()]
                    message = f"Market data for {', '.join(parts)} fetched from cache!"

                return JSONResponse(
                    status_code=200,
                    content={
                        "message": message,
                        "status_code": 200,
                        "data": {
                            "items": today_items,
                            "total_items": metadata["total_items"],
                        },
                        "timestamp": datetime.utcnow().isoformat(),
                        "response_time_ms": response_time,
                        "source": "cache",
                        "cache_expires_at": expiry_time,
                        "filters_applied": filters,
                    },
                )

        data_source = ensure_fresh_data(db, today_date, delete_date)

        result_data = fetch_from_database(
            db, today_date, previous_date, 1, DEFAULT_PAGE_SIZE, filters=filters, skip_pagination=True
        )

        today_items = result_data["today"]["items"]
        metadata = {"total_items": len(today_items)}

        with redis_error_handling():
            redis_client = get_redis_client()
            cache_expiry = calculate_cache_expiry()
            redis_client.setex(cache_key, cache_expiry, json.dumps(today_items))
            redis_client.setex(meta_key, cache_expiry, json.dumps(metadata))

        response_time = (time.time() - start_time) * 1000
        expiry_time = (datetime.now() + timedelta(seconds=cache_expiry)).strftime("%Y-%m-%d %H:%M:%S")
        message = "Market data fetched successfully!"
        if filters:
            parts = [f"{k}='{v}'" for k, v in filters.items()]
            message = f"Market data for {', '.join(parts)} fetched successfully!"

        return JSONResponse(
            status_code=200,
            content={
                "message": message,
                "status_code": 200,
                "data": {
                    "items": today_items,
                    "total_items": len(today_items),
                },
                "timestamp": datetime.utcnow().isoformat(),
                "response_time_ms": response_time,
                "source": data_source,
                "cache_expires_at": expiry_time,
                "filters_applied": filters,
            },
        )

    except Exception as e:
        logger.error(f"Error in get_market_data_by_state_commodity: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "message": "Internal server error",
                "status_code": 500,
                "error_code": "INTERNAL_SERVER_ERROR",
                "error": str(e),
            },
        )



@route.get(
    "/market_catalog",
    summary="Fetch unique states, districts, and commodities",
    response_description="Unique market metadata for filtering",
    operation_id="get_market_metadata",
)
def get_market_metadata(
    request: Request,
    user_id: str = Query(..., description="User ID for authentication"),
    response: Response = None,
    db: Session = Depends(get_db),
):
    """
    Fetches all unique states with their nested districts and commodities.
    Ensures today's data is present before processing.
    """
    start_time = time.time()

    # Validate user
    user, error_response = get_user_by_id(db, user_id)
    if error_response:
        return error_response

    today_date = datetime.now().date()
    delete_date = today_date - timedelta(days=2)

    metadata_cache_key = f"{user_id}:market_metadata"

    try:
        with redis_error_handling():
            redis_client = get_redis_client()
            # First check if metadata is already cached
            cached_metadata = redis_client.get(metadata_cache_key)
            if cached_metadata:
                metadata = json.loads(cached_metadata)
                response_time = (time.time() - start_time) * 1000
                return JSONResponse(
                    status_code=200,
                    content={
                        "message": "Market metadata fetched from cache",
                        "status_code": 200,
                        "data": metadata,
                        "timestamp": datetime.utcnow().isoformat(),
                        "response_time_ms": response_time,
                        "source": "cache",
                    },
                )

            # Now check if raw data is cached — same as /market_data_by_region
            raw_data_cache_key = f"{user_id}:{today_date}:all_data"
            raw_data_meta_key = f"{user_id}:{today_date}:all_data_meta"
            cached_data = redis_client.get(raw_data_cache_key)
            meta_data = redis_client.get(raw_data_meta_key)

            if not cached_data or not meta_data:
                # Data not available — fetch from API
                data_source = ensure_fresh_data(db, today_date, delete_date)
                if isinstance(data_source, JSONResponse):
                    return data_source  # early return if error from API

        # Build fresh metadata from DB
        states_data = {}
        state_districts_query = (
            db.query(Market.state, Market.district)
            .filter(
                Market.arrival_date == today_date,
                Market.state != "",
                Market.district != "",
            )
            .distinct()
            .order_by(Market.state, Market.district)
        )

        for state, district in state_districts_query.all():
            if state not in states_data:
                states_data[state] = []
            states_data[state].append(district)

        for state in states_data:
            states_data[state].sort()

        commodities_query = (
            db.query(Market.commodity)
            .filter(Market.arrival_date == today_date, Market.commodity != "")
            .distinct()
            .order_by(Market.commodity)
        )
        commodities = [c[0] for c in commodities_query.all()]

        metadata = {"states": states_data, "commodities": commodities}

        with redis_error_handling():
            redis_client = get_redis_client()
            redis_client.setex(metadata_cache_key, 24 * 60 * 60, json.dumps(metadata))

        response_time = (time.time() - start_time) * 1000
        return JSONResponse(
            status_code=200,
            content={
                "message": "Market metadata fetched successfully",
                "status_code": 200,
                "data": {
                    "states": states_data,
                    "commodities": commodities,
                },
                "timestamp": datetime.utcnow().isoformat(),
                "response_time_ms": response_time,
                "source": "database",
            },
        )

    except Exception as e:
        logger.error(f"Error fetching market metadata: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "message": "Internal server error",
                "status_code": 500,
                "error_code": "INTERNAL_SERVER_ERROR",
                "error": str(e),
            },
        )
