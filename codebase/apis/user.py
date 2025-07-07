from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID
from sqlalchemy import func
from database import get_db
from models import User, Role, Farm
from schemas import UserFlexibleSchema, UserCreateSchema, UserUpdateSchema
from helper_functions.utility import build_response
from fastapi_pagination import Page, paginate
from fastapi_pagination.ext.sqlalchemy import paginate as sqlalchemy_paginate
from fastapi_pagination import Params

from sqlalchemy import func, distinct

route = APIRouter(prefix="/api/users", tags=["Users"])

from sqlalchemy import func, or_

def serialize_user(user: User, include: Optional[List[str]] = None, db: Session = None, filters: dict = None) -> dict:
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
        if "parent" in include and user.parent:
            data["parent"] = {
                "id": str(user.parent.id),
                "username": user.parent.username,
                "name": user.parent.name,
                "role_name": user.parent.role.name if user.parent.role else None  
            }
        if "children" in include:
            data["children"] = [
                {
                    "id": str(child.id),
                    "username": child.username,
                    "email": child.email,
                    "name": child.name,
                    "role_name": child.role.name if child.role else None  
                }
                for child in user.children if not child.is_deleted
            ]
        if ("total_area" in include or "farm_count" in include) and db:
            farm_query = db.query(
                func.coalesce(func.sum(Farm.area), 0),
                func.count(Farm.id)
            )

            role_name = user.role.name.lower() if user.role else ""

            if role_name == "super_admin":
                child_ids = [child.id for child in user.children if not child.is_deleted]
                farm_query = farm_query.filter(Farm.kvk_id.in_(child_ids))

            elif role_name in ["kvk", "admin"]:
                farm_query = farm_query.filter(Farm.kvk_id == user.id)

            elif role_name == "farmer":
                farm_query = farm_query.filter(Farm.user_id == user.id)

            # Apply additional filters (crop, etc.)
            if filters:
                if filters.get("crop"):
                    farm_query = farm_query.filter(Farm.crop.ilike(f"%{filters['crop']}%"))

            total_area, farm_count = farm_query.first()
            if "total_area" in include:
                data["total_area"] = round(total_area or 0, 4)
            if "farm_count" in include:
                data["farm_count"] = farm_count

    return data


@route.get("/", response_model=List[UserFlexibleSchema])
def list_users(
    db: Session = Depends(get_db),
    id: Optional[UUID] = Query(None),
    parent_id: Optional[UUID] = Query(None),
    username: Optional[str] = Query(None),
    email: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    crop: Optional[str] = Query(None),
    include: Optional[str] = Query(None),
    params: Params = Depends()
):
    query = db.query(User).filter(User.is_deleted == False)

    if id:
        query = query.filter(User.id == id)
    if parent_id:
        query = query.filter(User.parent_id == parent_id)
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

    # Collect farm-related filters to apply during serialization
    farm_filters = {
        "crop": crop,
    }

    result = sqlalchemy_paginate(query, params)
    result.items = [serialize_user(user, include_fields, db=db, filters=farm_filters) for user in result.items]

    return build_response(result.dict())

@route.get("/{id}/", response_model=UserFlexibleSchema)
def read_user(id: UUID, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return build_response(serialize_user(user, ["role", "parent"]))


@route.post("/", response_model=UserFlexibleSchema)
def create_user(payload: UserCreateSchema, db: Session = Depends(get_db)):
    new_user = User(**payload.dict())
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return build_response(serialize_user(new_user, ["role", "parent"]))


@route.put("/{id}/", response_model=UserFlexibleSchema)
def update_user(id: UUID, payload: UserUpdateSchema, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    for key, value in payload.dict(exclude_unset=True).items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return build_response(serialize_user(user, ["role", "parent"]))


@route.delete("/{id}/")
def delete_user(id: UUID, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == id, User.is_deleted == False).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_deleted = True  # Soft delete
    db.commit()

    return build_response({"message": "User deleted successfully"})

@route.get("/overview-metrics")
def get_overview_metrics(
    db: Session = Depends(get_db),
    kvk_id: Optional[UUID] = Query(None),  # input param, maps to parent_id internally
    farmer_id: Optional[UUID] = Query(None),
    farm_id: Optional[UUID] = Query(None),
):
    # --- Step 1: Smart fallback for kvk_id (treated as parent_id) ---
    if not kvk_id:
        if farmer_id:
            farmer = db.query(User).filter(User.id == farmer_id).first()
            if farmer:
                kvk_id = farmer.parent_id
        elif farm_id:
            farm = db.query(Farm).filter(Farm.id == farm_id).first()
            if farm:
                kvk_id = farm.kvk_id  # still mapped from kvk_id field

    # --- Step 2: Build filters ---
    filters = []
    if kvk_id:
        filters.append(Farm.kvk_id == kvk_id)
    if farmer_id:
        filters.append(Farm.user_id == farmer_id)
    if farm_id:
        filters.append(Farm.id == farm_id)

    # --- Step 3: Farm Metrics ---
    farm_query = db.query(Farm).filter(*filters)
    total_farms = farm_query.count()
    total_area = db.query(func.sum(Farm.area)).filter(*filters).scalar() or 0
    avg_yield = db.query(func.avg(Farm.ai_yield)).filter(*filters).scalar() or 0
    avg_ndvi = db.query(func.avg(Farm.ndvi)).filter(*filters).scalar() or 0
    total_crops = [row[0] for row in db.query(distinct(Farm.crop)).filter(*filters).all()]

    # --- Step 4: User Metrics ---
    kvk_role_id = db.query(Role.id).filter(Role.name == "kvk").scalar()
    farmer_role_id = db.query(Role.id).filter(Role.name == "farmer").scalar()

    if kvk_id or farmer_id or farm_id:
        filtered_farms = farm_query.all()
        parent_ids = {f.kvk_id for f in filtered_farms}
        farmer_ids = {f.user_id for f in filtered_farms}

        total_kvks = db.query(User).filter(User.id.in_(parent_ids), User.role_id == kvk_role_id).count()
        total_farmers = db.query(User).filter(User.id.in_(farmer_ids), User.role_id == farmer_role_id).count()
    else:
        total_kvks = db.query(User).filter(User.role_id == kvk_role_id).count()
        total_farmers = db.query(User).filter(User.role_id == farmer_role_id).count()

    # --- Step 5: Current Parent Info ---
    current_parent = None
    if kvk_id:
        parent_user = db.query(User).filter(User.id == kvk_id).first()
        if parent_user:
            current_parent = {
                "id": str(parent_user.id),
                "name": parent_user.name,
                "district": parent_user.district,
                "state": parent_user.state,
                "email": parent_user.email,
            }

    # --- Step 6: Response ---
    metrics = {
        "current_kvk": current_parent,
        "total_kvks": total_kvks,
        "total_farmers": total_farmers,
        "total_farms": total_farms,
        "total_area": round(total_area, 3),
        "average_yield": round(avg_yield, 3),
        "average_ndvi": round(avg_ndvi, 3),
        "total_crops": total_crops,
    }

    return build_response(metrics, message="Overview metrics fetched successfully")
