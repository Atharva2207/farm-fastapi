import re
from pydantic import BaseModel, EmailStr, Field, constr, validator
from typing import Literal, Optional, List
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
    username: str
    email: EmailStr
    name: str
    phone_number: Optional[str] = None
    password: str
    role_name: Literal["farmer", "super_admin"]  # KVK registers separately
    
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
    username: str  # Can be email or phone
    password: str
    user_type: Literal["user", "kvk"]  # To distinguish between User and KVK login


class UserResponseSchema(BaseModel):
    id: str
    username: str
    email: str
    name: str
    phone_number: Optional[str]
    role_name: str
    is_active: bool
    date_joined: datetime


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