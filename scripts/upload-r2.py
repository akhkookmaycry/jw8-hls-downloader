#!/usr/bin/env python3
"""Upload file to Cloudflare R2 storage"""

import sys
import os
import boto3
from botocore.config import Config


def upload_to_r2(file_path, bucket_name, object_name=None):
    """Upload file to R2"""

    # Get credentials from environment
    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")

    # Build endpoint - always use account_id based endpoint for reliability
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    print(f"Using endpoint: {endpoint}")

    if not all([account_id, access_key, secret_key]):
        print("ERROR: Missing R2 credentials")
        sys.exit(1)

    if object_name is None:
        object_name = os.path.basename(file_path)

    # Create S3 client for R2
    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )

    # Get file size
    file_size = os.path.getsize(file_path)
    print(f"Uploading {object_name} ({file_size / (1024 * 1024):.2f} MB)...")

    # Upload file
    s3_client.upload_file(
        file_path, bucket_name, object_name, ExtraArgs={"ContentType": "video/mp4"}
    )

    # Generate public URL
    public_url = f"https://pub-{account_id}.r2.dev/{object_name}"

    print(f"Upload complete!")
    print(f"Public URL: {public_url}")

    return public_url


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python upload-r2.py <file_path> [object_name]")
        sys.exit(1)

    file_path = sys.argv[1]
    object_name = sys.argv[2] if len(sys.argv) > 2 else None
    bucket_name = os.environ.get("R2_BUCKET_NAME", "jw8-videos")

    if not os.path.exists(file_path):
        print(f"ERROR: File not found: {file_path}")
        sys.exit(1)

    upload_to_r2(file_path, bucket_name, object_name)
