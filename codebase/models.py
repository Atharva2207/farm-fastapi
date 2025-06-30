from sqlalchemy import UUID, Boolean, Column, ForeignKey, Integer, String, Float, Date, DateTime, Index, Text, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
from database import Base
import uuid
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry

class Market(Base):
    __tablename__ = "market_data"

    id = Column(Integer, primary_key=True, index=True)
    state = Column(String(100), default='')
    district = Column(String(100), default='')
    market = Column(String(100), default='')
    commodity = Column(String(100), default='')
    variety = Column(String(100), default='')
    grade = Column(String(100), default='')
    arrival_date = Column(Date, nullable=False)
    min_price = Column(Float, default=0.0)
    max_price = Column(Float, default=0.0)
    modal_price = Column(Float, default=0.0)
    updated_date = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_arrival_date', 'arrival_date'),
        Index('idx_state', 'state'),
        Index('idx_district', 'district'),
        Index('idx_commodity', 'commodity'),
        Index('idx_combo', 'state', 'district', 'market', 'commodity', 'variety', 'grade'),
        UniqueConstraint('state', 'district', 'market', 'commodity', 'variety', 'grade', 'arrival_date', name='uq_market_unique'),
    )



class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    name = Column(String(50), unique=True, index=True, nullable=False)  # farmer, kvk, super_admin
    description = Column(String(255), nullable=True, default="")


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    username = Column(String(150), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    phone_number = Column(String(15), nullable=True)
    password_hash = Column(String(255), nullable=False)

    # Foreign Key
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)

    date_joined = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    is_blocked = Column(Boolean, default=False)
    blocked_until = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, default=False)


class KVK(Base):
    __tablename__ = "kvks"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    kvk_name = Column(String(255), nullable=False)
    kvk_code = Column(String(50), unique=True, nullable=False)  # Unique KVK identifier
    email = Column(String(255), unique=True, nullable=False)
    phone_number = Column(String(15), nullable=True)
    password_hash = Column(String(255), nullable=False)

    # Location details
    district = Column(String(100), nullable=False)
    state = Column(String(100), nullable=False)
    address = Column(Text, nullable=True)
    pincode = Column(String(10), nullable=True)

    # Admin details
    director_name = Column(String(255), nullable=True)
    established_year = Column(String(4), nullable=True)

    # Status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    is_blocked = Column(Boolean, default=False)
    blocked_until = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, default=False)

    # Timestamps
    date_joined = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Farm(Base):
    __tablename__ = "farm"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    kvk_id = Column(Integer, ForeignKey("kvks.id"), nullable=False)
    # Spatial columns
    geometry = Column(Geometry("POLYGON"))
    center = Column(Geometry(geometry_type='POINT', srid=4326), nullable=True)

    # Other attributes
    area = Column(Float, nullable=True)
    crop = Column(String(100), nullable=True)
    ai_yield = Column(Float, nullable=True)
    revenue = Column(Float, nullable=True)
    kvk = Column(String(255), nullable=True)
    ndvi = Column(Float, nullable=True)
    farmer_name = Column(String(255), nullable=True)
    farm_name = Column(String(255), nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
