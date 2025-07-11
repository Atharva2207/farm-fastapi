from sqlalchemy import (
    JSON,
    UUID,
    Boolean,
    Column,
    Computed,
    ForeignKey,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from datetime import datetime
from database import Base
import uuid
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry


class Market(Base):
    __tablename__ = "market_data"

    id = Column(Integer, primary_key=True, index=True)
    state = Column(String(100), default="")
    district = Column(String(100), default="")
    market = Column(String(100), default="")
    commodity = Column(String(100), default="")
    variety = Column(String(100), default="")
    grade = Column(String(100), default="")
    arrival_date = Column(Date, nullable=False)
    min_price = Column(Float, default=0.0)
    max_price = Column(Float, default=0.0)
    modal_price = Column(Float, default=0.0)
    updated_date = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_arrival_date", "arrival_date"),
        Index("idx_state", "state"),
        Index("idx_district", "district"),
        Index("idx_commodity", "commodity"),
        Index(
            "idx_combo", "state", "district", "market", "commodity", "variety", "grade"
        ),
        UniqueConstraint(
            "state",
            "district",
            "market",
            "commodity",
            "variety",
            "grade",
            "arrival_date",
            name="uq_market_unique",
        ),
    )


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    name = Column(
        String(50), unique=True, index=True, nullable=False
    )  # farmer, kvk, super_admin
    description = Column(String(255), nullable=True, default="")


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # Basic Auth Info
    username = Column(String(150), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=True)
    password_hash = Column(String(255), nullable=False)

    # Personal Info
    name = Column(String(255), nullable=False)
    phone_number = Column(String(15), nullable=True)

    # Role & Hierarchy
    role_id = Column(
        Integer, ForeignKey("roles.id"), nullable=False
    )  # farmer, kvk, super_admin
    parent_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Status
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    is_blocked = Column(Boolean, default=False)
    blocked_until = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, default=False)

    # Timestamps
    date_joined = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # KVK-specific fields (nullable for non-KVKs)
    district = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    pincode = Column(String(10), nullable=True)
    director_name = Column(String(255), nullable=True)
    established_year = Column(String(4), nullable=True)

    # Relationships
    role = relationship("Role", backref="users")
    parent = relationship("User", remote_side=[id], backref="children")


class Farm(Base):
    __tablename__ = "farm"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # Both farmer and kvk are users
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )  # Farmer
    kvk_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)  # KVK

    # Spatial columns
    geometry = Column(Geometry("POLYGON"))
    area = Column(
        Float,
        Computed(
            "((ST_Area(ST_setSRID(geometry, 4326)::geography)) * 0.0002471054)", True
        ),
    )
    center = Column(Geometry("POINT"), Computed("(ST_Centroid(geometry))", True))
    crop = Column(String(100), nullable=True)
    ai_yield = Column(Float, nullable=True)
    revenue = Column(Float, nullable=True)
    farm_name = Column(String(255), nullable=True)
    lat = Column(Float, Computed("(ST_Y(ST_Centroid(geometry)))", True))
    lon = Column(Float, Computed("(ST_X(ST_Centroid(geometry)))", True))

    # Relationships
    farmer = relationship("User", foreign_keys=[user_id], backref="farms")
    kvk_user = relationship("User", foreign_keys=[kvk_id], backref="assigned_farms")
    bbox = Column(JSON, nullable=False)
    carbon_organic_gperkg = Column(Float, nullable=True)
    nitrogen_gperkg = Column(Float, nullable=True)
    ph = Column(Float, nullable=True)
    phosphorus_ppm = Column(Float, nullable=True)
    potassium_ppm = Column(Float, nullable=True)
    sowing_date = Column(Date, nullable=True)
    ndvi = Column(Float, nullable=True)
    cab = Column(Float, nullable=True)
    evi = Column(Float, nullable=True)
    ndmi = Column(Float, nullable=True)


class Satellites(Base):
    __tablename__ = "satellites"

    name = Column(String, primary_key=True)  # Full Sentinel/Planet name
    code = Column(String, nullable=False)  # e.g., "S1", "S2"
    region_url = Column(String, nullable=False)
    is_catalogue_enabled = Column(Boolean, default=True)
    cloud_cover = Column(Boolean, default=False)


class PlanetCollections(Base):
    __tablename__ = "planet_collections"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    farm_id = Column(String, nullable=False)
    collection_id = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    def __init__(self, farm_id, collection_id):
        self.farm_id = farm_id
        self.collection_id = collection_id


class Indices(Base):
    __tablename__ = "indices"

    name = Column(String, primary_key=True, nullable=False)
    code = Column(String, nullable=False)  # Formerly 'alias'
    evalscript = Column(String, nullable=False)
    statistical_evalscript = Column(String, nullable=True)
    satellite = Column(
        String, ForeignKey("satellites.name"), primary_key=True, nullable=False
    )
    legend = Column(JSON, nullable=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False
    )


