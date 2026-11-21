"""pricing-service: a tiny HTTP service the order API calls synchronously.

    GET /price?item=book[&slow=1]  ->  {"item": "book", "price": 12.5}

It continues the caller's trace from the W3C `traceparent` header, so its SERVER
span sits under order-api's CLIENT span and the two show up as connected services.
`slow=1` is the lab's latency fault: the delay happens here, not in AWS.
"""

import json
import logging
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind

import otel_setup

otel_setup.setup("pricing-service")
log = logging.getLogger("pricing")
tracer = trace.get_tracer("pricing")
PRICES = {"book": 12.5, "mug": 8.0, "lamp": 39.9}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        with tracer.start_as_current_span(
            f"GET {url.path}",
            # HTTP header names are case-insensitive (urllib sends "Traceparent"); the
            # default dict getter is not, so normalise before extracting.
            context=propagate.extract({k.lower(): v for k, v in self.headers.items()}),
            kind=SpanKind.SERVER,
            attributes={"http.request.method": "GET", "url.path": url.path, "http.route": url.path},
        ) as span:
            item = params.get("item", "")
            if url.path != "/price" or item not in PRICES:
                span.set_attribute("http.response.status_code", 404)
                span.set_status(trace.Status(trace.StatusCode.ERROR, "unknown item"))
                log.error("unknown item %r", item)
                return self._send(404, {"error": "unknown item"})
            if params.get("slow") == "1":
                delay = random.uniform(0.8, 1.5)
                span.set_attribute("app.injected_delay_s", round(delay, 2))
                log.warning("price lookup slow for %s, took %.2fs", item, delay)
                time.sleep(delay)
            span.set_attribute("http.response.status_code", 200)
            log.info("priced %s at %.2f", item, PRICES[item])
            self._send(200, {"item": item, "price": PRICES[item]})

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # silence the default access log
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8081), Handler)
    log.info("pricing-service listening on http://127.0.0.1:8081")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
