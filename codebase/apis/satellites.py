import io
import os
import shutil
import statistics
import traceback
from typing import Literal
import uuid
import rasterio
from sentinelhub import (
    SentinelHubRequest,
    SentinelHubCatalog,
    DataCollection,
    MimeType,
    CRS,
    BBox,
    SHConfig,
)
from sentinelhub.constants import ResamplingType
from fastapi import APIRouter, Depends, HTTPException, status, Response, Query, Request
from fastapi.responses import JSONResponse
from datetime import date, datetime, timedelta, timezone
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import case, func
from PIL import Image, ImageColor
from starlette.background import BackgroundTasks
from rasterio import mask
from pathlib import Path
import numpy as np
import json
import base64
import json
from uuid import uuid4
from cache import get_redis
from env_variables import setting
from database import get_db
from models import Farm, Satellites, PlanetCollections, Indices, User
from database import get_db


route = APIRouter(prefix="/api", tags=["Satellite"])


def get_validated_index(
    db: Session,
    index_code: str,
    satellite_code: str,
    eval_type: Literal["imagery", "statistics"],
):
    columns = {"imagery": "evalscript", "statistics": "statistical_evalscript"}

    selected_index = (
        db.query(Indices, Satellites)
        .filter(Indices.code == index_code)
        .filter(getattr(Indices, columns[eval_type]).isnot(None))
        .filter(Satellites.code == satellite_code)
        .filter(Satellites.name == Indices.satellite)
        .first()
    )

    if not selected_index:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Index not found for the given satellite",
        )

    return selected_index


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
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    # You'll need to replace this with your actual Farm model query
    try:
        farm = db.query(Farm).filter(Farm.id == farm_uuid).first()
    except Exception as e:
        return None, JSONResponse(
            status_code=500,
            content={
                "status_code": 500,
                "message": "Database error while fetching farm",
                "error_code": "DATABASE_ERROR",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    if not farm:
        return None, JSONResponse(
            status_code=404,
            content={
                "status_code": 404,
                "message": "Farm not found",
                "error_code": "FARM_NOT_FOUND",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
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
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))

        if end_dt <= start_dt:
            return False, JSONResponse(
                status_code=400,
                content={
                    "status_code": 400,
                    "message": "End time must be after start time",
                    "error_code": "INVALID_TIME_RANGE",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
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
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
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
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
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
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
    return user, None


def get_farm_geometry(db: Session, user_id: str, farm_id: str):
    user, error_response = get_user_by_id(db, user_id)
    if error_response:
        raise HTTPException(
            status_code=error_response.status_code,
            detail=error_response.content["detail"],
        )

    farm, error_response = get_farm_by_id(db, farm_id)
    if error_response:
        raise HTTPException(
            status_code=error_response.status_code,
            detail=error_response.content["detail"],
        )

    # Validate that the farm belongs to the user
    if str(farm.user_id) != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This farm does not belong to the specified user",
        )

    # Get farm geometry with stored bbox and calculate area
    farm_geom_query = (
        db.query(
            func.ST_AsGeoJSON(Farm.geometry).label("geom"),
            Farm.bbox.label("bbox"),
            func.ST_Area(func.ST_Transform(Farm.geometry, 3857)).label(
                "area_sqm"
            ),  # Area in square meters
        )
        .filter(Farm.user_id == user.id, Farm.id == farm_id)
        .first()
    )

    if not farm_geom_query:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Farm geometry not found"
        )

    # Parse the geometry JSON
    geom_data = json.loads(farm_geom_query.geom)

    # Use stored bbox (it's already a list from JSONB)
    bbox_list = farm_geom_query.bbox if farm_geom_query.bbox else []

    # Add properties with bbox and area
    geom_data["properties"] = {
        "bbox": bbox_list,  # This is already [min_lon, min_lat, max_lon, max_lat]
        "area": float(farm_geom_query.area_sqm)
        / 4047,  # Convert to acres (1 acre = 4047 sqm)
    }

    # Return in the expected format
    return {"geom": json.dumps(geom_data)}


def get_farm(db: Session, user_id: str, farm_id: str):
    """
    Get farm data with geometry and bbox for satellite image generation
    """
    user, error_response = get_user_by_id(db, user_id)
    if error_response:
        raise HTTPException(
            status_code=error_response.status_code,
            detail=error_response.content["detail"],
        )

    farm, error_response = get_farm_by_id(db, farm_id)
    if error_response:
        raise HTTPException(
            status_code=error_response.status_code,
            detail=error_response.content["detail"],
        )

    # Validate that the farm belongs to the user
    if str(farm.user_id) != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This farm does not belong to the specified user",
        )

    # Get farm with geometry, bbox, and area
    farm_data_query = (
        db.query(
            func.ST_AsGeoJSON(Farm.geometry).label("geom"),
            Farm.bbox.label("bbox"),
            func.ST_Area(func.ST_Transform(Farm.geometry, 3857)).label("area_sqm"),
        )
        .filter(Farm.user_id == user.id, Farm.id == farm_id)
        .first()
    )

    if not farm_data_query:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Farm data not found"
        )

    # Parse the geometry JSON and add properties
    geom_data = json.loads(farm_data_query.geom)
    geom_data["properties"] = {
        "bbox": farm_data_query.bbox if farm_data_query.bbox else [],
        "area": float(farm_data_query.area_sqm) / 4047,  # Convert to acres
    }

    return {"geom": json.dumps(geom_data)}