class NDVIMeanValues(Base):
    __tablename__ = "ndvi_mean_values"

    id = Column(Integer, primary_key=True, autoincrement=True)
    farm_name = Column(String(50), nullable=True)

    # Monthly NDVI columns
    _2021_01 = Column("2021-01", Float, nullable=True)
    _2021_02 = Column("2021-02", Float, nullable=True)
    _2021_03 = Column("2021-03", Float, nullable=True)
    _2021_04 = Column("2021-04", Float, nullable=True)
    _2021_05 = Column("2021-05", Float, nullable=True)
    _2021_06 = Column("2021-06", Float, nullable=True)
    _2021_07 = Column("2021-07", Float, nullable=True)
    _2021_08 = Column("2021-08", Float, nullable=True)
    _2021_09 = Column("2021-09", Float, nullable=True)
    _2021_10 = Column("2021-10", Float, nullable=True)
    _2021_11 = Column("2021-11", Float, nullable=True)
    _2021_12 = Column("2021-12", Float, nullable=True)

    _2022_01 = Column("2022-01", Float, nullable=True)
    _2022_02 = Column("2022-02", Float, nullable=True)
    _2022_03 = Column("2022-03", Float, nullable=True)
    _2022_04 = Column("2022-04", Float, nullable=True)
    _2022_05 = Column("2022-05", Float, nullable=True)
    _2022_06 = Column("2022-06", Float, nullable=True)
    _2022_07 = Column("2022-07", Float, nullable=True)
    _2022_08 = Column("2022-08", Float, nullable=True)
    _2022_09 = Column("2022-09", Float, nullable=True)
    _2022_10 = Column("2022-10", Float, nullable=True)
    _2022_11 = Column("2022-11", Float, nullable=True)
    _2022_12 = Column("2022-12", Float, nullable=True)

    _2023_01 = Column("2023-01", Float, nullable=True)
    _2023_02 = Column("2023-02", Float, nullable=True)
    _2023_03 = Column("2023-03", Float, nullable=True)
    _2023_04 = Column("2023-04", Float, nullable=True)
    _2023_05 = Column("2023-05", Float, nullable=True)
    _2023_06 = Column("2023-06", Float, nullable=True)
    _2023_07 = Column("2023-07", Float, nullable=True)
    _2023_08 = Column("2023-08", Float, nullable=True)
    _2023_09 = Column("2023-09", Float, nullable=True)
    _2023_10 = Column("2023-10", Float, nullable=True)
    _2023_11 = Column("2023-11", Float, nullable=True)
    _2023_12 = Column("2023-12", Float, nullable=True)

    _2024_01 = Column("2024-01", Float, nullable=True)
    _2024_02 = Column("2024-02", Float, nullable=True)
    _2024_03 = Column("2024-03", Float, nullable=True)
    _2024_04 = Column("2024-04", Float, nullable=True)
    _2024_05 = Column("2024-05", Float, nullable=True)
    _2024_06 = Column("2024-06", Float, nullable=True)
    _2024_07 = Column("2024-07", Float, nullable=True)
    _2024_08 = Column("2024-08", Float, nullable=True)
    _2024_09 = Column("2024-09", Float, nullable=True)
    _2024_10 = Column("2024-10", Float, nullable=True)
    _2024_11 = Column("2024-11", Float, nullable=True)
    _2024_12 = Column("2024-12", Float, nullable=True)

    _2025_01 = Column("2025-01", Float, nullable=True)
    _2025_02 = Column("2025-02", Float, nullable=True)
    _2025_03 = Column("2025-03", Float, nullable=True)
    _2025_04 = Column("2025-04", Float, nullable=True)
    _2025_05 = Column("2025-05", Float, nullable=True)
    _2025_06 = Column("2025-06", Float, nullable=True)


class SoilParameter(Base):
    __tablename__ = "soil_parameters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    farm_name = Column(String(50), nullable=True)
    aluminium_extractable_ppm = Column(Float, nullable=True)
    bulk_density_gpercubic = Column(Integer, nullable=True)
    calcium_extractable_ppm = Column(Float, nullable=True)
    clay_content_per = Column(Float, nullable=True)
    iron_extractable_ppm = Column(Float, nullable=True)
    magnesium_extractable_ppm = Column(Float, nullable=True)
    sulphur_extractable_ppm = Column(Float, nullable=True)
    farm_id = Column(UUID(as_uuid=True), ForeignKey("farm.id"), nullable=True)

