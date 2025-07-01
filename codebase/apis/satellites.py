import io
import os
import shutil
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
from sqlalchemy.orm import Session
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


route = APIRouter(prefix="/Satellite", tags=["Satellite"])


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
            func.ST_Area(func.ST_Transform(Farm.geometry, 3857)).label("area_sqm")  # Area in square meters
        )
        .filter(Farm.user_id == user.id, Farm.id == farm_id)
        .first()
    )

    if not farm_geom_query:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Farm geometry not found"
        )

    # Parse the geometry JSON
    geom_data = json.loads(farm_geom_query.geom)
    
    # Use stored bbox (it's already a list from JSONB)
    bbox_list = farm_geom_query.bbox if farm_geom_query.bbox else []
    
    # Add properties with bbox and area
    geom_data["properties"] = {
        "bbox": bbox_list,  # This is already [min_lon, min_lat, max_lon, max_lat]
        "area": float(farm_geom_query.area_sqm) / 4047  # Convert to acres (1 acre = 4047 sqm)
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
            func.ST_Area(func.ST_Transform(Farm.geometry, 3857)).label("area_sqm")
        )
        .filter(Farm.user_id == user.id, Farm.id == farm_id)
        .first()
    )

    if not farm_data_query:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Farm data not found"
        )

    # Parse the geometry JSON and add properties
    geom_data = json.loads(farm_data_query.geom)
    geom_data["properties"] = {
        "bbox": farm_data_query.bbox if farm_data_query.bbox else [],
        "area": float(farm_data_query.area_sqm) / 4047  # Convert to acres
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
    start_date: date = None,
    end_date: date = None,
    response: Response = None,
    db: Session = Depends(get_db),
    cache=Depends(get_redis),
):
    """
    Get available satellite dates for a specific farm.
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

    redis_key = f"catalogue_{farm_id}_{start_date.strftime('%Y-%m-%d')}_{end_date.strftime('%Y-%m-%d')}"

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

        satellites = (
            db.query(Satellites).filter(Satellites.is_catalogue_enabled == True).all()
        )
        catalog_searches = []

        for satellite in satellites:
            config = get_config(satellite.region_url)
            catalog = SentinelHubCatalog(config=config)
            search = catalog.search(
                collection=satellite.name, bbox=bbox, time=(start_date, end_date)
            )
            catalog_searches.append((search, satellite))

        planets = (
            db.query(PlanetCollections)
            .filter(PlanetCollections.farm_id == farm_id)
            .all()
        )
        for planet in planets:
            config = get_config("https://services.sentinel-hub.com")
            catalog = SentinelHubCatalog(config=config)
            search = catalog.search(
                collection="byoc-" + planet.collection_id,
                bbox=bbox,
                time=(start_date, end_date),
            )
            catalog_searches.append((search, None))

        response_items = []
        for search, satellite_info in catalog_searches:
            products = list(search)
            for product in products:
                if "byoc-" in product["collection"]:
                    response_items.append(
                        {
                            "satellite": "S6",
                            "datetime": product["properties"]["datetime"],
                        }
                    )
                else:
                    response_items.append(
                        {
                            "satellite": satellite_info.code,
                            "datetime": product["properties"]["datetime"],
                        }
                    )

        try:
            tomorrow = datetime.now(tz=timezone.utc).replace(
                hour=23, minute=59, second=59
            )
            now = datetime.now(tz=timezone.utc)
            cache.set(redis_key, json.dumps(response_items), (tomorrow - now).seconds)
        except Exception as e:
            print(f"Cache error: {str(e)}")

        return JSONResponse(
            status_code=200,
            content={
                "status_code": 200,
                "message": "Available satellite dates retrieved successfully.",
                "data": response_items,
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
        try:
            cache_response = cache.get(redis_key)
            if cache_response:
                response.headers["x-cached"] = "True"
                cached_data = json.loads(cache_response)
                return JSONResponse(
                    status_code=200,
                    content={
                        "status_code": 200,
                        "message": "Satellite image retrieved successfully (cached).",
                        "data": cached_data,
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
        except Exception as e:
            print(f"Cache error: {str(e)}")

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
                    src, [geometry_for_mask], crop=True, nodata=0  # FIXED: Use geometry_for_mask
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
                        src, [geometry_for_mask], crop=True, nodata=0  # FIXED: Use geometry_for_mask
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

                image_colors[np.ma.getmaskarray(band_value)] = (0, 0, 0, 0)
                masked_img = Image.fromarray(image_colors.astype(np.uint8), mode="RGBA")
                buffered = io.BytesIO()
                masked_img.save(buffered, format="PNG")
                encoded_image_string = base64.b64encode(buffered.getvalue()).decode()

                data = {
                    "image": encoded_image_string,
                    "dynamic": True,
                    "legends": final_legends,
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