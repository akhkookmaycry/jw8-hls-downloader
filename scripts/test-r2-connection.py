#!/usr/bin/env python3
"""Test R2 connection and list buckets"""

import os
import sys
import boto3
from botocore.config import Config


def test_r2_connection():
    """Test R2 connection and list buckets"""

    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")

    if not all([account_id, access_key, secret_key]):
        print("ERROR: Missing R2 credentials")
        print("Please set: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY")
        sys.exit(1)

    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

    # Create S3 client for R2
    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )

    print(f"Testing R2 connection...")
    print(f"Account ID: {account_id}")
    print(f"Endpoint: {endpoint}")
    print()

    try:
        # List all buckets
        response = s3_client.list_buckets()

        print("✅ Connected to R2 successfully!")
        print()
        print("Buckets:")

        buckets = response.get("Buckets", [])
        if not buckets:
            print("  (No buckets found)")
        else:
            for bucket in buckets:
                print(f"  - {bucket['Name']} (created: {bucket['CreationDate']})")

        return buckets

    except Exception as e:
        print(f"❌ Failed to connect to R2: {e}")
        print()
        print("Possible issues:")
        print("  1. Invalid credentials")
        print("  2. Account ID is incorrect")
        print("  3. API token doesn't have permissions")
        sys.exit(1)


if __name__ == "__main__":
    test_r2_connection()
