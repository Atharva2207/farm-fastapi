import re
from pydantic import BaseModel, EmailStr, Field, constr, validator
from typing import Literal, Optional, List
from datetime import datetime, date
from uuid import UUID


class CurrentWeatherRequest(BaseModel):
    user_id: int
    farm_id: int
    fields: Optional[List[str]] = None
    units: Optional[str] = "metric"


class HistoricWeatherRequest(BaseModel):
    user_id: int
    farm_id: int
    start_time: datetime
    end_time: datetime
    timesteps: Optional[str] = "1d"
    fields: Optional[List[str]] = None
    units: Optional[str] = "metric"


class ForecastWeatherRequest(BaseModel):
    user_id: int
    farm_id: int
    timesteps: Optional[str] = "1d"
    fields: Optional[List[str]] = None
    units: Optional[str] = "metric"


# Response Models
class BaseResponse(BaseModel):
    message: str
    status_code: int
    timestamp: str
    response_time_ms: Optional[float] = None
    source: Optional[str] = None
    cache_expires_at: Optional[str] = None


class SuccessResponse(BaseResponse):
    data: dict


class ErrorResponse(BaseResponse):
    error_code: Optional[str] = None
    error: Optional[str] = None


class UserRegistrationSchema(BaseModel):
    username: Optional[str]
    email: Optional[EmailStr] = None
    name: str
    phone_number: Optional[str] = None
    password: str
    role_name: Literal["farmer", "super_admin", "kvk"]
    parent_id: Optional[str] = None

    district: Optional[str] = None
    state: Optional[str] = None
    address: Optional[str] = None
    pincode: Optional[str] = None
    director_name: Optional[str] = None
    established_year: Optional[str] = None

    @validator('phone_number')
    def validate_phone(cls, v):
        if v and not v.isdigit():
            raise ValueError('Phone number must contain only digits')
        if v and len(v) < 8:
            raise ValueError('Phone number must be at least 8 digits')
        return v


class LoginSchema(BaseModel):
    username: str
    password: str


class UserResponseSchema(BaseModel):
    id: str
    username: str
    email: str
    name: str
    phone_number: Optional[str]
    role_name: str
    is_active: bool
    is_verified: Optional[bool]
    date_joined: datetime

    # KVK-specific (optional)
    district: Optional[str]
    state: Optional[str]
    address: Optional[str]
    pincode: Optional[str]
    director_name: Optional[str]
    established_year: Optional[str]


class TokenSchema(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: Optional[str] = None


class UserMini(BaseModel):
    id: UUID
    username: str
    email: EmailStr
    name: str

    class Config:
        from_attributes = True


class FarmPlotCreateSchema(BaseModel):
    user_id: UUID
    parent_id: Optional[UUID] = None
    sowing_date: Optional[date] = None
    geometry: Optional[str] = None
    center: Optional[str] = None
    area: Optional[float] = None
    crop: Optional[str] = Field(..., max_length=100)
    ai_yield: Optional[float] = None
    revenue: Optional[float] = None
    ndvi: Optional[float] = None
    farm_name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    carbon_organic_gperkg: Optional[float] = None
    nitrogen_gperkg: Optional[float] = None
    ph: Optional[float] = None
    phosphorus_ppm: Optional[float] = None
    potassium_ppm: Optional[float] = None


class FarmPlotUpdateSchema(BaseModel):
    geometry: Optional[str]
    center: Optional[str]
    area: Optional[float]
    crop: Optional[str]
    ai_yield: Optional[float]
    revenue: Optional[float]
    ndvi: Optional[float]
    farm_name: Optional[str]
    lat: Optional[float]
    lon: Optional[float]
    carbon_organic_gperkg: Optional[float]
    nitrogen_gperkg: Optional[float]
    ph: Optional[float]
    phosphorus_ppm: Optional[float]
    potassium_ppm: Optional[float]


class FarmPlotFlexibleSchema(BaseModel):
    id: UUID
    area: Optional[float] = None
    crop: Optional[str] = None
    ai_yield: Optional[float] = None
    revenue: Optional[float] = None
    ndvi: Optional[float] = None
    evi: Optional[float] = None
    ndmi: Optional[float] = None
    cab: Optional[float] = None
    geometry: Optional[str] = None
    created_at: Optional[str] = None
    farmer: Optional[UserMini] = None
    kvk: Optional[UserMini] = None
    carbon_organic_gperkg: Optional[float] = None
    nitrogen_gperkg: Optional[float] = None
    ph: Optional[float] = None
    phosphorus_ppm: Optional[float] = None
    potassium_ppm: Optional[float] = None

    # Added missing fields
    farm_name: Optional[str] = None
    # lat: Optional[float] = None
    # lon: Optional[float] = None
    # bbox: Optional[List[float]] = None
    sowing_date: Optional[date] = None

    class Config:
        from_attributes = True

class UserFlexibleSchema(BaseModel):
    id: UUID
    username: str
    email: EmailStr
    name: str
    phone_number: Optional[str] = None
    role: Optional[UserMini] = None
    parent: Optional[UserMini] = None
    is_active: bool
    is_verified: bool
    is_blocked: bool
    blocked_until: Optional[datetime] = None
    is_deleted: bool
    date_joined: datetime
    last_updated: datetime

    district: Optional[str]
    state: Optional[str]
    address: Optional[str]
    pincode: Optional[str]
    director_name: Optional[str]
    established_year: Optional[str]

    total_area: Optional[float] = None
    farm_count: Optional[int] = None

    class Config:
        from_attributes = True


class UserCreateSchema(BaseModel):
    username: str = Field(..., min_length=3)
    email: Optional[EmailStr] = None
    password: str = Field(..., min_length=6)
    name: str
    phone_number: Optional[str] = None
    role_id: int
    parent_id: Optional[UUID] = None
    district: Optional[str] = None
    state: Optional[str] = None
    address: Optional[str] = None
    pincode: Optional[str] = None
    director_name: Optional[str] = None
    established_year: Optional[str] = None


class UserUpdateSchema(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    role_id: Optional[int] = None
    parent_id: Optional[UUID] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None
    is_blocked: Optional[bool] = None
    blocked_until: Optional[datetime] = None
    district: Optional[str] = None
    state: Optional[str] = None
    address: Optional[str] = None
    pincode: Optional[str] = None
    director_name: Optional[str] = None
    established_year: Optional[str] = None