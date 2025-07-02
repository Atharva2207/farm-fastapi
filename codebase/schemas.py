import re
from pydantic import BaseModel, EmailStr, Field, constr, validator
from typing import Literal, Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from uuid import UUID
from datetime import datetime


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
    email: Optional[EmailStr]
    name: str
    phone_number: Optional[str] = None
    password: str
    role_name: Literal["farmer", "super_admin", "kvk"]

    # Optional KVK-association for farmers
    kvk_id: Optional[str] = None

    # KVK-specific fields
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


class KVKRegistrationSchema(BaseModel):
    kvk_name: str
    kvk_code: str
    email: EmailStr
    phone_number: Optional[str] = None
    password: str
    district: str
    state: str
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
    refresh_token: Optional[str] = None  # If you're using refresh tokens

class KVKResponseSchema(BaseModel):
    id: str
    kvk_name: str
    kvk_code: str
    email: str
    phone_number: Optional[str]
    district: str
    state: str
    is_active: bool
    is_verified: bool
    date_joined: datetime


class UserMini(BaseModel):
    id: UUID
    username: str
    email: str
    name: str

    class Config:
        from_attributes = True


class FarmPlotCreateSchema(BaseModel):
    user_id: UUID
    kvk_id: UUID
    geometry: Optional[str]
    center: Optional[str]
    area: Optional[float]
    crop: Optional[str] = Field(..., max_length=100)
    ai_yield: Optional[float]
    revenue: Optional[float]
    ndvi: Optional[float]
    farm_name: Optional[str]
    lat: Optional[float]
    lon: Optional[float]


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


class FarmPlotFlexibleSchema(BaseModel):
    id: UUID
    area: Optional[float]
    crop: Optional[str]
    ai_yield: Optional[float]
    revenue: Optional[float]
    ndvi: Optional[float]
    geometry: Optional[str] = None
    created_at: Optional[str]
    farmer: Optional[UserMini] = None
    kvk: Optional[UserMini] = None

    class Config:
        from_attributes = True

class UserMini(BaseModel):
    id: UUID
    username: str
    email: EmailStr
    name: str

    class Config:
        from_attributes = True


class UserFlexibleSchema(BaseModel):
    id: UUID
    username: str
    email: EmailStr
    name: str
    phone_number: Optional[str] = None
    role: Optional[UserMini] = None
    kvk_user: Optional[UserMini] = None
    is_active: bool
    is_verified: bool
    is_blocked: bool
    blocked_until: Optional[datetime] = None
    is_deleted: bool
    date_joined: datetime
    last_updated: datetime

    # KVK-specific fields
    district: Optional[str]
    state: Optional[str]
    address: Optional[str]
    pincode: Optional[str]
    director_name: Optional[str]
    established_year: Optional[str]

    class Config:
        from_attributes = True


class UserCreateSchema(BaseModel):
    username: str = Field(..., min_length=3)
    email: EmailStr
    password: str = Field(..., min_length=6)
    name: str
    phone_number: Optional[str] = None
    role_id: int
    kvk_id: Optional[UUID] = None
    district: Optional[str]
    state: Optional[str]
    address: Optional[str]
    pincode: Optional[str]
    director_name: Optional[str]
    established_year: Optional[str]


class UserUpdateSchema(BaseModel):
    name: Optional[str]
    email: Optional[EmailStr]
    phone_number: Optional[str]
    role_id: Optional[int]
    kvk_id: Optional[UUID]
    is_active: Optional[bool]
    is_verified: Optional[bool]
    is_blocked: Optional[bool]
    blocked_until: Optional[datetime]
    district: Optional[str]
    state: Optional[str]
    address: Optional[str]
    pincode: Optional[str]
    director_name: Optional[str]
    established_year: Optional[str]