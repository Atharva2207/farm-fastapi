from datetime import datetime
from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
import requests
from sqlalchemy.orm import Session
from models import Farm, User
from database import get_db
from datetime import datetime
from env_variables import setting

route = APIRouter(prefix="/api", tags=["Weather"])

DEFAULT_WEATHER_FIELDS = [
    "temperature",
    "temperatureApparent", 
    "humidity",
    "windSpeed",
    "windDirection",
    "precipitationIntensity",
    "precipitationProbability",
    "precipitationType",
    "pressureSeaLevel",
    "uvIndex",
    "visibility",
    "cloudCover"
]

# Utility Functions
def get_farm_by_id(db: Session, farm_id: str):
    """Get farm by ID with error handling"""
    try:
        farm_uuid = uuid.UUID(farm_id)
    except ValueError:
        return None, JSONResponse(
            status_code=400,
            content={
                "status_code": 400,
                "message": "Invalid farm ID format",
                "error_code": "INVALID_FARM_ID",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + 'Z'
            }
        )
    
    # You'll need to replace this with your actual Farm model query
    try:
        farm = db.query(Farm).filter(Farm.id == farm_uuid, Farm.deleted == False).first()
    except Exception as e:
        return None, JSONResponse(
            status_code=500,
            content={
                "status_code": 500,
                "message": "Database error while fetching farm",
                "error_code": "DATABASE_ERROR",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + 'Z'
            }
        )
    
    if not farm:
        return None, JSONResponse(
            status_code=404,
            content={
                "status_code": 404,
                "message": "Farm not found",
                "error_code": "FARM_NOT_FOUND",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + 'Z'
            }
        )
    
    # if not farm.is_active:
    #     return None, JSONResponse(
    #         status_code=403,
    #         content={
    #             "status_code": 403,
    #             "message": "Farm is not active",
    #             "error_code": "FARM_INACTIVE",
    #             "data": {},
    #             "timestamp": datetime.utcnow().isoformat() + 'Z'
    #         }
    #     )
    
    return farm, None

def validate_time_format(start_time: str, end_time: str):
    """Validate time format and logic"""
    try:
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        
        if end_dt <= start_dt:
            return False, JSONResponse(
                status_code=400,
                content={
                    "status_code": 400,
                    "message": "End time must be after start time",
                    "error_code": "INVALID_TIME_RANGE",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + 'Z'
                }
            )
        return True, None
        
    except ValueError:
        return False, JSONResponse(
            status_code=400,
            content={
                "status_code": 400,
                "message": "Invalid time format. Use ISO format: YYYY-MM-DDTHH:MM:SSZ",
                "error_code": "INVALID_TIME_FORMAT",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + 'Z'
            }
        )

def get_user_by_id(db: Session, user_id: str):    
    # Validate UUID format before conversion
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return None, JSONResponse(
            status_code=400,
            content={
                "status_code": 400,
                "message": f"Invalid UUID format: '{user_id}'",
                "error_code": "INVALID_UUID_FORMAT",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + 'Z'
            }
        )

    user = db.query(User).filter(User.id == user_uuid).first()
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


@route.get("/current")
async def get_current_weather(
    user_id: str,
    farm_id: str,
    units: str = "metric",
    db: Session = Depends(get_db)
):
    """
    Get current weather for a specific farm.
    """
    user, error_response = get_user_by_id(db, user_id)
    if error_response:
        return error_response

    farm, error_response = get_farm_by_id(db, farm_id)
    if error_response:
        return error_response

    if str(farm.user_id) != str(user.id):
        return JSONResponse(
            status_code=403,
            content={"detail": "This farm does not belong to the specified user."}
        )

    if not setting.TOMORROW_IO_API_KEY:
        return JSONResponse(
            status_code=500,
            content={"detail": "Weather API key not configured."}
        )

    if units not in ["metric", "imperial"]:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid units. Use 'metric' or 'imperial'."}
        )

    location = f"{farm.lat},{farm.lon}"

    try:
        response = requests.get(
            f"{setting.TOMORROW_IO_BASE_URL}/weather/realtime",
            params={
                'location': location,
                'fields': ','.join(DEFAULT_WEATHER_FIELDS),
                'units': units,
                'apikey': setting.TOMORROW_IO_API_KEY
            },
            timeout=30
        )
        response.raise_for_status()
        weather_data = response.json()

    except requests.Timeout:
        return JSONResponse(
            status_code=500,
            content={"detail": "Weather API request timed out."}
        )
    except requests.RequestException as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Weather API error: {str(e)}"}
        )

    return JSONResponse(
        status_code=200,
        content={
            "status_code": 200,
            "message": "Current weather data retrieved successfully.",
            "data": weather_data,
            "farm_info": {
                "farm_id": str(farm.id),
                "farm_name": farm.farm_name,
                "farmer_name": getattr(farm.farmer, "name", None),
                "latitude": farm.lat,
                "longitude": farm.lon,
                "district": getattr(farm, 'district', None),
                "state": getattr(farm, 'state', None)
            },
            "timestamp": datetime.utcnow().isoformat() + 'Z'
        }
    )


