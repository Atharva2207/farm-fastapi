from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID

from database import get_db
from models import User, Role, Farm
from schemas import UserFlexibleSchema, UserCreateSchema, UserUpdateSchema
from helper_functions.utility import build_response
from fastapi_pagination import Page, paginate
from fastapi_pagination.ext.sqlalchemy import paginate as sqlalchemy_paginate
from fastapi_pagination import Params

from sqlalchemy import func, distinct

route = APIRouter(prefix="/api/users", tags=["Users"])

def serialize_user(user: User, include: Optional[List[str]] = None) -> dict:
    data = {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "name": user.name,
        "phone_number": user.phone_number,
        "is_active": user.is_active,
        "is_verified": user.is_verified,
        "is_blocked": user.is_blocked,
        "blocked_until": user.blocked_until.isoformat() + "Z" if user.blocked_until else None,
        "date_joined": user.date_joined.isoformat() + "Z",
        "last_updated": user.last_updated.isoformat() + "Z",
        "district": user.district,
        "state": user.state,
        "address": user.address,
        "pincode": user.pincode,
        "director_name": user.director_name,
        "established_year": user.established_year,
    }

    if include:
        if "role" in include and user.role:
            data["role"] = {
                "id": user.role.id,
                "name": user.role.name,
                "description": user.role.description
            }
        if "kvk" in include and user.kvk_user:
            data["kvk"] = {
                "id": str(user.kvk_user.id),
                "username": user.kvk_user.username,
                "name": user.kvk_user.name
            }
        if "children" in include:
            data["children"] = [
                {
                    "id": str(child.id),
                    "username": child.username,
                    "email": child.email,
                    "name": child.name
                }
                for child in user.farmers if not child.is_deleted
            ]

    return data



@route.get("/", response_model=List[UserFlexibleSchema])
def list_users(
    db: Session = Depends(get_db),
    id: Optional[UUID] = Query(None),
    kvk_id: Optional[UUID] = Query(None),
    username: Optional[str] = Query(None),
    email: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    include: Optional[str] = Query(None),
    params: Params = Depends()  # enables limit/offset/page query params
):
    query = db.query(User).filter(User.is_deleted == False)

    if id:
        query = query.filter(User.id == id)
    if kvk_id:
        query = query.filter(User.kvk_id == kvk_id)
    if username:
        query = query.filter(User.username == username)
    if email:
        query = query.filter(User.email.ilike(f"%{email}%"))
    if role:
        query = query.join(Role).filter(Role.name == role)
    if state:
        query = query.filter(User.state.ilike(f"%{state}%"))
    if district:
        query = query.filter(User.district.ilike(f"%{district}%"))

    include_fields = include.split(",") if include else []

    # Run paginated query and serialize each result
    result = sqlalchemy_paginate(query, params)
    result.items = [serialize_user(user, include_fields) for user in result.items]

    return build_response(result.dict())

@route.get("/{id}/", response_model=UserFlexibleSchema)
def read_user(id: UUID, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return build_response(serialize_user(user, ["role", "kvk"]))


@route.post("/", response_model=UserFlexibleSchema)
def create_user(payload: UserCreateSchema, db: Session = Depends(get_db)):
    new_user = User(**payload.dict())
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return build_response(serialize_user(new_user, ["role", "kvk"]))


@route.put("/{id}/", response_model=UserFlexibleSchema)
def update_user(id: UUID, payload: UserUpdateSchema, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    for key, value in payload.dict(exclude_unset=True).items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return build_response(serialize_user(user, ["role", "kvk"]))


@route.delete("/{id}/")
def delete_user(id: UUID, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return build_response({"message": "User deleted successfully"})



@route.get("/overview-metrics")
def get_overview_metrics(
    db: Session = Depends(get_db),
    kvk_id: Optional[UUID] = Query(None),
    farmer_id: Optional[UUID] = Query(None),
    farm_id: Optional[UUID] = Query(None),
):
    filters = []
    if kvk_id:
        filters.append(Farm.kvk_id == kvk_id)
    if farmer_id:
        filters.append(Farm.user_id == farmer_id)
    if farm_id:
        filters.append(Farm.id == farm_id)

    # --- FARM METRICS ---
    farm_query = db.query(Farm).filter(*filters)
    total_farms = farm_query.count()
    total_area = db.query(func.sum(Farm.area)).filter(*filters).scalar() or 0
    avg_yield = db.query(func.avg(Farm.ai_yield)).filter(*filters).scalar() or 0
    avg_ndvi = db.query(func.avg(Farm.ndvi)).filter(*filters).scalar() or 0
    total_crops = db.query(func.count(distinct(Farm.crop))).filter(*filters).scalar() or 0

    # --- USER METRICS ---
    kvk_role_id = db.query(Role.id).filter(Role.name == "kvk").scalar()
    farmer_role_id = db.query(Role.id).filter(Role.name == "farmer").scalar()

    if kvk_id or farmer_id or farm_id:
        filtered_farms = farm_query.all()
        kvk_ids = {f.kvk_id for f in filtered_farms}
        farmer_ids = {f.user_id for f in filtered_farms}

        total_kvks = db.query(User).filter(User.id.in_(kvk_ids), User.role_id == kvk_role_id).count()
        total_farmers = db.query(User).filter(User.id.in_(farmer_ids), User.role_id == farmer_role_id).count()
    else:
        total_kvks = db.query(User).filter(User.role_id == kvk_role_id).count()
        total_farmers = db.query(User).filter(User.role_id == farmer_role_id).count()

    # --- CURRENT KVK INFO ---
    current_kvk = None
    if kvk_id:
        kvk_user = db.query(User).filter(User.id == kvk_id).first()
        if kvk_user:
            current_kvk = {
                "id": str(kvk_user.id),
                "name": kvk_user.name,
                "district": kvk_user.district,
                "state": kvk_user.state,
                "email": kvk_user.email,
            }

    # --- Final Response ---
    metrics = {
        "current_kvk": current_kvk,
        "total_kvks": total_kvks,
        "total_farmers": total_farmers,
        "total_farms": total_farms,
        "total_area": round(total_area, 3),
        "average_yield": round(avg_yield, 3),
        "average_ndvi": round(avg_ndvi, 3),
        "total_crops": total_crops,
    }

    return build_response(metrics, message="Overview metrics fetched successfully")