# Optional: Function to manually update bbox for existing farms (if needed)


def remove_folder(path: str):
    if os.path.exists(path):
        shutil.rmtree(path)


@route.get("/available-dates")
async def list_satellite_available_dates(
    request: Request,
    user_id: str,
    farm_id: str,
    satellite: str = None,  # NEW: Optional satellite filter parameter
    start_date: date = None,
    end_date: date = None,
    response: Response = None,
    db: Session = Depends(get_db),
    cache=Depends(get_redis),
):
    """
    Get available satellite dates for a specific farm.

    Args:
        satellite (str, optional): Filter by specific satellite code (e.g., "S1", "S2", "S6").
                                  If not provided, returns dates for all available satellites.
    """
    # Date validation
    if start_date and end_date and end_date < start_date:
        return JSONResponse(
            status_code=400, content={"detail": "Start date can't be after end date."}
        )
    if end_date and end_date > datetime.today().date():
        return JSONResponse(
            status_code=400, content={"detail": "End date can't be in the future."}
        )
    if start_date and start_date > datetime.today().date():
        return JSONResponse(
            status_code=400, content={"detail": "Start date can't be in the future."}
        )

    # Set default date ranges
    if start_date is None and end_date is not None:
        start_date = end_date - timedelta(days=365)
    if start_date is not None and end_date is None:
        end_date = min(start_date + timedelta(days=365), datetime.today().date())
    if start_date is None and end_date is None:
        end_date = datetime.today().date()
        start_date = end_date - timedelta(days=365)

    # Update redis key to include satellite filter for proper caching
    satellite_key = satellite if satellite else "all"
    redis_key = f"catalogue_{farm_id}_{satellite_key}_{start_date.strftime('%Y-%m-%d')}_{end_date.strftime('%Y-%m-%d')}"

    try:
        farm = json.loads(get_farm_geometry(db, user_id, farm_id)["geom"])
        bbox = BBox(bbox=farm["properties"]["bbox"], crs=CRS.WGS84)

        config_cache = {}

        def get_config(region_url):
            if region_url not in config_cache:
                config = SHConfig()
                config.sh_client_id = setting.SENTINEL_HUB_CLIENT_ID
                config.sh_client_secret = setting.SENTINEL_HUB_CLIENT_SECRET
                config.sh_base_url = region_url
                config_cache[region_url] = config
            return config_cache[region_url]

        catalog_searches = []

        # Handle regular satellites (S1, S2, S3, S5, etc.)
        if not satellite or satellite != "S6":
            satellites_query = db.query(Satellites).filter(
                Satellites.is_catalogue_enabled == True
            )

            # Apply satellite filter if provided
            if satellite and satellite != "S6":
                satellites_query = satellites_query.filter(Satellites.code == satellite)

            satellites = satellites_query.all()

            # Validate satellite exists if filter was applied
            if satellite and satellite != "S6" and not satellites:
                return JSONResponse(
                    status_code=404,
                    content={
                        "detail": f"Satellite '{satellite}' not found or not enabled for catalogue search."
                    },
                )

            for satellite_obj in satellites:
                config = get_config(satellite_obj.region_url)
                catalog = SentinelHubCatalog(config=config)
                search = catalog.search(
                    collection=satellite_obj.name,
                    bbox=bbox,
                    time=(start_date, end_date),
                )
                catalog_searches.append((search, satellite_obj))

        # Handle Planet collections (S6) - only if satellite is None or "S6"
        if not satellite or satellite == "S6":
            planets = (
                db.query(PlanetCollections)
                .filter(PlanetCollections.farm_id == farm_id)
                .all()
            )

            # If user specifically requested S6 but no Planet collections exist
            if satellite == "S6" and not planets:
                return JSONResponse(
                    status_code=404,
                    content={
                        "detail": "No Planet collections (S6) found for this farm."
                    },
                )

            for planet in planets:
                config = get_config("https://services.sentinel-hub.com")
                catalog = SentinelHubCatalog(config=config)
                search = catalog.search(
                    collection="byoc-" + planet.collection_id,
                    bbox=bbox,
                    time=(start_date, end_date),
                )
                catalog_searches.append(
                    (search, None)
                )  # None indicates Planet collection

        response_items = []
        for search, satellite_info in catalog_searches:
            products = list(search)
            for product in products:
                if "byoc-" in product["collection"]:
                    # This is a Planet collection (S6)
                    response_items.append(
                        {
                            "satellite": "S6",
                            "datetime": product["properties"]["datetime"],
                            "collection": product[
                                "collection"
                            ],  # Add collection info for Planet
                        }
                    )
                else:
                    # This is a regular satellite
                    response_items.append(
                        {
                            "satellite": satellite_info.code,
                            "datetime": product["properties"]["datetime"],
                            "collection": product["collection"],  # Add collection info
                        }
                    )

        # Sort by datetime for better user experience
        response_items.sort(key=lambda x: x["datetime"], reverse=True)

        try:
            tomorrow = datetime.now(tz=timezone.utc).replace(
                hour=23, minute=59, second=59
            )
            now = datetime.now(tz=timezone.utc)
            cache.set(redis_key, json.dumps(response_items), (tomorrow - now).seconds)
        except Exception as e:
            print(f"Cache error: {str(e)}")

        # Build response message
        if satellite:
            message = f"Available {satellite} satellite dates retrieved successfully."
        else:
            message = "Available satellite dates retrieved successfully."

        return JSONResponse(
            status_code=200,
            content={
                "status_code": 200,
                "message": message,
                "data": response_items,
                "metadata": {
                    "satellite_filter": satellite,
                    "date_range": {
                        "start_date": start_date.isoformat(),
                        "end_date": end_date.isoformat(),
                    },
                    "total_dates": len(response_items),
                },
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    except HTTPException as http_exc:
        return JSONResponse(
            status_code=http_exc.status_code, content={"detail": http_exc.detail}
        )
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"detail": "Something went wrong. Check with admin"},
        )