@route.get("/historic")
async def get_historic_weather(
    user_id: str,
    farm_id: str,
    start_date: str,
    end_date: str,
    units: str = "metric",
    db: Session = Depends(get_db)
):
    """
    Get historical weather data for a farm using Open-Meteo.
    """
    user, error_response = get_user_by_id(db, user_id)
    if error_response:
        return error_response

    farm, error_response = get_farm_by_id(db, farm_id)
    if error_response:
        return error_response

    if str(farm.user_id) != str(user.id):
        return JSONResponse(
            status_code=403,
            content={"detail": "This farm does not belong to the specified user."}
        )

    # Validate units
    valid_units = ["metric", "imperial"]
    if units not in valid_units:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid units. Use 'metric' or 'imperial'."}
        )

    # Validate date format
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"detail": "Dates must be in YYYY-MM-DD format."}
        )

    # Configure units for Open-Meteo
    temperature_unit = "celsius" if units == "metric" else "fahrenheit"
    windspeed_unit = "kmh" if units == "metric" else "mph"
    precipitation_unit = "mm" if units == "metric" else "inch"

    # Build request
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": farm.lat,
        "longitude": farm.lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": ",".join([
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "windspeed_10m_max"
        ]),
        "temperature_unit": temperature_unit,
        "windspeed_unit": windspeed_unit,
        "precipitation_unit": precipitation_unit,
        "timezone": "auto"
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        weather_data = response.json()

    except requests.Timeout:
        return JSONResponse(
            status_code=500,
            content={"detail": "Weather API request timed out."}
        )
    except requests.RequestException as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Weather API error: {str(e)}"}
        )

    return JSONResponse(
        status_code=200,
        content={
            "status_code": 200,
            "message": "Historic weather data retrieved successfully.",
            "data": weather_data,
            "farm_info": {
                "farm_id": str(farm.id),
                "farm_name": farm.farm_name,
                "farmer_name": getattr(farm.farmer, "name", None),
                "latitude": farm.lat,
                "longitude": farm.lon,
                "district": getattr(farm, 'district', None),
                "state": getattr(farm, 'state', None)
            },
            "query_info": {
                "start_date": start_date,
                "end_date": end_date,
                "units": units
            },
            "timestamp": datetime.utcnow().isoformat() + 'Z'
        }
    )


@route.get("/forecast")
async def get_forecast_weather(
    user_id: str,
    farm_id: str,
    timesteps: str = "1d",
    units: str = "metric",
    db: Session = Depends(get_db)
):
    """
    Get weather forecast for a specific farm.
    """
    user, error_response = get_user_by_id(db, user_id)
    if error_response:
        return error_response

    farm, error_response = get_farm_by_id(db, farm_id)
    if error_response:
        return error_response

    if str(farm.user_id) != str(user.id):
        return JSONResponse(
            status_code=403,
            content={"detail": "This farm does not belong to the specified user."}
        )

    if not setting.TOMORROW_IO_API_KEY:
        return JSONResponse(
            status_code=500,
            content={"detail": "Weather API key not configured."}
        )

    if units not in ["metric", "imperial"]:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid units. Use 'metric' or 'imperial'."}
        )

    valid_timesteps = ["1h", "1d"]
    if timesteps not in valid_timesteps:
        return JSONResponse(
            status_code=400,
            content={"detail": f"Invalid timesteps for forecast. Use one of: {', '.join(valid_timesteps)}"}
        )

    location = f"{farm.lat},{farm.lon}"

    try:
        response = requests.get(
            f"{setting.TOMORROW_IO_BASE_URL}/timelines",
            params={
                'location': location,
                'fields': ','.join(DEFAULT_WEATHER_FIELDS),
                'timesteps': timesteps,
                'units': units,
                'apikey': setting.TOMORROW_IO_API_KEY
            },
            timeout=30
        )
        response.raise_for_status()
        weather_data = response.json()

    except requests.Timeout:
        return JSONResponse(
            status_code=500,
            content={"detail": "Weather API request timed out."}
        )
    except requests.RequestException as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Weather API error: {str(e)}"}
        )

    return JSONResponse(
        status_code=200,
        content={
            "status_code": 200,
            "message": "Forecast weather data retrieved successfully.",
            "data": weather_data,
            "farm_info": {
                "farm_id": str(farm.id),
                "farm_name": farm.farm_name,
                "farmer_name": getattr(farm.farmer, "name", None),
                "latitude": farm.lat,
                "longitude": farm.lon,
                "district": getattr(farm, 'district', None),
                "state": getattr(farm, 'state', None)
            },
            "query_info": {
                "timesteps": timesteps,
                "units": units
            },
            "timestamp": datetime.utcnow().isoformat() + 'Z'
        }
    )
