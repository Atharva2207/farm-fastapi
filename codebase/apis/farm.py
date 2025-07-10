from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID
from datetime import date, datetime
from database import get_db
from models import Farm, SoilParameter, User, NdviStage
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


@route.get("/soil-classification-summary/")
def get_soil_classification_summary(parent_id: str, db: Session = Depends(get_db)):
    try:
       
        farms = (
            db.query(Farm)
            .join(User, Farm.user_id == User.id)
            .filter(User.parent_id == parent_id)
            .all()
        )

        if not farms:
            return JSONResponse(
                status_code=404,
                content={
                    "message": "No farms found",
                    "status_code": 404,
                    "data": None,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )

        farm_ids = [f.id for f in farms]
        total_farms = len(farms)

        # Step 2: Get soil parameters
        soil_params = (
            db.query(SoilParameter).filter(SoilParameter.farm_id.in_(farm_ids)).all()
        )

        # Step 3: Initialize results
        result = {
            "Nitrogen (g/kg)": {"Low": 0, "Medium": 0, "High": 0},
            "Phosphorus (ppm)": {"Low": 0, "Medium": 0, "High": 0},
            "Potassium (ppm)": {"Low": 0, "Medium": 0, "High": 0},
            "Organic Carbon (g/kg)": {"Low": 0, "Medium": 0, "High": 0},
            "pH": {
                "Strongly Acidic": 0,
                "Moderately Acidic": 0,
                "Neutral": 0,
                "Moderately Alkaline": 0,
                "Strongly Alkaline": 0,
            },
            "Iron": {"Deficient": 0, "Sufficient": 0},
            "Sulpher": {"Deficient": 0, "Sufficient": 0},
            "Aluminium": {"Deficient": 0, "Sufficient": 0},
            "Calcium": {"Deficient": 0, "Sufficient": 0},
            "magnesium": {"Deficient": 0, "Sufficient": 0},
        }

        # Step 4: Process Farm-based parameters
        for f in farms:
            if f.nitrogen_gperkg is not None:
                if f.nitrogen_gperkg < 30:
                    result["Nitrogen (g/kg)"]["Low"] += 1
                elif 30 <= f.nitrogen_gperkg <= 40:
                    result["Nitrogen (g/kg)"]["Medium"] += 1
                else:
                    result["Nitrogen (g/kg)"]["High"] += 1

            if f.phosphorus_ppm is not None:
                if f.phosphorus_ppm < 10:
                    result["Phosphorus (ppm)"]["Low"] += 1
                elif 10 <= f.phosphorus_ppm <= 15:
                    result["Phosphorus (ppm)"]["Medium"] += 1
                else:
                    result["Phosphorus (ppm)"]["High"] += 1

            if f.potassium_ppm is not None:
                if f.potassium_ppm < 110:
                    result["Potassium (ppm)"]["Low"] += 1
                elif 110 <= f.potassium_ppm <= 280:
                    result["Potassium (ppm)"]["Medium"] += 1
                else:
                    result["Potassium (ppm)"]["High"] += 1

            if f.carbon_organic_gperkg is not None:
                if f.carbon_organic_gperkg < 35:
                    result["Organic Carbon (g/kg)"]["Low"] += 1
                elif 35 <= f.carbon_organic_gperkg <= 40:
                    result["Organic Carbon (g/kg)"]["Medium"] += 1
                else:
                    result["Organic Carbon (g/kg)"]["High"] += 1

            if f.ph is not None:
                if f.ph < 5.5:
                    result["pH"]["Strongly Acidic"] += 1
                elif 5.5 <= f.ph < 6.7:
                    result["pH"]["Moderately Acidic"] += 1
                elif 6.7 <= f.ph <= 7.3:
                    result["pH"]["Neutral"] += 1
                elif 7.3 < f.ph <= 8.5:
                    result["pH"]["Moderately Alkaline"] += 1
                else:
                    result["pH"]["Strongly Alkaline"] += 1

        # Step 5: Process SoilParameter-based values
        for s in soil_params:
            if s.iron_extractable_ppm is not None:
                if s.iron_extractable_ppm < 20:
                    result["Iron"]["Deficient"] += 1
                else:
                    result["Iron"]["Sufficient"] += 1

            if s.sulphur_extractable_ppm is not None:
                if s.sulphur_extractable_ppm < 25:
                    result["Sulpher"]["Deficient"] += 1
                else:
                    result["Sulpher"]["Sufficient"] += 1

            if s.aluminium_extractable_ppm is not None:
                if s.aluminium_extractable_ppm < 35:
                    result["Aluminium"]["Deficient"] += 1
                else:
                    result["Aluminium"]["Sufficient"] += 1

            if s.calcium_extractable_ppm is not None:
                if s.calcium_extractable_ppm < 67:
                    result["Calcium"]["Deficient"] += 1
                else:
                    result["Calcium"]["Sufficient"] += 1

            if s.magnesium_extractable_ppm is not None:
                if s.magnesium_extractable_ppm < 50:
                    result["magnesium"]["Deficient"] += 1
                else:
                    result["magnesium"]["Sufficient"] += 1

        # Step 6: Add percentages
        for param in result:
            for cat in result[param]:
                count = result[param][cat]
                percent = (count / total_farms) * 100 if total_farms else 0
                result[param][cat] = {"count": count, "percentage": round(percent, 2)}

        return JSONResponse(
            status_code=200,
            content={
                "message": "Soil classification summary generated successfully.",
                "status_code": 200,
                "data": result,
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


DATE_COLUMNS = [
    "1/3/2025",
    "1/28/2025",
    "2/2/2025",
    "2/7/2025",
    "2/12/2025",
    "2/17/2025",
    "2/22/2025",
    "3/4/2025",
    "3/9/2025",
    "3/14/2025",
    "3/19/2025",
    "3/24/2025",
    "3/29/2025",
    "3/31/2025",
    "4/3/2025",
    "4/8/2025",
    "4/13/2025",
    "4/18/2025",
    "4/20/2025",
    "4/23/2025",
    "4/28/2025",
    "5/8/2025",
    "5/10/2025",
    "5/13/2025",
    "5/18/2025",
    "5/23/2025",
    "5/30/2025",
    "6/2/2025",
    "6/7/2025",
    "6/12/2025",
]


@route.get("/crop-cycle-data")
def get_crop_lifecycle_data(user_id: str, farm_id: str, db: Session = Depends(get_db)):

    farm = db.query(Farm).filter(Farm.id == farm_id).first()
    if not farm:
        return JSONResponse(
            status_code=400,
            content={
                "message": "Farm ID is required",
                "status_code": 400,
                "data": None,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )


    record = db.query(NdviStage).filter(NdviStage.farm_name == farm.farm_name).first()
    if not record:
        return JSONResponse(
            status_code=404,
            content={
                "message": f"No data found for farm: {farm.farm_name}",
                "status_code": 404,
                "data": None,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    crop_lifecycle_data = []

    for date_str in DATE_COLUMNS:
        try:
            dt = datetime.strptime(date_str, "%m/%d/%Y")
            iso_date = dt.strftime("%Y-%m-%d")
            attr_prefix = f"_{iso_date.replace('-', '_')}"
        except ValueError:
            continue

        ndvi_val = getattr(record, f"{attr_prefix}_ndvi", None)
        stage_val = getattr(record, f"{attr_prefix}_stage", None)

        if ndvi_val is not None or stage_val is not None:
            crop_lifecycle_data.append(
                {"date": iso_date, "data": ndvi_val, "crop_stage": stage_val}
            )

    return JSONResponse(
        status_code=200,
        content={
            "message": "NDVI values fetched successfully",
            "status_code": 200,
            "data": crop_lifecycle_data,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    )
