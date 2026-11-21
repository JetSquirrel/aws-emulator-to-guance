"""OpenTelemetry wiring: traces, metrics and logs → DataKit (OTLP/HTTP) → Guance.

DataKit serves OTLP on its own port and paths, not the OTLP defaults:
    http://127.0.0.1:9529/otel/v1/{traces,metrics,logs}
so each signal gets its exporter endpoint explicitly.

Every boto3 call becomes a client span (botocore instrumentation), and every
`logging` record emitted inside a span carries that span's trace_id/span_id.
"""

import atexit
import logging
import os

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

DATAKIT = os.environ.get("DATAKIT_OTLP_BASE", "http://127.0.0.1:9529/otel/v1")


def _name_aws_dependency(span, service_name: str, operation_name: str, api_params: dict) -> None:
    """Give each AWS service its own node in Guance's service map.

    DataKit's split_service_name names a client span's dependency after db.system,
    then rpc.system, then messaging.system. botocore sets rpc.system="aws-api" for
    every AWS call, so S3, SQS and STS would collapse into one "aws-api" node
    (DynamoDB already splits out via db.system=dynamodb). Lab-only deviation from the
    semantic conventions: rpc.system carries the AWS service name instead.
    """
    if span.is_recording():
        span.set_attribute("rpc.system", service_name.lower())
        span.set_attribute("peer.service", service_name.lower())


def setup(service_name: str) -> None:
    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", service_name),
            "service.version": "0.1.0",
            "deployment.environment": "aws-emulator",
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{DATAKIT}/traces"))
    )
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{DATAKIT}/metrics"),
                export_interval_millis=10_000,
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{DATAKIT}/logs"))
    )
    set_logger_provider(logger_provider)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)
    # botocore/urllib3 debug chatter is noise for this lab
    for noisy in ("botocore", "urllib3", "boto3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # The SDK's own export errors go to the console only: feeding them back into the
    # OTLP log pipeline would loop when DataKit is unreachable.
    otel_internal = logging.getLogger("opentelemetry")
    otel_internal.propagate = False
    otel_internal.addHandler(console)

    BotocoreInstrumentor().instrument(request_hook=_name_aws_dependency)

    def _flush() -> None:
        # Short-lived scripts must flush, or the last batch never leaves the process.
        tracer_provider.shutdown()
        meter_provider.shutdown()
        logger_provider.shutdown()

    atexit.register(_flush)
