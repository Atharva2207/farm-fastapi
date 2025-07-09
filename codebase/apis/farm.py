from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID
from datetime import date, datetime
from database import get_db
from models import Farm, SoilParameter, User
from schemas import FarmPlotCreateSchema, FarmPlotUpdateSchema, FarmPlotFlexibleSchema
from geoalchemy2.shape import to_shape
from fastapi_pagination import Page, paginate

from helper_functions.utility import (
    build_response,
    safe_serialize,
)  # Assuming you have a response utility

route = APIRouter(prefix="/api", tags=["Farm"])


def serialize_farm(farm: Farm, include: Optional[List[str]] = None) -> dict:
    def safe_date(val):
        return val.isoformat() if isinstance(val, (date, datetime)) else val

    data = {
        "id": str(farm.id),
        "area": farm.area,
        "crop": farm.crop,
        "ai_yield": farm.ai_yield,
        "revenue": farm.revenue,
        "ndvi": farm.ndvi,
        "carbon_organic_gperkg": farm.carbon_organic_gperkg,
        "nitrogen_gperkg": farm.nitrogen_gperkg,
        "ph": farm.ph,
        "phosphorus_ppm": farm.phosphorus_ppm,
        "potassium_ppm": farm.potassium_ppm,
        "farm_name": farm.farm_name,
        # "lat": farm.lat,
        # "lon": farm.lon,
        # "bbox": farm.bbox,
        "sowing_date": safe_date(farm.sowing_date),
        "created_at": (
            farm.farmer.date_joined.isoformat() + "Z"
            if farm.farmer and farm.farmer.date_joined
            else None
        ),
    }

    if include:
        if "geometry" in include and farm.geometry:
            data["geometry"] = to_shape(farm.geometry).wkt
        if "farmer" in include and farm.farmer:
            data["farmer"] = {
                "id": str(farm.farmer.id),
                "username": farm.farmer.username,
                "email": farm.farmer.email,
                "name": farm.farmer.name,
            }
        if "kvk" in include and farm.kvk_user:
            data["kvk"] = {
                "id": str(farm.kvk_user.id),
                "username": farm.kvk_user.username,
                "email": farm.kvk_user.email,
                "name": farm.kvk_user.name,
            }

    return data


@route.get("/farmplots/", response_model=Page[FarmPlotFlexibleSchema])
def list_farmplots(
    db: Session = Depends(get_db),
    farmer_id: Optional[UUID] = Query(None),
    kvk_id: Optional[UUID] = Query(None),
    crop: Optional[str] = Query(None),
    include: Optional[str] = Query(None),
):
    query = db.query(Farm)
    if farmer_id:
        query = query.filter(Farm.user_id == farmer_id)
    if kvk_id:
        query = query.filter(Farm.kvk_id == kvk_id)
    if crop:
        query = query.filter(Farm.crop.ilike(f"%{crop}%"))

    include_fields = include.split(",") if include else []
    farms = query.all()
    serialized = [serialize_farm(f, include_fields) for f in farms]
    paginated = paginate(serialized)
    return build_response(paginated.dict())


@route.get("/farmplots/{id}/", response_model=FarmPlotFlexibleSchema)
def read_farmplot(id: UUID, db: Session = Depends(get_db)):
    farm = db.query(Farm).filter(Farm.id == id).first()
    if not farm:
        raise HTTPException(status_code=404, detail="Farm not found")
    return build_response(serialize_farm(farm, ["geometry", "farmer", "kvk"]))


@route.post("/farmplots/", response_model=FarmPlotFlexibleSchema)
def create_farmplot(payload: FarmPlotCreateSchema, db: Session = Depends(get_db)):
    data = payload.dict()

    # If kvk_id is not provided, try to infer from farmer's parent_id
    if not data.get("kvk_id"):
        farmer = db.query(User).filter(User.id == data["user_id"]).first()
        if not farmer:
            raise HTTPException(status_code=404, detail="Farmer not found")
        if not farmer.parent_id:
            raise HTTPException(
                status_code=400,
                detail="KVK not provided and farmer is not linked to any KVK",
            )
        data["kvk_id"] = farmer.parent_id  # use parent_id as kvk_id for saving

    new_farm = Farm(**data)
    db.add(new_farm)
    db.commit()
    db.refresh(new_farm)

    return build_response(serialize_farm(new_farm, ["geometry", "farmer", "kvk"]))