class NdviStage(Base):
    __tablename__ = "ndvi_and_crop_stages_wide_format"

    farm_name = Column(String(50), primary_key=True)
    crop = Column(String(50))

    _2025_01_03_ndvi = Column("2025-01-03_ndvi", Float)
    _2025_01_28_ndvi = Column("2025-01-28_ndvi", Float)
    _2025_02_02_ndvi = Column("2025-02-02_ndvi", Float)
    _2025_02_07_ndvi = Column("2025-02-07_ndvi", Float)
    _2025_02_12_ndvi = Column("2025-02-12_ndvi", Float)
    _2025_02_17_ndvi = Column("2025-02-17_ndvi", Float)
    _2025_02_22_ndvi = Column("2025-02-22_ndvi", Float)
    _2025_03_04_ndvi = Column("2025-03-04_ndvi", Float)
    _2025_03_09_ndvi = Column("2025-03-09_ndvi", Float)
    _2025_03_14_ndvi = Column("2025-03-14_ndvi", Float)
    _2025_03_19_ndvi = Column("2025-03-19_ndvi", Float)
    _2025_03_24_ndvi = Column("2025-03-24_ndvi", Float)
    _2025_03_29_ndvi = Column("2025-03-29_ndvi", Float)
    _2025_03_31_ndvi = Column("2025-03-31_ndvi", Float)
    _2025_04_03_ndvi = Column("2025-04-03_ndvi", Float)
    _2025_04_08_ndvi = Column("2025-04-08_ndvi", Float)
    _2025_04_13_ndvi = Column("2025-04-13_ndvi", Float)
    _2025_04_18_ndvi = Column("2025-04-18_ndvi", Float)
    _2025_04_20_ndvi = Column("2025-04-20_ndvi", Float)
    _2025_04_23_ndvi = Column("2025-04-23_ndvi", Float)
    _2025_04_28_ndvi = Column("2025-04-28_ndvi", Float)
    _2025_05_08_ndvi = Column("2025-05-08_ndvi", Float)
    _2025_05_10_ndvi = Column("2025-05-10_ndvi", Float)
    _2025_05_13_ndvi = Column("2025-05-13_ndvi", Float)
    _2025_05_18_ndvi = Column("2025-05-18_ndvi", Float)
    _2025_05_23_ndvi = Column("2025-05-23_ndvi", Float)
    _2025_05_30_ndvi = Column("2025-05-30_ndvi", Float)
    _2025_06_02_ndvi = Column("2025-06-02_ndvi", Float)
    _2025_06_07_ndvi = Column("2025-06-07_ndvi", Float)
    _2025_06_12_ndvi = Column("2025-06-12_ndvi", Float)

    _2025_01_03_stage = Column("2025-01-03_stage", String(50))
    _2025_01_28_stage = Column("2025-01-28_stage", String(50))
    _2025_02_02_stage = Column("2025-02-02_stage", String(50))
    _2025_02_07_stage = Column("2025-02-07_stage", String(50))
    _2025_02_12_stage = Column("2025-02-12_stage", String(50))
    _2025_02_17_stage = Column("2025-02-17_stage", String(50))
    _2025_02_22_stage = Column("2025-02-22_stage", String(50))
    _2025_03_04_stage = Column("2025-03-04_stage", String(50))
    _2025_03_09_stage = Column("2025-03-09_stage", String(50))
    _2025_03_14_stage = Column("2025-03-14_stage", String(50))
    _2025_03_19_stage = Column("2025-03-19_stage", String(50))
    _2025_03_24_stage = Column("2025-03-24_stage", String(50))
    _2025_03_29_stage = Column("2025-03-29_stage", String(50))
    _2025_03_31_stage = Column("2025-03-31_stage", String(50))
    _2025_04_03_stage = Column("2025-04-03_stage", String(50))
    _2025_04_08_stage = Column("2025-04-08_stage", String(50))
    _2025_04_13_stage = Column("2025-04-13_stage", String(50))
    _2025_04_18_stage = Column("2025-04-18_stage", String(50))
    _2025_04_20_stage = Column("2025-04-20_stage", String(50))
    _2025_04_23_stage = Column("2025-04-23_stage", String(50))
    _2025_04_28_stage = Column("2025-04-28_stage", String(50))
    _2025_05_08_stage = Column("2025-05-08_stage", String(50))
    _2025_05_10_stage = Column("2025-05-10_stage", String(50))
    _2025_05_13_stage = Column("2025-05-13_stage", String(50))
    _2025_05_18_stage = Column("2025-05-18_stage", String(50))
    _2025_05_23_stage = Column("2025-05-23_stage", String(50))
    _2025_05_30_stage = Column("2025-05-30_stage", String(50))
    _2025_06_02_stage = Column("2025-06-02_stage", String(50))
    _2025_06_07_stage = Column("2025-06-07_stage", String(50))
    _2025_06_12_stage = Column("2025-06-12_stage", String(50))