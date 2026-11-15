import argparse
import json
import os
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import boto3


def wait_for(label, check, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(3)
    raise SystemExit(f"FAIL: {label}")


def request(base, path, payload=None):
    if payload is None:
        req = Request(base + path)
    else:
        req = Request(
            base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
    try:
        with urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return 404, {}
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stack")
    parser.add_argument("--dlq", action="store_true")
    args = parser.parse_args()
    session = boto3.Session(region_name=os.getenv("AWS_DEFAULT_REGION", "eu-north-1"))
    stacks = session.client("cloudformation").describe_stacks(StackName=args.stack)
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stacks["Stacks"][0]["Outputs"]}
    url = outputs["ServiceUrl"]
    logs = session.client("logs")
    elb = session.client("elbv2")
    status, body = request(url, "/health")
    targets = elb.describe_target_health(TargetGroupArn=outputs["TargetGroupArn"])
    healthy = [t["Target"]["Id"] for t in targets["TargetHealthDescriptions"] if t["TargetHealth"]["State"] == "healthy"]
    if (status, body) != (200, {"status": "ok"}) or len(healthy) != 2:
        raise SystemExit(f"FAIL: expected HTTP 200 and two healthy targets, got {status}, {healthy}")
    print("PASS /health and two healthy Fargate targets:", ", ".join(healthy))

    order_id = "demo-" + uuid.uuid4().hex[:12]
    payload = {"orderId": order_id}
    status, _ = request(url, "/orders", payload)
    if status != 202:
        raise SystemExit(f"FAIL: expected HTTP 202, got {status}")

    def get_order():
        status, item = request(url, f"/orders/{order_id}")
        return item if status == 200 and item.get("status") == "CREATED" else None

    original = wait_for("order never reached DynamoDB", get_order, 120)
    print("PASS order persisted:", original)
    start_ms = int(time.time() * 1000)
    status, _ = request(url, "/orders", payload)
    if status != 202:
        raise SystemExit(f"FAIL: duplicate publication returned {status}")

    def duplicate_log():
        for page in logs.get_paginator("filter_log_events").paginate(
            logGroupName=outputs["ConsumerLogGroup"],
            startTime=start_ms,
            filterPattern=f'"DUPLICATE {order_id}"',
        ):
            for event in page["events"]:
                words = event["message"].split()
                if len(words) == 3 and words[:2] == ["DUPLICATE", order_id]:
                    if words[2] != original["firstMessageId"]:
                        return event["message"].strip()
        return None

    duplicate = wait_for("no duplicate log with a different SQS message ID", duplicate_log, 180)
    if get_order() != original:
        raise SystemExit("FAIL: stored order changed")
    print("PASS duplicate skipped:", duplicate)

    if args.dlq:
        sns = session.client("sns")
        sqs = session.client("sqs")
        sns.publish(TopicArn=outputs["TopicArn"], Message=json.dumps({"type": "OrderCreated", "testId": order_id}))
        print("Waiting for DLQ after five receives...", flush=True)

        def find_message():
            messages = sqs.receive_message(
                QueueUrl=outputs["DlqUrl"], MaxNumberOfMessages=10,
                WaitTimeSeconds=20, VisibilityTimeout=0,
            ).get("Messages", [])
            for message in messages:
                body = json.loads(message["Body"])
                if body.get("testId") == order_id:
                    return body
            return None

        print("PASS invalid event in DLQ:", wait_for("invalid event did not reach DLQ", find_message, 600))


if __name__ == "__main__":
    main()
