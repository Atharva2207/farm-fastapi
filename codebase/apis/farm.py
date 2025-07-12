from fastapi import APIRouter, Depends, Query, HTTPException, Body
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID
from datetime import date, datetime
from database import get_db
from models import Farm, SoilParameter, User, NdviStage
from schemas import FarmPlotCreateSchema, FarmPlotUpdateSchema, FarmPlotFlexibleSchema, FarmFilterBody
from geoalchemy2.shape import to_shape
from fastapi_pagination import Page, paginate
from sqlalchemy import select

from helper_functions.utility import (
    build_response,
    safe_serialize,
)  # Assuming you have a response utility

route = APIRouter(prefix="/api", tags=["Farm"])


def serialize_farm(
    farm: Farm, include: Optional[List[str]] = None, db: Session = None
) -> dict:
    def safe_date(val):
        return val.isoformat() if isinstance(val, (date, datetime)) else val

    # 🟢 Fetch soil parameters manually
    soil = None
    if db:
        soil = db.query(SoilParameter).filter(SoilParameter.farm_id == farm.id).first()

    data = {
        "id": str(farm.id),
        "area": farm.area,
        "crop": farm.crop,
        "ai_yield": farm.ai_yield,
        "revenue": farm.revenue,
        "ndvi": farm.ndvi,
        "evi": farm.evi,
        "ndmi": farm.ndmi,
        "cab": farm.cab,
        "carbon_organic_gperkg": farm.carbon_organic_gperkg,
        "nitrogen_gperkg": farm.nitrogen_gperkg,
        "aluminium_extractable_ppm": getattr(soil, "aluminium_extractable_ppm", None),
        "bulk_density_gpercubic": getattr(soil, "bulk_density_gpercubic", None),
        "calcium_extractable_ppm": getattr(soil, "calcium_extractable_ppm", None),
        "clay_content_per": getattr(soil, "clay_content_per", None),
        "iron_extractable_ppm": getattr(soil, "iron_extractable_ppm", None),
        "magnesium_extractable_ppm": getattr(soil, "magnesium_extractable_ppm", None),
        "sulphur_extractable_ppm": getattr(soil, "sulphur_extractable_ppm", None),
        "ph": farm.ph,
        "phosphorus_ppm": farm.phosphorus_ppm,
        "potassium_ppm": farm.potassium_ppm,
        "farm_name": farm.farm_name,
        "sowing_date": safe_date(farm.sowing_date),
        "created_at": (
            farm.farmer.date_joined.isoformat() + "Z"
            if farm.farmer and farm.farmer.date_joined
            else None
        ),
    }

    # Optional includes
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



@route.post("/farmplots/filter", response_model=Page[FarmPlotFlexibleSchema])
def list_farmplots(
    db: Session = Depends(get_db),
    farmer_id: Optional[UUID] = Query(None),
    kvk_id: Optional[UUID] = Query(None),
    crop: Optional[str] = Query(None),
    include: Optional[str] = Query(None),
    body: FarmFilterBody = Body(default=None),
):
    query = db.query(Farm).filter(Farm.deleted == False)
    if farmer_id:
        query = query.filter(Farm.user_id == farmer_id)
    if kvk_id:
        query = query.filter(Farm.kvk_id == kvk_id)
    if crop:
        query = query.filter(Farm.crop.ilike(f"%{crop}%"))
    if body and body.ids:
        query = query.filter(Farm.id.in_(body.ids))

    include_fields = include.split(",") if include else []
    farms = query.all()
    serialized = [serialize_farm(f, include_fields, db) for f in farms]
    paginated = paginate(serialized)
    return build_response(paginated.dict())


@route.get("/farmplots/{id}/", response_model=FarmPlotFlexibleSchema)
def read_farmplot(id: UUID, db: Session = Depends(get_db)):
    farm = db.query(Farm).filter(Farm.id == id and Farm.deleted == False).first()
    if not farm:
        raise HTTPException(status_code=404, detail="Farm not found")
    return build_response(serialize_farm(farm, ["geometry", "farmer", "kvk"], db))


