from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID

from database import get_db
from models import Farm, User
from schemas import FarmPlotCreateSchema, FarmPlotUpdateSchema, FarmPlotFlexibleSchema
from geoalchemy2.shape import to_shape
from fastapi_pagination import Page, paginate

from helper_functions.utility  import build_response, safe_serialize  # Assuming you have a response utility

route = APIRouter(prefix="/api", tags=["Farm"])

def serialize_farm(farm: Farm, include: Optional[List[str]] = None) -> dict:
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
        "created_at": (
                farm.farmer.date_joined.isoformat() + "Z"
                if farm.farmer and farm.farmer.date_joined else None
            )
    }

    if include:
        if "geometry" in include and farm.geometry:
            data["geometry"] = to_shape(farm.geometry).wkt
        if "farmer" in include and farm.farmer:
            data["farmer"] = {
                "id": str(farm.farmer.id),
                "username": farm.farmer.username,
                "email": farm.farmer.email,
                "name": farm.farmer.name
            }
        if "kvk" in include and farm.kvk_user:
            data["kvk"] = {
                "id": str(farm.kvk_user.id),
                "username": farm.kvk_user.username,
                "email": farm.kvk_user.email,
                "name": farm.kvk_user.name
            }

    return data

@route.get("/farmplots/", response_model=Page[FarmPlotFlexibleSchema])
def list_farmplots(
    db: Session = Depends(get_db),
    farmer_id: Optional[UUID] = Query(None),
    kvk_id: Optional[UUID] = Query(None),
    crop: Optional[str] = Query(None),
    include: Optional[str] = Query(None)
):
    query = db.query(Farm)
    if farmer_id:
        query = query.filter(Farm.user_id == farmer_id)
    if kvk_id:
        query = query.filter(Farm.kvk_id == kvk_id)
    if crop:
        query = query.filter(Farm.crop.ilike(f"%{crop}%"))

    include_fields = include.split(',') if include else []
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
                detail="KVK not provided and farmer is not linked to any KVK"
            )
        data["kvk_id"] = farmer.parent_id  # use parent_id as kvk_id for saving

    new_farm = Farm(**data)
    db.add(new_farm)
    db.commit()
    db.refresh(new_farm)

    return build_response(serialize_farm(new_farm, ["geometry", "farmer", "kvk"]))

@route.put("/farmplots/{id}/", response_model=FarmPlotFlexibleSchema)
def update_farmplot(id: UUID, payload: FarmPlotUpdateSchema, db: Session = Depends(get_db)):
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
