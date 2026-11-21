"""Shared bits of the order lab: resource names, emulator guard, trace-context carriers."""

import boto3
from botocore.exceptions import ClientError
from opentelemetry import propagate
from opentelemetry.instrumentation.utils import suppress_instrumentation

EMULATOR_ACCOUNT = "000000000000"
BUCKET = "lab-receipts"
TABLE = "lab-orders"
QUEUE = "lab-shipping"
PRICING_URL = "http://127.0.0.1:8081"


def ensure_emulator() -> None:
    """Refuse to run unless requests reach the emulator, never real AWS.

    A safety check, not part of any business flow: keep it out of the traces, or every
    process start leaves an orphan "sts" node in the service map.
    """
    with suppress_instrumentation():
        account = boto3.client("sts").get_caller_identity()["Account"]
    if account != EMULATOR_ACCOUNT:
        raise SystemExit(f"Refusing to run: account {account} is not the emulator.")


def ensure_resources() -> str:
    """Create the lab's bucket, table and queue if missing; return the queue URL.

    Every service calls this, so start order does not matter. The emulator keeps
    state in memory only, so a restarted emulator starts empty again.
    """
    try:
        boto3.client("s3").create_bucket(Bucket=BUCKET)
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
    ddb = boto3.client("dynamodb")
    try:
        ddb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "order_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "order_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceInUseException":
            raise
    ddb.get_waiter("table_exists").wait(TableName=TABLE)
    return boto3.client("sqs").create_queue(QueueName=QUEUE)["QueueUrl"]


def prune_bucket(keep: int = 500) -> int:
    """Delete all but the newest `keep` objects in the lab bucket; return how many went.

    AWS Emulator keeps at most 1,000 objects per bucket (PutObject then fails with a
    ValidationException), and every order writes a receipt and a label. Housekeeping,
    not business flow, so it stays out of the traces.
    """
    s3 = boto3.client("s3")
    with suppress_instrumentation():
        objs = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
            objs += page.get("Contents", [])
        old = sorted(objs, key=lambda o: o["LastModified"])[:-keep] if len(objs) > keep else []
        for i in range(0, len(old), 500):
            s3.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": o["Key"]} for o in old[i:i + 500]],
                                                     "Quiet": True})
    return len(old)


def context_to_message_attributes() -> dict:
    """Current trace context (W3C traceparent) as SQS MessageAttributes."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return {k: {"DataType": "String", "StringValue": v} for k, v in carrier.items()}


def context_from_message(message: dict):
    """Trace context carried in an SQS message's attributes (or an empty context)."""
    attrs = message.get("MessageAttributes") or {}
    return propagate.extract({k: v["StringValue"] for k, v in attrs.items() if "StringValue" in v})