@route.post("/farmplots/", response_model=FarmPlotFlexibleSchema)
def create_farmplot(payload: FarmPlotCreateSchema, db: Session = Depends(get_db)):
    data = payload.dict()

    # If kvk_id is not provided, try to infer from farmer's parent_id
    if not data.get("kvk_id"):
        farmer = db.query(User).filter(User.id == data["user_id"], User.is_deleted == False).first()
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

    return build_response(serialize_farm(new_farm, ["geometry", "farmer", "kvk"], db))


@route.put("/farmplots/{id}/", response_model=FarmPlotFlexibleSchema)
def update_farmplot(
    id: UUID, payload: FarmPlotUpdateSchema, db: Session = Depends(get_db)
):
    farm = db.query(Farm).filter(Farm.id == id and Farm.deleted == False).first()
    if not farm:
        raise HTTPException(status_code=404, detail="Farm not found")
    for key, value in payload.dict(exclude_unset=True).items():
        setattr(farm, key, value)
    db.commit()
    db.refresh(farm)
    return build_response(serialize_farm(farm, ["geometry", "farmer", "kvk"], db))


@route.delete("/farmplots/{id}/")
def delete_farmplot(id: UUID, db: Session = Depends(get_db)):
    farm = db.query(Farm and Farm.deleted == False).filter(Farm.id == id).first()
    if not farm:
        raise HTTPException(status_code=404, detail="Farm not found")
    db.delete(farm)
    db.commit()
    return build_response({"message": "Farm deleted successfully"})

