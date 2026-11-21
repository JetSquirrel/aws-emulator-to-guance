"""order-api: places orders — calls pricing-service, then S3, DynamoDB and SQS.

    ./run.sh python order_api.py                    # one order, one trace
    ./run.sh python order_api.py --loop --error-rate 0.15 --slow-rate 0.1
    ./run.sh python order_api.py --fault bad-item   # force one fault

Per order: price it (HTTP → pricing-service) → save receipt (S3) → store order
(DynamoDB) → enqueue for shipping (SQS, trace context in message attributes).
shipping-worker picks it up and continues the same trace.

Faults for the observability lab:
  missing-object  read back a receipt that was never written  → S3 NoSuchKey
  bad-param       write the order without its key attribute    → DynamoDB ValidationException
  bad-item        ask pricing-service for an unknown item      → HTTP 404 from a downstream service
  slow            pricing-service takes ~1s                    → latency only, no error
"""

import argparse
import json
import logging
import random
import time
import urllib.error
import urllib.request
import uuid

import boto3
from botocore.exceptions import ClientError
from opentelemetry import metrics, propagate, trace
from opentelemetry.trace import SpanKind, Status, StatusCode

import lab_common
import otel_setup

otel_setup.setup("order-api")
log = logging.getLogger("orders")
tracer = trace.get_tracer("orders")
meter = metrics.get_meter("orders")
orders_total = meter.create_counter("orders_total", description="orders processed, by outcome and fault")
order_duration = meter.create_histogram("order_duration_ms", unit="ms", description="end-to-end order time")
FAULTS = ["missing-object", "bad-param", "bad-item"]


class OrderFailed(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class OrderApi:
    def __init__(self) -> None:
        with tracer.start_as_current_span("startup"):
            self.queue = lab_common.ensure_resources()
        self.s3, self.sqs = boto3.client("s3"), boto3.client("sqs")
        self.table = boto3.resource("dynamodb").Table(lab_common.TABLE)

    def price(self, order: dict, fault: str | None) -> None:
        item = "unicorn" if fault == "bad-item" else order["item"]
        url = f"{lab_common.PRICING_URL}/price?item={item}" + ("&slow=1" if fault == "slow" else "")
        with tracer.start_as_current_span(
            "GET /price", kind=SpanKind.CLIENT,
            attributes={"http.request.method": "GET", "url.full": url, "server.address": "pricing-service",
                        "peer.service": "pricing-service"},
        ) as span:
            headers: dict[str, str] = {}
            propagate.inject(headers)  # W3C traceparent → pricing-service continues this trace
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as resp:
                    span.set_attribute("http.response.status_code", resp.status)
                    order["total"] = str(json.load(resp)["price"])
            except urllib.error.HTTPError as e:
                span.set_attribute("http.response.status_code", e.code)
                span.set_status(Status(StatusCode.ERROR, f"HTTP {e.code}"))
                raise OrderFailed(f"Pricing{e.code}") from e

    @tracer.start_as_current_span("save-receipt")
    def save_receipt(self, order: dict, fault: str | None) -> None:
        key = f"receipts/{order['order_id']}.json"
        self.s3.put_object(Bucket=lab_common.BUCKET, Key=key, Body=json.dumps(order))
        read_key = f"receipts/missing-{order['order_id']}.json" if fault == "missing-object" else key
        self.s3.get_object(Bucket=lab_common.BUCKET, Key=read_key)

    @tracer.start_as_current_span("store-order")
    def store_order(self, order: dict, fault: str | None) -> None:
        item = {**order, "status": "PLACED"}
        if fault == "bad-param":
            item.pop("order_id")
        self.table.put_item(Item=item)

    @tracer.start_as_current_span("enqueue-shipping")
    def enqueue(self, order: dict) -> None:
        self.sqs.send_message(QueueUrl=self.queue, MessageBody=json.dumps(order),
                              MessageAttributes=lab_common.context_to_message_attributes())

    def place_order(self, fault: str | None) -> str:
        order = {"order_id": uuid.uuid4().hex[:12], "item": random.choice(["book", "mug", "lamp"])}
        start = time.perf_counter()
        # A fresh root span per order: background traffic must not share one trace.
        with tracer.start_as_current_span("POST /orders", context=trace.set_span_in_context(trace.INVALID_SPAN),
                                          kind=SpanKind.SERVER) as span:
            span.set_attributes({"order.id": order["order_id"], "app.fault": fault or "none",
                                 "http.request.method": "POST", "http.route": "/orders"})
            trace_id = format(span.get_span_context().trace_id, "032x")
            outcome = "ok"
            try:
                self.price(order, fault)
                self.save_receipt(order, fault)
                self.store_order(order, fault)
                self.enqueue(order)
                log.info("order %s placed, total=%s", order["order_id"], order["total"])
            except (ClientError, OrderFailed) as e:
                outcome = "error"
                code = e.code if isinstance(e, OrderFailed) else e.response["Error"]["Code"]
                span.set_status(Status(StatusCode.ERROR, code))
                span.set_attribute("error.type", code)
                log.error("order %s failed: %s", order["order_id"], code)
            finally:
                attrs = {"outcome": outcome, "fault": fault or "none"}
                orders_total.add(1, attrs)
                order_duration.record((time.perf_counter() - start) * 1000, attrs)
        return f"{order['order_id']} {outcome:5} fault={fault or '-':14} trace_id={trace_id}"


def pick_fault(error_rate: float, slow_rate: float) -> str | None:
    r = random.random()
    if r < error_rate:
        return random.choice(FAULTS)
    if r < error_rate + slow_rate:
        return "slow"
    return None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--loop", action="store_true", help="keep placing orders until Ctrl-C")
    p.add_argument("--interval", type=float, default=1.0, help="seconds between orders (default 1)")
    p.add_argument("--error-rate", type=float, default=0.0, help="share of orders with an injected error")
    p.add_argument("--slow-rate", type=float, default=0.0, help="share of orders with a slow pricing call")
    p.add_argument("--fault", choices=FAULTS + ["slow"], help="force one fault on every order")
    args = p.parse_args()

    lab_common.ensure_emulator()
    api = OrderApi()
    lab_common.prune_bucket()
    placed = 0
    try:
        while True:
            print(api.place_order(args.fault or pick_fault(args.error_rate, args.slow_rate)), flush=True)
            placed += 1
            if placed % 100 == 0:  # emulator caps a bucket at 1,000 objects; 2 objects per order
                log.info("housekeeping: pruned %d old objects from %s", lab_common.prune_bucket(), lab_common.BUCKET)
            if not args.loop:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