@route.put("/farmplots/{id}/", response_model=FarmPlotFlexibleSchema)
def update_farmplot(
    id: UUID, payload: FarmPlotUpdateSchema, db: Session = Depends(get_db)
):
    farm = db.query(Farm).filter(Farm.id == id).first()
    if not farm:
        raise HTTPException(status_code=404, detail="Farm not found")
    for key, value in payload.dict(exclude_unset=True).items():
        setattr(farm, key, value)
    db.commit()
    db.refresh(farm)
    return build_response(serialize_farm(farm, ["geometry", "farmer", "kvk"]))


@route.delete("/farmplots/{id}/")
def delete_farmplot(id: UUID, db: Session = Depends(get_db)):
    farm = db.query(Farm).filter(Farm.id == id).first()
    if not farm:
        raise HTTPException(status_code=404, detail="Farm not found")
    db.delete(farm)
    db.commit()
    return build_response({"message": "Farm deleted successfully"})


@route.get("/get-soil-parameters/")
def get_soil_parameters(
    user_id: str, farm_id: Optional[str] = Query(None), db: Session = Depends(get_db)
):
    try:
        # Step 1: Validate user
        user = db.query(User).filter(User.id == user_id).first()
        farm_ids = []

        if farm_id:
            # Validate the farm belongs to this user's hierarchy
            farm = db.query(Farm).filter(Farm.id == farm_id).first()
            if not farm:
                return JSONResponse(
                    status_code=404,
                    content={
                        "message": "Farm not found.",
                        "status_code": 404,
                        "data": None,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    },
                )

            # Admin check: allow only if farm.user_id is under their hierarchy
            if user.role_id == 1:
                valid_ids = []
                kvks = (
                    db.query(User)
                    .filter(User.parent_id == user_id, User.role_id == 2)
                    .all()
                )
                for kvk in kvks:
                    child_users = db.query(User).filter(User.parent_id == kvk.id).all()
                    valid_ids.extend([u.id for u in child_users])
                if farm.user_id not in valid_ids:
                    return JSONResponse(
                        status_code=403,
                        content={
                            "message": "You are not authorized to access this farm.",
                            "status_code": 403,
                            "data": None,
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                        },
                    )
            elif user.role_id == 2 and farm.user_id not in [
                u.id for u in db.query(User).filter(User.parent_id == user_id).all()
            ]:
                return JSONResponse(
                    status_code=403,
                    content={
                        "message": "You are not authorized to access this farm.",
                        "status_code": 403,
                        "data": None,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    },
                )

            farm_ids = [farm_id]

        else:
            # No farm_id provided, fetch all farms under hierarchy
            child_user_ids = []

            if user.role_id == 1:
                kvk_users = (
                    db.query(User)
                    .filter(User.parent_id == user_id, User.role_id == 2)
                    .all()
                )
                for kvk_user in kvk_users:
                    farmers = db.query(User).filter(User.parent_id == kvk_user.id).all()
                    child_user_ids.extend([u.id for u in farmers])
            elif user.role_id == 2:
                child_user_ids = [
                    u.id for u in db.query(User).filter(User.parent_id == user_id).all()
                ]

            if not child_user_ids:
                return JSONResponse(
                    status_code=404,
                    content={
                        "message": "No child users found.",
                        "status_code": 404,
                        "data": None,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    },
                )

            farms = db.query(Farm).filter(Farm.user_id.in_(child_user_ids)).all()
            farm_ids = [f.id for f in farms]

        if not farm_ids:
            return JSONResponse(
                status_code=404,
                content={
                    "message": "No farms found for this user.",
                    "status_code": 404,
                    "data": None,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

        # Step 2: Fetch soil data
        soil_data = (
            db.query(SoilParameter).filter(SoilParameter.farm_id.in_(farm_ids)).all()
        )

        if not soil_data:
            return JSONResponse(
                status_code=404,
                content={
                    "message": "No soil data found for the specified farms.",
                    "status_code": 404,
                    "data": None,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

        # Step 3: Format response
        data = [
            {
                "farm_name": s.farm_name,
                "aluminium_extractable_ppm": s.aluminium_extractable_ppm,
                "bulk_density_gpercubic": s.bulk_density_gpercubic,
                "calcium_extractable_ppm": s.calcium_extractable_ppm,
                "clay_content_per": s.clay_content_per,
                "iron_extractable_ppm": s.iron_extractable_ppm,
                "magnesium_extractable_ppm": s.magnesium_extractable_ppm,
                "sulphur_extractable_ppm": s.sulphur_extractable_ppm,
                "farm_id": str(s.farm_id),
            }
            for s in soil_data
        ]

        return JSONResponse(
            status_code=200,
            content={
                "message": "Soil parameters fetched successfully.",
                "status_code": 200,
                "data": data,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "message": f"Internal server error: {str(e)}",
                "status_code": 500,
                "data": None,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
