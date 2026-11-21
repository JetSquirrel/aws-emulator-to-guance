"""Tests that run against AWS Emulator. Start with ./run.sh pytest"""

import uuid

import boto3
import pytest


@pytest.fixture(scope="session", autouse=True)
def only_emulator():
    account = boto3.client("sts").get_caller_identity()["Account"]
    if account != "000000000000":
        pytest.exit(f"account {account} is not the emulator; aborting", returncode=2)


@pytest.fixture
def name():
    return f"t-{uuid.uuid4().hex[:8]}"


def test_s3_roundtrip(name):
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket=name)
    s3.put_object(Bucket=name, Key="k", Body=b"v")
    assert s3.get_object(Bucket=name, Key="k")["Body"].read() == b"v"
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=name)["Contents"]]
    assert keys == ["k"]


def test_dynamodb_put_get(name):
    table = boto3.resource("dynamodb").create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    table.put_item(Item={"id": "1", "n": 7})
    assert table.get_item(Key={"id": "1"})["Item"]["n"] == 7


def test_sqs_send_receive(name):
    sqs = boto3.client("sqs")
    url = sqs.create_queue(QueueName=name)["QueueUrl"]
    sqs.send_message(QueueUrl=url, MessageBody="hello")
    msgs = sqs.receive_message(QueueUrl=url, WaitTimeSeconds=1)["Messages"]
    assert [m["Body"] for m in msgs] == ["hello"]
