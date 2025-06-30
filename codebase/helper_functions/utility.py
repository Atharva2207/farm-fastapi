import datetime
from functools import wraps
import re
from typing import List
from fastapi import status, Query
from fastapi.responses import JSONResponse
from requests import Session
from sqlalchemy import func
from database import SessionLocal
from models import KVK, Role, User
from passlib.context import CryptContext
from re import compile
from datetime import datetime

timestamp = datetime.utcnow().isoformat()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_user(db: Session, api: str, roles: list = [], is_active: bool = True):
    if not len(roles):
        roles = [role.id for role in db.query(Role).all()]

    user = db.query(User).filter(User.apikey == api, User.role.in_(roles)).first()

    if not user:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "message": "User with given key and role does not exist",
                "status_code": status.HTTP_404_NOT_FOUND,
                "error_code": "USER_NOT_FOUND",
                "data": {},
                "timestamp": timestamp,
                # "request_id": log.id,
            },
        )

    if user and not user.is_active and is_active:
        return JSONResponse(
            status_code=status.HTTP_406_NOT_ACCEPTABLE,
            content={
                "message": "The account is not active. Please contact administrator",
                "status_code": status.HTTP_406_NOT_ACCEPTABLE,
                "error_code": "INACTIVE_USER",  # Fixed: More appropriate error code
                "data": {},
                "timestamp": timestamp,
                # "request_id": log.id,
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


def secure_pwd(raw_password):
    hashed = pwd_context.hash(raw_password)

    return hashed


def verify_pwd(plain, hash):
    return pwd_context.verify(plain, hash)


def validate_password(password: str):
    """
    Validate password against security requirements.

    Args:
        password (str): Password string to validate.

    Returns:
        JSONResponse or None: Returns JSONResponse with error details if validation fails,
        None if validation passes.
    """
    timestamp = datetime.utcnow().isoformat() + "Z"

    if len(password) < 8:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "message": "Password must be at least 8 characters long",
                "status_code": 400,
                "error_code": "PASSWORD_TOO_SHORT",
                "data": {"minimum_length": 8, "current_length": len(password)},
                "timestamp": timestamp,
            },
        )

    if not re.search(r"[a-z]", password):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "message": "Password must include at least one lowercase letter",
                "status_code": 400,
                "error_code": "PASSWORD_MISSING_LOWERCASE",
                "data": {},
                "timestamp": timestamp,
            },
        )

    if not re.search(r"[A-Z]", password):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "message": "Password must contain at least one uppercase letter",
                "status_code": 400,
                "error_code": "PASSWORD_MISSING_UPPERCASE",
                "data": {},
                "timestamp": timestamp,
            },
        )

    if not re.search(r"\d", password):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "message": "Password must contain at least one numeric digit",
                "status_code": 400,
                "error_code": "PASSWORD_MISSING_DIGIT",
                "data": {},
                "timestamp": timestamp,
            },
        )

    if not re.search(r"[!@#$%*?&]", password):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "message": "Password must contain at least one special character (!, @, #, $, %, *, ?, &)",
                "status_code": 400,
                "error_code": "PASSWORD_MISSING_SPECIAL_CHAR",
                "data": {
                    "allowed_special_chars": ["!", "@", "#", "$", "%", "*", "?", "&"]
                },
                "timestamp": timestamp,
            },
        )

    # If all validations pass, return None (no error)
    return None


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_role_by_name(db: Session, role_name: str):
    return db.query(Role).filter(Role.name == role_name).first()

def get_user_by_username_or_email(db: Session, username: str):
    return db.query(User).filter(
        (User.username == username) | (User.email == username) | (User.phone_number == username)
    ).first()

def get_kvk_by_username_or_email(db: Session, username: str):
    return db.query(KVK).filter(
        (KVK.email == username) | (KVK.phone_number == username) | (KVK.kvk_code == username)
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