@route.get("/soil-classification-summary/")
def get_soil_classification_summary(parent_id: str, db: Session = Depends(get_db)):
    try:

        # Get direct children of parent_id (could be KVKs or farmers)
        children_lvl1 = db.query(User.id).filter(User.parent_id == parent_id, User.is_deleted == False).all()
        lvl1_ids = [row.id for row in children_lvl1]

        # Get their children (only if they exist, i.e., it's a super_admin)
        if lvl1_ids:
            children_lvl2 = db.query(User.id).filter(User.parent_id.in_(lvl1_ids), User.is_deleted == False).all()
            lvl2_ids = [row.id for row in children_lvl2]
        else:
            lvl2_ids = []

        # Final list of user_ids to fetch farms for
        # If there are level 2 children, use them (means super_admin)
        # Else, fallback to level 1 children (means KVK)
        target_user_ids = lvl2_ids if lvl2_ids else lvl1_ids

        # Fetch farms
        farms = db.query(Farm).filter(Farm.user_id.in_(target_user_ids), Farm.deleted == False).all()

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

        # Step 4: Get soil parameters
        soil_params = db.query(SoilParameter).filter(SoilParameter.farm_id.in_(farm_ids)).all()
        farm_lookup = {str(f.id): str(f.user_id) for f in farms}

        # Step 5: Base result structure
        def param_bucket():
            return {
                "Low": {"count": 0, "percentage": 0, "entries": []},
                "Medium": {"count": 0, "percentage": 0, "entries": []},
                "High": {"count": 0, "percentage": 0, "entries": []},
            }

        def suff_def_bucket():
            return {
                "Deficient": {"count": 0, "percentage": 0, "entries": []},
                "Sufficient": {"count": 0, "percentage": 0, "entries": []},
            }

        def ph_bucket():
            return {
                "Strongly Acidic": {"count": 0, "percentage": 0, "entries": []},
                "Moderately Acidic": {"count": 0, "percentage": 0, "entries": []},
                "Neutral": {"count": 0, "percentage": 0, "entries": []},
                "Moderately Alkaline": {"count": 0, "percentage": 0, "entries": []},
                "Strongly Alkaline": {"count": 0, "percentage": 0, "entries": []},
            }

        result = {
            "Nitrogen (g/kg)": param_bucket(),
            "Phosphorus (ppm)": param_bucket(),
            "Potassium (ppm)": param_bucket(),
            "Organic Carbon (g/kg)": param_bucket(),
            "pH": ph_bucket(),
            "Iron": suff_def_bucket(),
            "Sulpher": suff_def_bucket(),
            "Aluminium": suff_def_bucket(),
            "Calcium": suff_def_bucket(),
            "magnesium": suff_def_bucket(),
        }

        # Step 6: Process farm-level parameters
        for f in farms:
            farm_id = str(f.id)
            farmer_id = str(f.user_id)

            def add_entry(param, category):
                result[param][category]["count"] += 1
                result[param][category]["entries"].append({"farm_id": farm_id, "farmer_id": farmer_id})

            if f.nitrogen_gperkg is not None:
                if f.nitrogen_gperkg < 30:
                    add_entry("Nitrogen (g/kg)", "Low")
                elif 30 <= f.nitrogen_gperkg <= 40:
                    add_entry("Nitrogen (g/kg)", "Medium")
                else:
                    add_entry("Nitrogen (g/kg)", "High")

            if f.phosphorus_ppm is not None:
                if f.phosphorus_ppm < 10:
                    add_entry("Phosphorus (ppm)", "Low")
                elif 10 <= f.phosphorus_ppm <= 15:
                    add_entry("Phosphorus (ppm)", "Medium")
                else:
                    add_entry("Phosphorus (ppm)", "High")

            if f.potassium_ppm is not None:
                if f.potassium_ppm < 110:
                    add_entry("Potassium (ppm)", "Low")
                elif 110 <= f.potassium_ppm <= 280:
                    add_entry("Potassium (ppm)", "Medium")
                else:
                    add_entry("Potassium (ppm)", "High")

            if f.carbon_organic_gperkg is not None:
                if f.carbon_organic_gperkg < 35:
                    add_entry("Organic Carbon (g/kg)", "Low")
                elif 35 <= f.carbon_organic_gperkg <= 40:
                    add_entry("Organic Carbon (g/kg)", "Medium")
                else:
                    add_entry("Organic Carbon (g/kg)", "High")

            if f.ph is not None:
                if f.ph < 5.5:
                    add_entry("pH", "Strongly Acidic")
                elif 5.5 <= f.ph < 6.7:
                    add_entry("pH", "Moderately Acidic")
                elif 6.7 <= f.ph <= 7.3:
                    add_entry("pH", "Neutral")
                elif 7.3 < f.ph <= 8.5:
                    add_entry("pH", "Moderately Alkaline")
                else:
                    add_entry("pH", "Strongly Alkaline")

        # Step 7: Process SoilParameter values
        for s in soil_params:
            farm_id = str(s.farm_id)
            farmer_id = farm_lookup.get(farm_id)
            if not farmer_id:
                continue

            def add_soil_entry(param, level):
                result[param][level]["count"] += 1
                result[param][level]["entries"].append({"farm_id": farm_id, "farmer_id": farmer_id})

            if s.iron_extractable_ppm is not None:
                level = "Deficient" if s.iron_extractable_ppm < 20 else "Sufficient"
                add_soil_entry("Iron", level)

            if s.sulphur_extractable_ppm is not None:
                level = "Deficient" if s.sulphur_extractable_ppm < 25 else "Sufficient"
                add_soil_entry("Sulpher", level)

            if s.aluminium_extractable_ppm is not None:
                level = "Deficient" if s.aluminium_extractable_ppm < 35 else "Sufficient"
                add_soil_entry("Aluminium", level)

            if s.calcium_extractable_ppm is not None:
                level = "Deficient" if s.calcium_extractable_ppm < 67 else "Sufficient"
                add_soil_entry("Calcium", level)

            if s.magnesium_extractable_ppm is not None:
                level = "Deficient" if s.magnesium_extractable_ppm < 50 else "Sufficient"
                add_soil_entry("magnesium", level)

        # Step 8: Calculate percentages
        for param in result:
            for cat in result[param]:
                count = result[param][cat]["count"]
                result[param][cat]["percentage"] = round((count / total_farms) * 100, 2) if total_farms else 0

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

    farm = db.query(Farm).filter(Farm.id == farm_id and Farm.deleted == False).first()
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
