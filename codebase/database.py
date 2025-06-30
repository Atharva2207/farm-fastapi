from datetime import datetime
from fastapi import status
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from env_variables import setting

SQLALCHEMY_DATABASE_URL = "postgresql://{0}:{1}@{2}:{3}/{4}".format(
    setting.db_usr, setting.db_pwd, setting.db_host, setting.db_port, setting.db_name
)

pool_size = 200
max_overflow = 40

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, pool_size=pool_size, max_overflow=max_overflow
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# Dependency
def get_db():
    db = SessionLocal()
    timestamp = datetime.utcnow().isoformat()

    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db.close()
        print("Database error", e)

        # Log the error if you have logging functionality
        log_entry = {
            "status": "Failed",
            "message": "Database connection failed",
            "response": {"error": str(e)},
        }
        # log = log_api_request(db, **log_entry)  # Uncomment if you have logging

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "message": "Database connection failed. Please try again later",
                "status_code": 500,
                "error_code": "DATABASE_CONNECTION_ERROR",
                "data": {},
                "timestamp": timestamp,
                # "request_id": log.id,  # Uncomment if you have logging
            },
        )

    try:
        yield db
    finally:
        db.close()
