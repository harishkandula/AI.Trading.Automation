import json

import boto3

class S3Store:
    def __init__(self, bucket: str) -> None:
        self.bucket = bucket
        self.client = boto3.client("s3")

    def put_json(self, key: str, payload: dict) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(payload).encode(), ContentType="application/json")