# Optional: Helper function to get available satellites for a farm
@route.get("/available-satellites")
async def list_available_satellites(
    request: Request,
    user_id: str,
    farm_id: str,
    db: Session = Depends(get_db),
):
    """
    Get list of available satellites for a specific farm.
    Useful for UI dropdowns and validation.
    """
    try:
        # Validate user and farm access
        user, error_response = get_user_by_id(db, user_id)
        if error_response:
            return error_response

        farm, error_response = get_farm_by_id(db, farm_id)
        if error_response:
            return error_response

        if str(farm.user_id) != str(user.id):
            return JSONResponse(
                status_code=403,
                content={"detail": "This farm does not belong to the specified user."},
            )

        # Get enabled satellites
        satellites = (
            db.query(Satellites.code, Satellites.name)
            .filter(Satellites.is_catalogue_enabled == True)
            .all()
        )

        # Check for Planet collections
        planet_collections = (
            db.query(PlanetCollections)
            .filter(PlanetCollections.farm_id == farm_id)
            .count()
        )

        available_satellites = [
            {"code": sat.code, "name": sat.name} for sat in satellites
        ]

        # Add Planet if collections exist
        if planet_collections > 0:
            available_satellites.append({"code": "S6", "name": "Planet"})

        return JSONResponse(
            status_code=200,
            content={
                "status_code": 200,
                "message": "Available satellites retrieved successfully.",
                "data": available_satellites,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    except Exception as e:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"detail": "Something went wrong. Check with admin"},
        )


