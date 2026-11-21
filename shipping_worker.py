"""shipping-worker: consumes orders from SQS, writes a shipping label, marks them SHIPPED.

The order API puts its trace context into each message's attributes; the worker
continues that trace, so one order's trace spans order-api → SQS → shipping-worker.
"""

import json
import logging
import time
import uuid

import boto3
from botocore.exceptions import ClientError
from opentelemetry import trace
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.trace import SpanKind

import lab_common
import otel_setup

otel_setup.setup("shipping-worker")
log = logging.getLogger("shipping")
tracer = trace.get_tracer("shipping")


def ship(s3, table, order: dict) -> None:
    tracking = f"TRK-{uuid.uuid4().hex[:10].upper()}"
    with tracer.start_as_current_span("write-label"):
        s3.put_object(Bucket=lab_common.BUCKET, Key=f"labels/{order['order_id']}.txt",
                      Body=f"{order['order_id']} {order['item']} {tracking}\n")
    with tracer.start_as_current_span("mark-shipped"):
        table.update_item(
            Key={"order_id": order["order_id"]},
            UpdateExpression="SET #s = :s, tracking = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "SHIPPED", ":t": tracking},
        )
    log.info("order %s shipped, tracking=%s", order["order_id"], tracking)


def main() -> None:
    lab_common.ensure_emulator()
    queue_url = lab_common.ensure_resources()
    sqs, s3 = boto3.client("sqs"), boto3.client("s3")
    table = boto3.resource("dynamodb").Table(lab_common.TABLE)
    log.info("shipping-worker polling %s", queue_url)
    while True:
        # Polling itself is not part of any order's trace; suppress its spans.
        with suppress_instrumentation():
            msgs = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=5,
                                       WaitTimeSeconds=1, MessageAttributeNames=["All"]).get("Messages", [])
        for msg in msgs:
            order = json.loads(msg["Body"])
            with tracer.start_as_current_span(
                f"{lab_common.QUEUE} process",
                context=lab_common.context_from_message(msg),
                kind=SpanKind.CONSUMER,
                # messaging.system names this span's node in the service map; "sqs" matches the
                # producer side (see otel_setup._name_aws_dependency): order-api → sqs → shipping-worker.
                attributes={"messaging.system": "sqs", "messaging.destination.name": lab_common.QUEUE,
                            "messaging.operation.type": "process", "order.id": order["order_id"]},
            ) as span:
                try:
                    ship(s3, table, order)
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
                except ClientError as e:
                    code = e.response["Error"]["Code"]
                    span.set_status(trace.Status(trace.StatusCode.ERROR, code))
                    log.error("order %s shipping failed: %s", order["order_id"], code)
        if not msgs:
            time.sleep(0.2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
