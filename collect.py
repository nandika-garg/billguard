import boto3, json, datetime

REGION = "us-east-1"
BUCKET = "billguard-nandika-0110"
INTERVAL_HOURS = 0.25   # matches the 15-minute cron schedule

# Approximate us-east-1 on-demand Linux prices in USD.
# Check the AWS pricing pages before quoting exact numbers.
INSTANCE_PRICE = {
    "t3.nano": 0.0052, "t3.micro": 0.0104, "t3.small": 0.0208,
    "t3.medium": 0.0416, "t2.micro": 0.0116, "t2.small": 0.023,
    "t2.medium": 0.0464,
}
DEFAULT_INSTANCE_PRICE = 0.05
EBS_GB_MONTH = {"gp3": 0.08, "gp2": 0.10}
DEFAULT_EBS = 0.08
IPV4_HOUR = 0.005
HOURS_PER_MONTH = 730

ec2 = boto3.client("ec2", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)


def running_instances():
    res = ec2.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}])
    return [{"id": i["InstanceId"], "type": i["InstanceType"],
             "launched": i["LaunchTime"].isoformat(),
             "has_public_ip": bool(i.get("PublicIpAddress"))}
            for r in res["Reservations"] for i in r["Instances"]]


def all_volumes():
    res = ec2.describe_volumes()
    return [{"id": v["VolumeId"], "size_gb": v["Size"],
             "type": v["VolumeType"], "state": v["State"]}
            for v in res["Volumes"]]


def unused_elastic_ips():
    res = ec2.describe_addresses()
    return [a["PublicIp"] for a in res["Addresses"]
            if "AssociationId" not in a]


def bucket_usage():
    total, count = 0, 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix="snapshots/"):
        for obj in page.get("Contents", []):
            total += obj["Size"]
            count += 1
    return {"bytes": total, "objects": count}


def estimate(instances, volumes, unused_ips):
    inst = sum(INSTANCE_PRICE.get(i["type"], DEFAULT_INSTANCE_PRICE)
               for i in instances)
    rate = lambda v: v["size_gb"] * EBS_GB_MONTH.get(v["type"], DEFAULT_EBS)
    ebs_month = sum(rate(v) for v in volumes)
    ipv4 = IPV4_HOUR * (sum(1 for i in instances if i["has_public_ip"])
                        + len(unused_ips))
    hourly = inst + ebs_month / HOURS_PER_MONTH + ipv4
    waste_month = (sum(rate(v) for v in volumes if v["state"] == "available")
                   + len(unused_ips) * IPV4_HOUR * HOURS_PER_MONTH)
    return {"hourly_usd": round(hourly, 4),
            "waste_monthly_usd": round(waste_month, 2)}


def update_month_total(now, hourly):
    key = "cost/" + now.strftime("%Y-%m") + ".json"
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        total = json.loads(body)["total_usd"]
    except s3.exceptions.NoSuchKey:
        total = 0.0
    total += hourly * INTERVAL_HOURS
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(
        {"total_usd": round(total, 4), "updated": now.isoformat()}))


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    instances = running_instances()
    volumes = all_volumes()
    unused_ips = unused_elastic_ips()
    est = estimate(instances, volumes, unused_ips)
    snapshot = {
        "timestamp": now.isoformat(),
        "instances": instances,
        "unattached_volumes": [{"id": v["id"], "size_gb": v["size_gb"]}
                               for v in volumes if v["state"] == "available"],
        "unused_elastic_ips": unused_ips,
        "s3": bucket_usage(),
        "est": est,
    }
    key = "snapshots/" + now.strftime("%Y-%m-%dT%H-%M-%S") + ".json"
    s3.put_object(Bucket=BUCKET, Key=key,
                  Body=json.dumps(snapshot, indent=2))
    update_month_total(now, est["hourly_usd"])
    print("Saved", key)


if __name__ == "__main__":
    main()