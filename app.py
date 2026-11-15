import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import boto3

sns = boto3.client("sns")
db = boto3.resource("dynamodb").Table(os.environ["ORDERS_TABLE"])
topic = os.environ["ORDERS_TOPIC"]
order_pattern = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            return self.reply(200, {"status": "ok"})
        if path == "/":
            return self.reply(200, {"service": "CAD SIS 3"})
        order_id = path.removeprefix("/orders/")
        if path.startswith("/orders/") and order_pattern.fullmatch(order_id):
            item = db.get_item(Key={"orderId": order_id}, ConsistentRead=True).get("Item")
            return self.reply(200 if item else 404, item or {"error": "order not found"})
        return self.reply(404, {"error": "not found"})

    def do_POST(self):
        if urlsplit(self.path).path != "/orders":
            return self.reply(404, {"error": "not found"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 4096:
                raise ValueError()
            order_id = json.loads(self.rfile.read(size))["orderId"]
            if not isinstance(order_id, str) or not order_pattern.fullmatch(order_id):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            return self.reply(400, {"error": "provide a valid orderId"})
        result = sns.publish(
            TopicArn=topic,
            Message=json.dumps({"type": "OrderCreated", "orderId": order_id}),
        )
        return self.reply(202, {"orderId": order_id, "publicationId": result["MessageId"]})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
