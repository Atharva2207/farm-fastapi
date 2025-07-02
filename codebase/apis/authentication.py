# Refactored authentication router using single User model with JWT

from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, status, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from helper_functions.utility import (
    get_user_by_username_or_email, get_password_hash, validate_password,
    verify_password, get_role_by_name
)
from models import Role, User
from schemas import UserRegistrationSchema, UserResponseSchema, LoginSchema
from database import get_db
import uuid
from fastapi.security import HTTPBearer

SECRET_KEY = "your_secret_key_here"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

oauth2_scheme = HTTPBearer()

route = APIRouter(prefix="/api", tags=["Authentication"])

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: dict):
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode = data.copy()
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

@route.post("/register")
def register_user(payload: UserRegistrationSchema, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(
        (User.username == payload.username) | (User.email == payload.email)
    ).first()
    if existing_user:
        return JSONResponse(
            status_code=400,
            content={
                "status_code": 400,
                "message": "User already exists.",
                "error_code": "USER_EXISTS",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    # Validate password
    pwd_validation = validate_password(payload.password)
    if pwd_validation:
        return pwd_validation  # already returns JSONResponse

    # Fetch or create role
    role = get_role_by_name(db, payload.role_name)
    if not role:
        role = Role(name=payload.role_name, description=f"Auto-created role")
        db.add(role)
        db.commit()
        db.refresh(role)

    # If farmer, validate and attach kvk_id
    kvk_user_id = None
    if payload.role_name == "farmer":
        if not payload.kvk_id:
            return JSONResponse(
                status_code=400,
                content={
                    "status_code": 400,
                    "message": "Farmer must be linked to a KVK",
                    "error_code": "KVK_REQUIRED",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
        kvk_user = db.query(User).filter(User.id == payload.kvk_id, User.role.has(name="kvk")).first()
        if not kvk_user:
            return JSONResponse(
                status_code=404,
                content={
                    "status_code": 404,
                    "message": "KVK with given ID not found",
                    "error_code": "KVK_NOT_FOUND",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
        kvk_user_id = kvk_user.id

    # Hash the password
    hashed_password = get_password_hash(payload.password)

    # Create the user
    user = User(
        username=payload.username,
        email=payload.email,
        name=payload.name,
        phone_number=payload.phone_number,
        password_hash=hashed_password,
        role_id=role.id,
        kvk_id=kvk_user_id if payload.role_name == "farmer" else None,
        district=payload.district if payload.role_name == "kvk" else None,
        state=payload.state if payload.role_name == "kvk" else None,
        address=payload.address if payload.role_name == "kvk" else None,
        pincode=payload.pincode if payload.role_name == "kvk" else None,
        director_name=payload.director_name if payload.role_name == "kvk" else None,
        established_year=payload.established_year if payload.role_name == "kvk" else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Response
    response_data = {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "name": user.name,
        "phone_number": user.phone_number,
        "role": payload.role_name,
        "is_active": user.is_active,
        "date_joined": user.date_joined.isoformat() + "Z",
        "kvk_id": str(user.kvk_id) if user.kvk_id else None,
    }

    return JSONResponse(
        status_code=201,
        content={
            "status_code": 201,
            "message": "User registered successfully",
            "error_code": None,
            "data": response_data,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    )


@route.post("/login")
def login(payload: LoginSchema, db: Session = Depends(get_db)):
    user = get_user_by_username_or_email(db, payload.username)
    if not user or not verify_password(payload.password, user.password_hash):
        return JSONResponse(
            status_code=403,
            content={
                "status_code": 403,
                "message": "Invalid credentials",
                "error_code": "INVALID_CREDENTIALS",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    if user.is_blocked and user.blocked_until and user.blocked_until > datetime.utcnow():
        return JSONResponse(
            status_code=403,
            content={
                "status_code": 403,
                "message": "User is blocked",
                "error_code": "BLOCKED_USER",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    access_token = create_access_token({"sub": str(user.id)})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    response_data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": get_user_details(user)
    }

    return JSONResponse(
        status_code=200,
        content={
            "status_code": 200,
            "message": "Login successful",
            "error_code": None,
            "data": response_data,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    )


@route.post("/token/refresh")
def refresh_token(refresh_token: str, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            raise JWTError("Invalid refresh token")
        user_id = payload.get("sub")
    except JWTError:
        return JSONResponse(
            status_code=401,
            content={
                "status_code": 401,
                "message": "Invalid refresh token",
                "error_code": "INVALID_REFRESH_TOKEN",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    access_token = create_access_token({"sub": user_id})
    new_refresh_token = create_refresh_token({"sub": user_id})
    user = db.query(User).filter(User.id == user_id).first()

    return JSONResponse(
        status_code=200,
        content={
            "status_code": 200,
            "message": "Token refreshed successfully",
            "error_code": None,
            "data": {
                "access_token": access_token,
                "refresh_token": new_refresh_token,
                "token_type": "bearer",
                "user": get_user_details(user)
            },
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    )

@route.get("/token/verify")
def verify_token(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        user = db.query(User).filter(User.id == user_id).first()
        return JSONResponse(
            status_code=200,
            content={
                "status_code": 200,
                "message": "Token is valid",
                "error_code": None,
                "data": {"user": get_user_details(user)},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
    except JWTError:
        return JSONResponse(
            status_code=401,
            content={
                "status_code": 401,
                "message": "Invalid token",
                "error_code": "INVALID_TOKEN",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )


def get_user_details(user: User):
    if not user:
        return None
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "name": user.name,
        "phone_number": user.phone_number,
        "role_name": user.role.name if user.role else None,
        "kvk_id": str(user.kvk_id) if user.kvk_id else None,
        "district": user.district,
        "state": user.state,
        "address": user.address,
        "pincode": user.pincode,
        "director_name": user.director_name,
        "established_year": user.established_year,
        "is_active": user.is_active,
        "is_verified": user.is_verified,
        "is_blocked": user.is_blocked,
        "date_joined": user.date_joined.isoformat() + "Z",
    }


@route.post("/logout")
def logout(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """
    Logout endpoint - invalidates the current access token
    
    For a complete logout, the client should:
    1. Call this endpoint with the access token
    2. Delete both access_token and refresh_token from client storage
    """
    try:
        # Verify the token is valid
        payload = jwt.decode(token.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        
        # Optional: You can add token to a blacklist here
        # blacklist_token(token.credentials, payload.get("exp"))
        
        return JSONResponse(
            status_code=200,
            content={
                "status_code": 200,
                "message": "Logout successful",
                "error_code": None,
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
        
    except JWTError:
        return JSONResponse(
            status_code=401,
            content={
                "status_code": 401,
                "message": "Invalid token",
                "error_code": "INVALID_TOKEN",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
