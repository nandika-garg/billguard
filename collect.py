import boto3, json, datetime

REGION = "us-east-1"
BUCKET = "billguard-nandika-0110"

ec2 = boto3.client("ec2", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)


def running_instances():
    res = ec2.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}])
    return [{"id": i["InstanceId"], "type": i["InstanceType"],
             "launched": i["LaunchTime"].isoformat()}
            for r in res["Reservations"] for i in r["Instances"]]


def unattached_volumes():
    res = ec2.describe_volumes(
        Filters=[{"Name": "status", "Values": ["available"]}])
    return [{"id": v["VolumeId"], "size_gb": v["Size"]} for v in res["Volumes"]]


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


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    snapshot = {
        "timestamp": now.isoformat(),
        "instances": running_instances(),
        "unattached_volumes": unattached_volumes(),
        "unused_elastic_ips": unused_elastic_ips(),
        "s3": bucket_usage(),
    }
    key = "snapshots/" + now.strftime("%Y-%m-%dT%H-%M-%S") + ".json"
    s3.put_object(Bucket=BUCKET, Key=key,
                  Body=json.dumps(snapshot, indent=2))
    print("Saved", key)


if __name__ == "__main__":
    main()
