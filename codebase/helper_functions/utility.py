from datetime import datetime
from decimal import Decimal
from functools import wraps
import re
from typing import Any, Optional, List
from uuid import UUID
from fastapi import status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from database import SessionLocal
from models import Role, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_user(db: Session, api: str, roles: list = [], is_active: bool = True):
    if not len(roles):
        roles = [role.id for role in db.query(Role).all()]

    user = db.query(User).filter(User.apikey == api, User.role_id.in_(roles)).first()

    if not user:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "message": "User with given key and role does not exist",
                "status_code": status.HTTP_404_NOT_FOUND,
                "error_code": "USER_NOT_FOUND",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    if user and not user.is_active and is_active:
        return JSONResponse(
            status_code=status.HTTP_406_NOT_ACCEPTABLE,
            content={
                "message": "The account is not active. Please contact administrator",
                "status_code": status.HTTP_406_NOT_ACCEPTABLE,
                "error_code": "INACTIVE_USER",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
    return user


def has_permission(permission: List[int] = []):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            api_key = kwargs.get("api_key")
            db = SessionLocal()

            user = get_user(db, api_key, permission)

            if "user" in kwargs:
                kwargs["user"] = user
            return func(*args, **kwargs)

        return wrapper

    return decorator


def secure_pwd(raw_password: str) -> str:
    return pwd_context.hash(raw_password)


def verify_pwd(plain: str, hash: str) -> bool:
    return pwd_context.verify(plain, hash)


def validate_password(password: str):
    """
    Validate password against security requirements.
    Returns JSONResponse if invalid, None if valid.
    """
    timestamp = datetime.utcnow().isoformat() + "Z"

    if len(password) < 8:
        return JSONResponse(
            status_code=400,
            content={
                "status_code": 400,
                "message": "Password must be at least 8 characters long",
                "error_code": "PASSWORD_TOO_SHORT",
                "data": {},
                "timestamp": timestamp,
            },
        )
    if not re.search(r"[a-z]", password):
        return JSONResponse(
            status_code=400,
            content={
                "status_code": 400,
                "message": "Password must include at least one lowercase letter",
                "error_code": "PASSWORD_MISSING_LOWERCASE",
                "data": {},
                "timestamp": timestamp,
            },
        )
    if not re.search(r"[A-Z]", password):
        return JSONResponse(
            status_code=400,
            content={
                "status_code": 400,
                "message": "Password must include at least one uppercase letter",
                "error_code": "PASSWORD_MISSING_UPPERCASE",
                "data": {},
                "timestamp": timestamp,
            },
        )
    if not re.search(r"\d", password):
        return JSONResponse(
            status_code=400,
            content={
                "status_code": 400,
                "message": "Password must include at least one digit",
                "error_code": "PASSWORD_MISSING_DIGIT",
                "data": {},
                "timestamp": timestamp,
            },
        )
    if not re.search(r"[!@#$%*?&]", password):
        return JSONResponse(
            status_code=400,
            content={
                "status_code": 400,
                "message": "Password must include one special character (!@#$%*?&)",
                "error_code": "PASSWORD_MISSING_SPECIAL_CHAR",
                "data": {},
                "timestamp": timestamp,
            },
        )
    return None


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_role_by_name(db: Session, role_name: str):
    return db.query(Role).filter(Role.name == role_name).first()

def get_user_by_username_or_email(db: Session, username: str):
    return db.query(User).filter(
        (User.username == username) |
        (User.email == username) |
        (User.phone_number == username)
    ).first()


def get_role_by_name(db: Session, role_name: str):
    """Get role by name"""
    return db.query(Role).filter(Role.name == role_name).first()


def validate_password(password: str):
    """Validate password strength"""
    if len(password) < 8:
        return JSONResponse(
            status_code=400,
            content={
                "status_code": 400,
                "message": "Password must be at least 8 characters long",
                "error_code": "WEAK_PASSWORD",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
    return None


def get_password_hash(password: str) -> str:
    """Hash password using bcrypt"""
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    return pwd_context.hash(password)

def safe_serialize(obj):
    if isinstance(obj, dict):
        return {key: safe_serialize(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [safe_serialize(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(safe_serialize(item) for item in obj)
    elif isinstance(obj, set):
        return list(safe_serialize(item) for item in obj)
    elif isinstance(obj, UUID):
        return str(obj)
    elif isinstance(obj, datetime):
        return obj.isoformat() + "Z"
    elif isinstance(obj, Decimal):
        return float(obj)
    else:
        return obj

def build_response(
    data: Any,
    message: str = "Success",
    status_code: int = 200,
    error_code: Optional[str] = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=safe_serialize({
            "status_code": status_code,
            "message": message,
            "error_code": error_code,
            "data": data,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })
    )
