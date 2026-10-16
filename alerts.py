import boto3, json, sys

REGION = "us-east-1"
BUCKET = "billguard-nandika-0110"
TOPIC_ARN = "arn:aws:sns:us-east-1:YOUR_ACCOUNT_ID:billguard-alerts"   # paste your SNS topic ARN between the quotes

CONFIG = {
    "max_running_instances": 2,
    "max_s3_gb": 5,
    "alert_at_percent": 80,
}

s3 = boto3.client("s3", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)


def latest_snapshot():
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix="snapshots/"):
        keys += [o["Key"] for o in page.get("Contents", [])]
    key = sorted(keys)[-1]
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    return key, json.loads(body)


def check(snap):
    problems = []
    pct = CONFIG["alert_at_percent"]

    n = len(snap["instances"])
    limit = CONFIG["max_running_instances"]
    if n * 100 >= limit * pct:
        problems.append(f"Running instances: {n} of {limit} allowed")

    gb = snap["s3"]["bytes"] / 1024 ** 3
    max_gb = CONFIG["max_s3_gb"]
    if gb * 100 >= max_gb * pct:
        problems.append(f"S3 snapshot storage: {gb:.2f} GB of {max_gb} GB")

    for v in snap["unattached_volumes"]:
        problems.append(f"Unattached EBS volume {v['id']} ({v['size_gb']} GB)")
    for ip in snap["unused_elastic_ips"]:
        problems.append(f"Unused Elastic IP {ip}")
    return problems


def last_message():
    try:
        obj = s3.get_object(Bucket=BUCKET, Key="alerts/last.txt")
        return obj["Body"].read().decode()
    except Exception:
        return ""


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        problems = ["TEST ALERT: BillGuard email is working"]
        key = "test"
    else:
        key, snap = latest_snapshot()
        problems = check(snap)

    if not problems:
        print("No problems found in", key)
        s3.put_object(Bucket=BUCKET, Key="alerts/last.txt", Body=b"")
        return

    message = "BillGuard found:\n- " + "\n- ".join(problems)
    if message == last_message() and key != "test":
        print("Same alert as last time, not sending again")
        return

    s3.put_object(Bucket=BUCKET, Key="alerts/last.txt", Body=message.encode())
    s3.put_object(Bucket=BUCKET, Key="alerts/latest.json",
                  Body=json.dumps({"snapshot": key, "problems": problems}))
    print(message)

    if TOPIC_ARN:
        sns.publish(TopicArn=TOPIC_ARN, Subject="BillGuard alert",
                    Message=message)
        print("Email sent via SNS")
    else:
        print("TOPIC_ARN is empty, alert saved to S3 only")


if __name__ == "__main__":
    main()
