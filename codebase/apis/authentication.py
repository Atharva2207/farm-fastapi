from datetime import datetime
from re import compile
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from helper_functions.utility import get_kvk_by_username_or_email, get_password_hash, get_role_by_name, get_user_by_username_or_email, validate_password, verify_password, verify_pwd
from models import KVK, Role, User
from schemas import UserRegistrationSchema, UserResponseSchema, KVKRegistrationSchema, KVKResponseSchema, LoginSchema
from database import get_db
from datetime import datetime

route = APIRouter(prefix="/auth", tags=["Authentication"])

@route.post("/register/user", response_model=UserResponseSchema)
def register_user(payload: UserRegistrationSchema, db: Session = Depends(get_db)):
    """Register a new farmer or super admin user"""
    
    # Check if user already exists
    existing_user = db.query(User).filter(
        (User.username == payload.username) | 
        (User.email == payload.email) |
        (User.phone_number == payload.phone_number)
    ).first()
    
    if existing_user:
        if existing_user.is_deleted:
            error_msg = "Account cannot be created with these credentials."
            error_code = "DELETED_USER"
        else:
            error_msg = "User already exists with this username, email, or phone number"
            error_code = "USER_EXISTS"
            
        return JSONResponse(
            status_code=403,
            content={
                "status_code": 403,
                "message": error_msg,
                "error_code": error_code,
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
    
    # Validate password
    password_validation = validate_password(payload.password)
    if password_validation:
        return password_validation
    
    # Get role or create if doesn't exist
    role = get_role_by_name(db, payload.role_name)
    if not role:
        # Auto-create role if it's a valid role name
        valid_roles = ["farmer", "super_admin"]
        if payload.role_name in valid_roles:
            role_descriptions = {
                "farmer": "Farmer user with basic access to farm management",
                "super_admin": "Super administrator with full system access"
            }
            
            role = Role(
                name=payload.role_name,
                description=role_descriptions[payload.role_name]
            )
            db.add(role)
            db.commit()
            db.refresh(role)
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "status_code": 400,
                    "message": f"Invalid role: {payload.role_name}. Valid roles are: {', '.join(valid_roles)}",
                    "error_code": "INVALID_ROLE",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
    
    # Create user
    try:
        hashed_password = get_password_hash(payload.password)
        user = User(
            username=payload.username,
            email=payload.email,
            name=payload.name,
            phone_number=payload.phone_number,
            password_hash=hashed_password,
            role_id=role.id
        )
        
        db.add(user)
        db.commit()
        db.refresh(user)
        
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=500,
            content={
                "status_code": 500,
                "message": "Failed to create user. Please try again.",
                "error_code": "USER_CREATION_FAILED",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
    
    # Prepare response data with proper datetime serialization
    response_data = {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "name": user.name,
        "phone_number": user.phone_number,
        "role_name": role.name,
        "is_active": user.is_active,
        "date_joined": user.date_joined.isoformat() + "Z" if user.date_joined else None,
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


@route.post("/register/kvk", response_model=KVKResponseSchema)
def register_kvk(payload: KVKRegistrationSchema, db: Session = Depends(get_db)):
    """Register a new KVK (Krishi Vigyan Kendra)"""
    
    # Check if KVK already exists
    existing_kvk = db.query(KVK).filter(
        (KVK.email == payload.email) |
        (KVK.phone_number == payload.phone_number) |
        (KVK.kvk_code == payload.kvk_code)
    ).first()
    
    if existing_kvk:
        if existing_kvk.is_deleted:
            error_msg = "KVK cannot be registered with these credentials."
            error_code = "DELETED_KVK"
        else:
            error_msg = "KVK already exists with this email, phone number, or KVK code"
            error_code = "KVK_EXISTS"
            
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "status_code": status.HTTP_403_FORBIDDEN,
                "message": error_msg,
                "error_code": error_code,
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
    
    # Validate password
    password_validation = validate_password(payload.password)
    if password_validation:
        return password_validation
    
    # Create KVK
    hashed_password = get_password_hash(payload.password)
    kvk = KVK(
        kvk_name=payload.kvk_name,
        kvk_code=payload.kvk_code,
        email=payload.email,
        phone_number=payload.phone_number,
        password_hash=hashed_password,
        district=payload.district,
        state=payload.state,
        address=payload.address,
        pincode=payload.pincode,
        director_name=payload.director_name,
        established_year=payload.established_year
    )
    
    db.add(kvk)
    db.commit()
    db.refresh(kvk)
    
    response_data = {
        "id": str(kvk.id),
        "kvk_name": kvk.kvk_name,
        "kvk_code": kvk.kvk_code,
        "email": kvk.email,
        "phone_number": kvk.phone_number,
        "district": kvk.district,
        "state": kvk.state,
        "is_active": kvk.is_active,
        "is_verified": kvk.is_verified,
        "date_joined": kvk.date_joined.isoformat() + "Z",
    }
    
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "status_code": status.HTTP_201_CREATED,
            "message": "KVK registered successfully. Awaiting admin verification.",
            "error_code": None,
            "data": response_data,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    )


@route.post("/login")
def login(payload: LoginSchema, db: Session = Depends(get_db)):
    """Login for both Users and KVKs"""
    
    if payload.user_type == "user":
        # Login for User (Farmer/Super Admin)
        user = get_user_by_username_or_email(db, payload.username)
        
        if not user:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "status_code": status.HTTP_403_FORBIDDEN,
                    "message": "User does not exist",
                    "error_code": "USER_NOT_FOUND",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
        
        # Check various user statuses
        if user.is_deleted:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "status_code": status.HTTP_403_FORBIDDEN,
                    "message": "This user is marked for deletion",
                    "error_code": "DELETED_USER",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
        
        if not user.is_active:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "status_code": status.HTTP_401_UNAUTHORIZED,
                    "message": "User is not active. Please activate your account first",
                    "error_code": "INACTIVE_USER",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
        
        if user.is_blocked:
            if user.blocked_until and user.blocked_until > datetime.utcnow():
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "status_code": status.HTTP_403_FORBIDDEN,
                        "message": "Account is temporarily blocked. Please try again later.",
                        "error_code": "BLOCKED_USER",
                        "data": {},
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    },
                )
            else:
                # Unblock user if block period has expired
                user.is_blocked = False
                user.blocked_until = None
                db.commit()
        
        # Verify password
        if not verify_password(payload.password, user.password_hash):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "status_code": status.HTTP_403_FORBIDDEN,
                    "message": "Incorrect password",
                    "error_code": "INVALID_PASSWORD",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
        
        # Get role information
        role = db.query(Role).filter(Role.id == user.role_id).first()
        
        response_data = {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "name": user.name,
            "phone_number": user.phone_number,
            "role": role.name if role else None,
            "is_active": user.is_active,
            "user_type": "user"
        }
    
    elif payload.user_type == "kvk":
        # Login for KVK
        kvk = get_kvk_by_username_or_email(db, payload.username)
        
        if not kvk:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "status_code": status.HTTP_403_FORBIDDEN,
                    "message": "KVK does not exist",
                    "error_code": "KVK_NOT_FOUND",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
        
        # Check various KVK statuses
        if kvk.is_deleted:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "status_code": status.HTTP_403_FORBIDDEN,
                    "message": "This KVK is marked for deletion",
                    "error_code": "DELETED_KVK",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
        
        if not kvk.is_active:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "status_code": status.HTTP_401_UNAUTHORIZED,
                    "message": "KVK is not active",
                    "error_code": "INACTIVE_KVK",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
        
        if not kvk.is_verified:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "status_code": status.HTTP_401_UNAUTHORIZED,
                    "message": "KVK is not verified by admin yet",
                    "error_code": "UNVERIFIED_KVK",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
        
        if kvk.is_blocked:
            if kvk.blocked_until and kvk.blocked_until > datetime.utcnow():
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "status_code": status.HTTP_403_FORBIDDEN,
                        "message": "KVK is temporarily blocked. Please try again later.",
                        "error_code": "BLOCKED_KVK",
                        "data": {},
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    },
                )
            else:
                # Unblock KVK if block period has expired
                kvk.is_blocked = False
                kvk.blocked_until = None
                db.commit()
        
        # Verify password
        if not verify_password(payload.password, kvk.password_hash):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "status_code": status.HTTP_403_FORBIDDEN,
                    "message": "Incorrect password",
                    "error_code": "INVALID_PASSWORD",
                    "data": {},
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                },
            )
        
        response_data = {
            "id": str(kvk.id),
            "kvk_name": kvk.kvk_name,
            "kvk_code": kvk.kvk_code,
            "email": kvk.email,
            "phone_number": kvk.phone_number,
            "district": kvk.district,
            "state": kvk.state,
            "is_active": kvk.is_active,
            "is_verified": kvk.is_verified,
            "user_type": "kvk"
        }
    
    else:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "status_code": status.HTTP_400_BAD_REQUEST,
                "message": "Invalid user type",
                "error_code": "INVALID_USER_TYPE",
                "data": {},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )
    
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status_code": status.HTTP_200_OK,
            "message": "Login successful",
            "error_code": None,
            "data": response_data,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    )