@route.get("/generate-image")
async def generate_satellite_image(
    request: Request,
    user_id: str,
    farm_id: str,
    index: str,
    satellite: str,
    satellite_date: date,
    units: str = "metric",
    background_tasks: BackgroundTasks = None,
    response: Response = None,
    db: Session = Depends(get_db),
    cache=Depends(get_redis),
):
    """
    Generate satellite image for a specific farm.
    """
    try:
        # Validate user and farm
        user, error_response = get_user_by_id(db, user_id)
        if error_response:
            return error_response

        farm, error_response = get_farm_by_id(db, farm_id)
        if error_response:
            return error_response

        if str(farm.user_id) != str(user.id):
            return JSONResponse(
                status_code=403,
                content={"detail": "This farm does not belong to the specified user."},
            )

        # Validate units
        if units not in ["metric", "imperial"]:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid units. Use 'metric' or 'imperial'."},
            )

        # Check if index and satellite are valid
        [selected_index, selected_satellite] = get_validated_index(
            db, index, satellite, "imagery"
        )

        farm_data = get_farm(db, user_id, farm_id)
        farm_geom = json.loads(farm_data["geom"])
        farm_area = farm_geom["properties"]["area"]

        # Convert area based on units
        if units == "metric":
            farm_area = farm_area * 0.405
        # Default to acres for imperial

        # Create a unique redis key
        redis_key = "field_imagery"
        for value in [farm_id, index, satellite, satellite_date.strftime("%Y-%m-%d")]:
            redis_key = redis_key + "_" + str(value.replace(" ", "_"))

        # Check cache first
        # try:
        #     cache_response = cache.get(redis_key)
        #     if cache_response:
        #         response.headers["x-cached"] = "True"
        #         cached_data = json.loads(cache_response)
        #         return JSONResponse(
        #             status_code=200,
        #             content={
        #                 "status_code": 200,
        #                 "message": "Satellite image retrieved successfully (cached).",
        #                 "data": cached_data,
        #                 "farm_info": {
        #                     "farm_id": str(farm.id),
        #                     "farm_name": farm.farm_name,
        #                     "farmer_name": getattr(farm.farmer, "name", None),
        #                     "latitude": farm.lat,
        #                     "longitude": farm.lon,
        #                     "district": getattr(farm, "district", None),
        #                     "state": getattr(farm, "state", None),
        #                 },
        #                 "timestamp": datetime.utcnow().isoformat() + "Z",
        #             },
        #         )
        # except Exception as e:
        #     print(f"Cache error: {str(e)}")

        # Setup Sentinel Hub config
        config = SHConfig()
        config.sh_client_id = setting.SENTINEL_HUB_CLIENT_ID
        config.sh_client_secret = setting.SENTINEL_HUB_CLIENT_SECRET
        config.sh_base_url = selected_satellite.region_url

        # Determine collection
        if satellite == "S6":
            temp_collection = (
                db.query(PlanetCollections.collection_id)
                .filter(PlanetCollections.farm_id == farm_id)
                .first()
            )

            if not temp_collection:
                return JSONResponse(
                    status_code=404,
                    content={"detail": "Planet collection not found for this farm"},
                )

            collection = DataCollection.define_byoc(temp_collection[0])
        else:
            try:
                collection = next(
                    x
                    for x in list(DataCollection)
                    if x.api_id == selected_index.satellite
                )
            except StopIteration:
                return JSONResponse(
                    status_code=404,
                    content={
                        "detail": f"No matching data collection found for {selected_index.satellite}"
                    },
                )

        bbox = BBox(bbox=farm_geom["properties"]["bbox"], crs=CRS.WGS84)

        # Create temporary folder for processing
        data_folder = f"static/field-imagery-stats-{uuid4()}"
        Path(data_folder).mkdir(parents=True, exist_ok=True)
        if background_tasks:
            background_tasks.add_task(remove_folder, data_folder)

        # Make the Sentinel Hub request
        request_sh = SentinelHubRequest(
            data_folder=data_folder,
            evalscript=selected_index.evalscript,
            input_data=[
                SentinelHubRequest.input_data(
                    data_collection=collection,
                    time_interval=(satellite_date, satellite_date),
                    upsampling=ResamplingType.BICUBIC,
                    downsampling=ResamplingType.BICUBIC,
                ),
            ],
            responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
            bbox=bbox,
            config=config,
        )

        # Get the data with timeout to prevent hanging
        try:
            request_sh.get_data(save_data=True)
        except Exception as timeout_error:
            return JSONResponse(
                status_code=504,
                content={
                    "detail": "Request to Sentinel Hub timed out. Please try again later."
                },
            )

        # Process the response
        full_path = f"{data_folder}/{next(os.walk(data_folder))[1][0]}"

        # FIXED: Use the correct geometry reference
        print(farm_geom)
        # geometry_for_mask = farm_geom["geometry"]  # This is the correct way to access geometry
        geometry_for_mask = farm_geom

        # Process specific indices with special handling
        if selected_index.name in [
            "Barren Soil",
            "Bare Soil Marker",
            "True color",
            "True Color 2",
        ]:
            with rasterio.open(f"{full_path}/response.tiff") as src:
                out_image, out_transform = mask.mask(
                    src,
                    [geometry_for_mask],
                    crop=True,
                    nodata=0,  # FIXED: Use geometry_for_mask
                )
                out_meta = src.meta.copy()
                out_meta.update(
                    {
                        "driver": "PNG",
                        "height": out_image.shape[1],
                        "width": out_image.shape[2],
                        "transform": out_transform,
                        "nodata": 0,
                    }
                )

                # Process the legend data
                legends = (
                    selected_index.legend.get("values", [])
                    if selected_index.legend
                    else []
                )
                # Process the split data
                splits = (
                    selected_index.legend.get("split", [])
                    if selected_index.legend
                    else []
                )
                dynamic_legend = (
                    selected_index.legend.get("dynamic", False)
                    if selected_index.legend
                    else False
                )

                counted_pixels = np.count_nonzero(out_image[0] > 0)
                pixel_area = farm_area / counted_pixels if counted_pixels else 0

                if dynamic_legend:
                    # Dynamic legend processing based on min/max ranges
                    for legend in legends:
                        min_legend, max_legend, hex_color = (
                            legend.get("min"),
                            legend.get("max"),
                            legend["hex"],
                        )
                        pixel_positions = (
                            (out_image[0] >= min_legend) if min_legend else True
                        ) & ((out_image[0] < max_legend) if max_legend else True)
                        legend_pixels = np.count_nonzero(pixel_positions)
                        legend["unit"] = "hectare" if units == "metric" else "acre"
                        legend["area"] = round(legend_pixels * pixel_area, 2)
                        legend["area%"] = 0

                    # Process split data similarly
                    for split in splits:
                        min_split, max_split, hex_color = (
                            split.get("min"),
                            split.get("max"),
                            split["hex"],
                        )
                        pixel_positions = (
                            (out_image[0] >= min_split) if min_split else True
                        ) & ((out_image[0] < max_split) if max_split else True)
                        split_pixels = np.count_nonzero(pixel_positions)
                        split["unit"] = "hectare" if units == "metric" else "acre"
                        split["area"] = round(split_pixels * pixel_area, 2)
                        split["area%"] = (
                            f"{round((split_pixels / counted_pixels) * 100, 2)}%"
                        )
                else:
                    # Static legend processing based on color
                    for legend in legends:
                        hex_color = legend["hex"]
                        color_value = int(hex_color.lstrip("#"), 16)

                        # Find pixels that match this color
                        color_pixels = np.count_nonzero(out_image[0] == color_value)
                        legend["unit"] = "hectare" if units == "metric" else "acre"
                        legend["area"] = round(color_pixels * pixel_area, 2)
                        legend["area%"] = (
                            f"{round((color_pixels / counted_pixels) * 100, 2)}%"
                        )

                    # Process split data for static legends
                    for split in splits:
                        hex_color = split["hex"]
                        color_value = int(hex_color.lstrip("#"), 16)

                        # Find pixels that match this color
                        color_pixels = np.count_nonzero(out_image[0] == color_value)
                        split["unit"] = "hectare" if units == "metric" else "acre"
                        split["area"] = round(color_pixels * pixel_area, 2)
                        split["area%"] = (
                            f"{round((color_pixels / counted_pixels) * 100, 2)}%"
                        )

                # Save image and encode as base64
                masked_img_path = f"{full_path}/masked.png"
                with rasterio.open(masked_img_path, "w", **out_meta) as dst:
                    dst.write(out_image)

                with open(masked_img_path, "rb") as masked_image:
                    encoded_image_string = base64.b64encode(
                        masked_image.read()
                    ).decode()

                data = {
                    "image": encoded_image_string,
                    "dynamic": dynamic_legend,
                    "legends": legends,
                    "split": splits,
                    "unit": units,
                }

                # Cache the result
                cache.set(redis_key, json.dumps(data), 60 * 60 * 24)

        else:
            with rasterio.open(f"{full_path}/response.tiff") as src:
                if (
                    selected_index.legend is None
                    or selected_index.legend.get("dynamic", False) == False
                ):
                    out_image, out_transform = mask.mask(
                        src,
                        [geometry_for_mask],
                        crop=True,
                        nodata=0,  # FIXED: Use geometry_for_mask
                    )
                    out_meta = src.meta.copy()
                    out_meta.update(
                        {
                            "driver": "PNG",
                            "height": out_image.shape[1],
                            "width": out_image.shape[2],
                            "transform": out_transform,
                            "nodata": 0,
                        }
                    )
                else:
                    out_image = mask.mask(
                        src,
                        [geometry_for_mask],  # FIXED: Use geometry_for_mask
                        crop=True,
                        filled=False,
                    )
                    band_value = (out_image[0][0]) / 255

                if (
                    selected_index.legend is None
                    or selected_index.legend.get("dynamic", False) == False
                ):
                    masked_img_path = f"{full_path}/masked.png"
                    with rasterio.open(masked_img_path, "w", **out_meta) as dst:
                        dst.write(out_image)

                    with open(masked_img_path, "rb") as masked_image:
                        encoded_image_string = base64.b64encode(
                            masked_image.read()
                        ).decode()

                    data = {
                        "image": encoded_image_string,
                        "dynamic": False,
                        "legends": (
                            selected_index.legend.get("values", [])
                            if selected_index.legend
                            else []
                        ),
                        "split": (
                            selected_index.legend.get("split", [])
                            if selected_index.legend
                            else []
                        ),
                        "unit": units,
                    }

                    # Cache the result
                    cache.set(redis_key, json.dumps(data), 60 * 60 * 24)

                    return JSONResponse(
                        status_code=200,
                        content={
                            "status_code": 200,
                            "message": "Satellite image generated successfully.",
                            "data": data,
                            "farm_info": {
                                "farm_id": str(farm.id),
                                "farm_name": farm.farm_name,
                                "farmer_name": getattr(farm.farmer, "name", None),
                                "latitude": farm.lat,
                                "longitude": farm.lon,
                                "district": getattr(farm, "district", None),
                                "state": getattr(farm, "state", None),
                            },
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                        },
                    )

                image_colors = np.zeros(band_value.shape + (4,))

                legend_data = selected_index.legend
                counted_pixels = band_value.count()
                pixel_area = farm_area / counted_pixels
                final_legends = []
                final_splits = []

                # Process values (legends)
                for legend in legend_data["values"]:
                    min_legend, max_legend, hex = (
                        legend.get("min"),
                        legend.get("max"),
                        legend["hex"],
                    )
                    legend["unit"] = "hectare" if units == "metric" else "acre"
                    if min_legend is not None and max_legend is not None:
                        pixel_positions = (band_value >= min_legend) & (
                            band_value < max_legend
                        )
                        image_colors[pixel_positions] = ImageColor.getcolor(hex, "RGBA")
                        legend_pixels = (pixel_positions).sum()
                        legend["area"] = round(legend_pixels * pixel_area, 2)
                        legend["area%"] = (
                            f"{round((legend_pixels / counted_pixels) * 100, 2)}%"
                        )
                    elif min_legend is not None:
                        pixel_positions = band_value >= min_legend
                        image_colors[pixel_positions] = ImageColor.getcolor(hex, "RGBA")
                        legend_pixels = (pixel_positions).sum()
                        legend["area"] = round(legend_pixels * pixel_area, 2)
                        legend["area%"] = (
                            f"{round((legend_pixels / counted_pixels) * 100, 2)}%"
                        )
                    elif max_legend is not None:
                        pixel_positions = band_value < max_legend
                        image_colors[pixel_positions] = ImageColor.getcolor(hex, "RGBA")
                        legend_pixels = (pixel_positions).sum()
                        legend["area"] = round(legend_pixels * pixel_area, 2)
                        legend["area%"] = (
                            f"{round((legend_pixels / counted_pixels) * 100, 2)}%"
                        )
                    final_legends.append(legend)

                # Process split data
                if legend_data.get("split"):
                    for split in legend_data["split"]:
                        min_split, max_split, hex = (
                            split.get("min"),
                            split.get("max"),
                            split["hex"],
                        )
                        split["unit"] = "hectare" if units == "metric" else "acre"
                        if min_split is not None and max_split is not None:
                            pixel_positions = (band_value >= min_split) & (
                                band_value < max_split
                            )
                            split_pixels = (pixel_positions).sum()
                            split["area"] = round(split_pixels * pixel_area, 2)
                            split["area%"] = (
                                f"{round((split_pixels / counted_pixels) * 100, 2)}%"
                            )
                        elif min_split is not None:
                            pixel_positions = band_value >= min_split
                            split_pixels = (pixel_positions).sum()
                            split["area"] = round(split_pixels * pixel_area, 2)
                            split["area%"] = (
                                f"{round((split_pixels / counted_pixels) * 100, 2)}%"
                            )
                        elif max_split is not None:
                            pixel_positions = band_value < max_split
                            split_pixels = (pixel_positions).sum()
                            split["area"] = round(split_pixels * pixel_area, 2)
                            split["area%"] = (
                                f"{round((split_pixels / counted_pixels) * 100, 2)}%"
                            )
                        final_splits.append(split)

                image_colors[np.ma.getmaskarray(band_value)] = (0, 0, 0, 0)
                masked_img = Image.fromarray(image_colors.astype(np.uint8), mode="RGBA")
                buffered = io.BytesIO()
                masked_img.save(buffered, format="PNG")
                encoded_image_string = base64.b64encode(buffered.getvalue()).decode()

                data = {
                    "image": encoded_image_string,
                    "dynamic": True,
                    "legends": final_legends,
                    "split": final_splits,
                    "unit": units,
                }

                # Cache the result with 24-hour expiration
                cache.set(redis_key, json.dumps(data), 60 * 60 * 24)

        return JSONResponse(
            status_code=200,
            content={
                "status_code": 200,
                "message": "Satellite image generated successfully.",
                "data": data,
                "farm_info": {
                    "farm_id": str(farm.id),
                    "farm_name": farm.farm_name,
                    "farmer_name": getattr(farm.farmer, "name", None),
                    "latitude": farm.lat,
                    "longitude": farm.lon,
                    "district": getattr(farm, "district", None),
                    "state": getattr(farm, "state", None),
                },
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    except HTTPException as http_exc:
        return JSONResponse(
            status_code=http_exc.status_code, content={"detail": http_exc.detail}
        )
    except Exception as e:
        # Log the detailed error but return a generic message
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"detail": "Something went wrong. Check with admin"},
        )


