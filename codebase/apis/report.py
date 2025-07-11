from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from io import BytesIO

route = APIRouter(prefix="/report", tags=["Report"])

# Initialize boto3 client
s3_client = boto3.client("s3")


@route.post("/download-report")
def download_report():
    bucket_name = "c9s-tiff-files"
    key = "reports/report.pdf"

    try:
        s3_object = s3_client.get_object(Bucket=bucket_name, Key=key)
        file_stream = BytesIO(s3_object['Body'].read())
        content_type = s3_object.get('ContentType', 'application/pdf')

        return StreamingResponse(
            file_stream,
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="report.pdf"'
            }
        )

    except ClientError as e:
        raise HTTPException(status_code=404, detail=f"File not found: {e}")
    except BotoCoreError as e:
        raise HTTPException(status_code=500, detail=f"S3 error: {e}")