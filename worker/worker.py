import json
import os
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import boto3
from dotenv import load_dotenv

from worker.scraper.service import process_website


# ============================================================
# Environment configuration
# ============================================================

# Load worker/.env for local development.
#
# In ECS/Fargate, this file will not exist. In that case,
# the environment variables provided by ECS are used instead.
ENV_FILE = Path(__file__).resolve().parent / ".env"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)


AWS_REGION = os.getenv("AWS_REGION", "us-east-2")


def get_required_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is missing. "
            f"For local development, check {ENV_FILE}. "
            f"For ECS, provide it through the task definition."
        )

    return value


SQS_QUEUE_URL = get_required_env("SQS_QUEUE_URL")
DYNAMODB_TABLE_NAME = get_required_env("DYNAMODB_TABLE_NAME")
S3_BUCKET_NAME = get_required_env("S3_BUCKET_NAME")


# ============================================================
# AWS clients
# ============================================================

sqs = boto3.client(
    "sqs",
    region_name=AWS_REGION,
)

dynamodb = boto3.resource(
    "dynamodb",
    region_name=AWS_REGION,
)

s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
)

jobs_table = dynamodb.Table(
    DYNAMODB_TABLE_NAME
)


# ============================================================
# Helpers
# ============================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_job(
    job_id: str,
    status: str,
    progress: int,
    **extra_fields,
) -> None:
    expression_names = {
        "#status": "status",
        "#progress": "progress",
        "#updated_at": "updated_at",
    }

    expression_values = {
        ":status": status,
        ":progress": progress,
        ":updated_at": utc_now(),
    }

    update_expression = (
        "SET #status = :status, "
        "#progress = :progress, "
        "#updated_at = :updated_at"
    )

    for key, value in extra_fields.items():
        expression_names[f"#{key}"] = key
        expression_values[f":{key}"] = value
        update_expression += f", #{key} = :{key}"

    jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression=update_expression,
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
    )


def save_result_to_s3(
    job_id: str,
    result: dict,
) -> str:
    key = f"jobs/{job_id}/result.json"

    s3.put_object(
        Bucket=S3_BUCKET_NAME,
        Key=key,
        Body=json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )

    return key


# ============================================================
# Job processing
# ============================================================

def process_job(job: dict) -> None:
    job_id = job["job_id"]
    website_url = job["website_url"]

    print(f"[JOB] Starting job: {job_id}")
    print(f"[JOB] Website: {website_url}")

    update_job(
        job_id=job_id,
        status="RUNNING",
        progress=5,
        started_at=utc_now(),
    )

    try:
        print("[JOB] Starting real scraper...")

        # --------------------------------------------------------
        # Progress callback
        # --------------------------------------------------------

        def scraper_progress(progress: int) -> None:
            update_job(
                job_id=job_id,
                status="RUNNING",
                progress=progress,
            )

            print(
                f"[JOB] Progress: {progress}%"
            )

        # --------------------------------------------------------
        # Run scraper
        # --------------------------------------------------------

        report = process_website(
            target_url=website_url,
            progress_callback=scraper_progress,
        )

        print("[JOB] Scraper completed successfully")

        # --------------------------------------------------------
        # Final processing before completion
        # --------------------------------------------------------

        update_job(
            job_id=job_id,
            status="RUNNING",
            progress=90,
            pages_scraped=report.parsed_trafilatura_count,
        )

        result = {
            "job_id": job_id,
            **asdict(report),
        }

        s3_key = save_result_to_s3(
            job_id=job_id,
            result=result,
        )

        # --------------------------------------------------------
        # Mark job completed
        # --------------------------------------------------------

        update_job(
            job_id=job_id,
            status="COMPLETED",
            progress=100,
            completed_at=utc_now(),
            pages_scraped=report.parsed_trafilatura_count,
            s3_prefix=f"jobs/{job_id}/",
            result_s3_key=s3_key,
        )

        print(f"[JOB] Completed job: {job_id}")
        print(
            f"[JOB] Result saved to "
            f"s3://{S3_BUCKET_NAME}/{s3_key}"
        )

    except Exception as exc:
        print(f"[JOB] Failed job: {job_id}")
        print(traceback.format_exc())

        update_job(
            job_id=job_id,
            status="FAILED",
            progress=0,
            error=str(exc),
        )

        raise


# ============================================================
# SQS worker loop
# ============================================================

def main() -> None:
    print("[WORKER] Scraping worker started")
    print(f"[WORKER] AWS region: {AWS_REGION}")
    print(f"[WORKER] DynamoDB table: {DYNAMODB_TABLE_NAME}")
    print(f"[WORKER] S3 bucket: {S3_BUCKET_NAME}")
    print(f"[WORKER] SQS queue configured: {bool(SQS_QUEUE_URL)}")

    while True:
        response = sqs.receive_message(
            QueueUrl=SQS_QUEUE_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            VisibilityTimeout=7200,
        )

        messages = response.get("Messages", [])

        if not messages:
            continue

        for message in messages:
            receipt_handle = message["ReceiptHandle"]

            try:
                job = json.loads(message["Body"])

                print(
                    f"[WORKER] Received message: {job}"
                )

                process_job(job)

                sqs.delete_message(
                    QueueUrl=SQS_QUEUE_URL,
                    ReceiptHandle=receipt_handle,
                )

                print("[WORKER] SQS message deleted")

            except Exception:
                print(
                    "[WORKER] Job processing failed. "
                    "Message will become visible again "
                    "and can be retried."
                )


if __name__ == "__main__":
    main()