@route.get("/ndvi-analysis")
async def get_ndvi_analysis(
    user_id: str,
    db: Session = Depends(get_db),
):
    """
    Analyze NDVI values for farms under a KVK/Super User.
    Returns highest and lowest NDVI performing farms.
    """
    try:
        # Step 1: Validate user and check role
        user = (
            db.query(User)
            .options(joinedload(User.role))
            .filter(User.id == user_id)
            .first()
        )
        if not user:
            return JSONResponse(
                status_code=404,
                content={
                    "message": "User not found",
                    "status_code": 404,
                    "data": None,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

        # Check if user has kvk or super_admin role
        if not user.role or user.role.name not in ["kvk", "super_admin"]:
            return JSONResponse(
                status_code=403,
                content={
                    "message": "Access denied. User must have kvk or super_admin role",
                    "status_code": 403,
                    "data": None,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

        # Step 2: Query farms for this KVK with NDVI values
        if user.role.name == "super_admin":
            # Super admin can see farms from KVKs under their hierarchy
            # First get KVKs that have this super_admin as parent
            kvk_users = (
                db.query(User)
                .filter(
                    User.parent_id == user_id,
                    User.role.has(name="kvk")
                )
                .all()
            )
            
            if not kvk_users:
                return JSONResponse(
                    status_code=404,
                    content={
                        "message": "No KVKs found under this super admin",
                        "status_code": 404,
                        "data": None,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    },
                )
            
            # Get all kvk_ids under this super_admin
            kvk_ids = [kvk.id for kvk in kvk_users]
            
            # Query farms that belong to these KVKs
            farms_query = (
                db.query(Farm)
                .options(joinedload(Farm.farmer))
                .filter(
                    Farm.kvk_id.in_(kvk_ids),  # Farms belonging to KVKs under this super_admin
                    Farm.ndvi.isnot(None),  # Only farms with NDVI values
                    Farm.ndvi != 0,  # Exclude farms with zero NDVI
                )
                .all()
            )
        else:
            # KVK user can only see farms assigned to them
            farms_query = (
                db.query(Farm)
                .options(joinedload(Farm.farmer))
                .filter(
                    Farm.kvk_id == user_id,  # Use kvk_id field from Farm table
                    Farm.ndvi.isnot(None),  # Only farms with NDVI values
                    Farm.ndvi != 0,  # Exclude farms with zero NDVI
                )
                .all()
            )

        if not farms_query:
            role_specific_message = (
                "No farms with NDVI data found under KVKs managed by this super admin"
                if user.role.name == "super_admin"
                else f"No farms with NDVI data found assigned to KVK: {user.name}"
            )
            return JSONResponse(
                status_code=404,
                content={
                    "message": role_specific_message,
                    "status_code": 404,
                    "data": None,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

        # Step 4: Extract NDVI values and calculate mean
        ndvi_values = [farm.ndvi for farm in farms_query if farm.ndvi is not None]

        if not ndvi_values:
            return JSONResponse(
                status_code=400,
                content={
                    "message": "No valid NDVI values found",
                    "status_code": 400,
                    "data": None,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

        mean_ndvi = statistics.mean(ndvi_values)

        # Create a mapping of kvk_id to kvk_name for quick lookup
        kvk_name_map = {}
        if user.role.name == "super_admin":
            for kvk in kvk_users:
                kvk_name_map[str(kvk.id)] = kvk.name
        else:
            kvk_name_map[str(user.id)] = user.name

        # Step 5: Separate farms into highest and lowest NDVI
        highest_ndvi_farms = []
        lowest_ndvi_farms = []

        for farm in farms_query:
            farm_data = {
                "farm_id": str(farm.id),
                "farm_name": farm.farm_name,
                "farmer_name": farm.farmer.name if farm.farmer else None,
                "farmer_id": str(farm.user_id),
                "kvk_id": str(farm.kvk_id),
                "kvk_name": kvk_name_map.get(str(farm.kvk_id), "Unknown KVK"),  # Added KVK name
                "ndvi_value": farm.ndvi,
                "latitude": farm.lat,
                "longitude": farm.lon,
                "area": farm.area,
                "crop": farm.crop,
                "ai_yield": farm.ai_yield,
                "revenue": farm.revenue,
                "sowing_date": (
                    farm.sowing_date.isoformat() if farm.sowing_date else None
                ),
                "soil_data": (
                    {
                        "carbon_organic_gperkg": farm.carbon_organic_gperkg,
                        "nitrogen_gperkg": farm.nitrogen_gperkg,
                        "ph": farm.ph,
                        "phosphorus_ppm": farm.phosphorus_ppm,
                        "potassium_ppm": farm.potassium_ppm,
                    }
                    if any(
                        [
                            farm.carbon_organic_gperkg,
                            farm.nitrogen_gperkg,
                            farm.ph,
                            farm.phosphorus_ppm,
                            farm.potassium_ppm,
                        ]
                    )
                    else None
                ),
            }

            if farm.ndvi >= mean_ndvi:
                highest_ndvi_farms.append(farm_data)
            else:
                lowest_ndvi_farms.append(farm_data)

        # Sort farms by NDVI value
        highest_ndvi_farms.sort(key=lambda x: x["ndvi_value"], reverse=True)
        lowest_ndvi_farms.sort(key=lambda x: x["ndvi_value"])

        # Step 6: Create response data
        analysis_data = {
            "requesting_user_id": str(user_id),
            "user_role": user.role.name if user.role else None,
            "user_name": user.name,
            "managed_kvks": (
                [{"kvk_id": str(kvk.id), "kvk_name": kvk.name} for kvk in kvk_users]
                if user.role.name == "super_admin"
                else None
            ),
            "total_farms": len(farms_query),
            "mean_ndvi": round(mean_ndvi, 4),
            "ndvi_statistics": {
                "min_ndvi": round(min(ndvi_values), 4),
                "max_ndvi": round(max(ndvi_values), 4),
                "mean_ndvi": round(mean_ndvi, 4),
                "median_ndvi": round(statistics.median(ndvi_values), 4),
                "std_dev": round(
                    statistics.stdev(ndvi_values) if len(ndvi_values) > 1 else 0, 4
                ),
            },
            "highest_ndvi_farms": {
                "count": len(highest_ndvi_farms),
                "farms": highest_ndvi_farms,
            },
            "lowest_ndvi_farms": {
                "count": len(lowest_ndvi_farms),
                "farms": lowest_ndvi_farms,
            },
        }

        return JSONResponse(
            status_code=200,
            content={
                "message": "NDVI analysis completed successfully",
                "status_code": 200,
                "data": analysis_data,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    except ValueError as ve:
        return JSONResponse(
            status_code=400,
            content={
                "message": f"Invalid data: {str(ve)}",
                "status_code": 400,
                "data": None,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    except HTTPException as http_exc:
        return JSONResponse(
            status_code=http_exc.status_code,
            content={
                "message": str(http_exc.detail),
                "status_code": http_exc.status_code,
                "data": None,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    except Exception as e:
        # Log the detailed error but return a generic message
        print(f"Error in NDVI analysis: {str(e)}")
        import traceback

        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={
                "message": "Internal server error occurred during NDVI analysis",
                "status_code": 500,
                "data": None,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )