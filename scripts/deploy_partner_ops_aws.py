#!/usr/bin/env python3
"""Build and deploy the dedicated Partner Ops AWS service in disabled mode.

This script never configures Slack endpoints and never enables capture,
briefing, review posting, external writes, or message sending.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


REGION = "us-east-2"
STACK = "partner-ops-foundation"
SERVICE = "partner-ops-agent"
CLUSTER = "default"
INFRA_ROLE = "arn:aws:iam::626963115342:role/service-role/ecsInfrastructureRoleForExpressServices"
PARTNER_OPS_APP_ID = "A0CA2C43D4L"
RUNPOD_TEAM_ID = "T06F03V8NUQ"
CAPTURE_TEST_CHANNEL_ID = "C0C7HG8KYFL"
CAPTURE_TEST_USER_ID = "U0BEHPEJA6L"


def command(*args, input_text=None):
    result = subprocess.run(args, input=input_text, text=True, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.strip()


def aws(*args):
    return json.loads(command("aws", *args, "--region", REGION, "--output", "json") or "{}")


def stack_outputs():
    stacks = aws("cloudformation", "describe-stacks", "--stack-name", STACK).get("Stacks", [])
    if len(stacks) != 1 or stacks[0].get("StackStatus") not in (
            "CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise RuntimeError("Partner Ops foundation stack is not ready.")
    return {item["OutputKey"]: item["OutputValue"] for item in stacks[0].get("Outputs", [])}


def clean_commit():
    if command("git", "status", "--porcelain"):
        raise RuntimeError("Working tree must be clean before an AWS image is built.")
    return command("git", "rev-parse", "--short=12", "HEAD")


def build_and_push(repository, tag):
    registry = repository.split("/", 1)[0]
    password = command("aws", "ecr", "get-login-password", "--region", REGION)
    command("docker", "login", "--username", "AWS", "--password-stdin", registry,
            input_text=password)
    image = repository + ":" + tag
    command("docker", "build", "--platform", "linux/amd64", "-t", image, ".")
    command("docker", "push", image)
    details = aws("ecr", "describe-images", "--repository-name", repository.rsplit("/", 1)[-1],
                  "--image-ids", "imageTag=" + tag)
    digest = details["imageDetails"][0]["imageDigest"]
    return repository + "@" + digest


def task_definition(image, outputs, subnets, capture_test=False):
    disabled = [
        "P_OPS_DAILY_REVIEW_ENABLED", "P_OPS_TRIAGE_SHADOW_ENABLED",
        "P_OPS_REVIEW_POSTING_ENABLED",
        "P_OPS_REVIEW_INTERACTIVE_ENABLED", "P_OPS_NOTION_CREATE",
        "P_OPS_NOTION_WRITE", "P_OPS_LINEAR_WRITE", "P_OPS_SEND_MESSAGES",
        "P_OPS_WORKING_TASK_BRIEF_ENABLED",
    ]
    environment = [
        {"name": "P_OPS_ENABLED", "value": "true"},
        {"name": "P_OPS_DB", "value": "/data/p_ops.sqlite3"},
        {"name": "P_OPS_REVIEW_USER", "value": "colby"},
        {"name": "SLACK_APP_ID", "value": PARTNER_OPS_APP_ID},
        {"name": "SLACK_TEAM_ID", "value": RUNPOD_TEAM_ID},
        {"name": "PORT", "value": "8080"},
        {"name": "P_OPS_CAPTURE_ENABLED", "value": "true" if capture_test else "false"},
    ] + [{"name": name, "value": "false"} for name in disabled]
    if capture_test:
        environment.extend([
            {"name": "P_OPS_CAPTURE_CHANNEL_IDS", "value": CAPTURE_TEST_CHANNEL_ID},
            {"name": "P_OPS_CAPTURE_USER_IDS", "value": CAPTURE_TEST_USER_ID},
        ])
    definition = {
        "family": "partner-ops-agent",
        "executionRoleArn": outputs["ExecutionRoleArn"],
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "512",
        "memory": "1024",
        "runtimePlatform": {"cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX"},
        "volumes": [{
            "name": "partner-ops-data",
            "efsVolumeConfiguration": {
                "fileSystemId": outputs["FileSystemId"],
                "transitEncryption": "ENABLED",
                "authorizationConfig": {
                    "accessPointId": outputs["AccessPointId"],
                    "iam": "DISABLED",
                },
            },
        }],
        "containerDefinitions": [{
            "name": "Main",
            "image": image,
            "essential": True,
            "command": ["python", "p_ops_service.py"],
            "portMappings": [{
                "containerPort": 8080, "hostPort": 8080, "protocol": "tcp",
                "name": "main-8080-tcp",
            }],
            "environment": environment,
            "secrets": [
                {"name": "SLACK_BOT_TOKEN", "valueFrom": outputs["BotTokenSecretArn"]},
                {"name": "SLACK_SIGNING_SECRET", "valueFrom": outputs["SigningSecretArn"]},
                {"name": "P_OPS_REVIEW_PASSWORD", "valueFrom": outputs["ReviewPasswordSecretArn"]},
                {"name": "P_OPS_CSRF_SECRET", "valueFrom": outputs["CsrfSecretArn"]},
            ],
            "mountPoints": [{
                "sourceVolume": "partner-ops-data", "containerPath": "/data", "readOnly": False,
            }],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": outputs["LogGroupName"],
                    "awslogs-region": REGION,
                    "awslogs-stream-prefix": "ecs",
                },
            },
        }],
        "tags": [{"key": "app", "value": "partner-ops"}],
    }
    if len(subnets) < 2:
        raise ValueError("At least two subnets are required.")
    return definition


def register(definition):
    result = aws("ecs", "register-task-definition", "--cli-input-json", json.dumps(definition))
    return result["taskDefinition"]["taskDefinitionArn"]


def existing_service():
    try:
        result = aws("ecs", "describe-express-gateway-service", "--service-arn",
                     "arn:aws:ecs:%s:626963115342:service/%s/%s" % (REGION, CLUSTER, SERVICE))
    except subprocess.CalledProcessError:
        return None
    return result.get("service")


def deploy(task_arn, outputs, subnets):
    network = json.dumps({
        "securityGroups": [outputs["TaskSecurityGroupId"]],
        "subnets": subnets,
    })
    service = existing_service()
    if service:
        result = aws("ecs", "update-express-gateway-service",
                     "--service-arn", service["serviceArn"],
                     "--task-definition-arn", task_arn,
                     "--network-configuration", network,
                     "--health-check-path", "/healthz")
    else:
        result = aws("ecs", "create-express-gateway-service",
                     "--service-name", SERVICE, "--cluster", CLUSTER,
                     "--infrastructure-role-arn", INFRA_ROLE,
                     "--task-definition-arn", task_arn,
                     "--network-configuration", network,
                     "--health-check-path", "/healthz",
                     "--scaling-target", json.dumps({
                         "minTaskCount": 1, "maxTaskCount": 1,
                         "autoScalingMetric": "AVERAGE_CPU", "autoScalingTargetValue": 60,
                     }))
    return result["service"]["serviceArn"]


def wait_for_endpoint(service_arn, capture_test=False):
    deadline = time.time() + 900
    while time.time() < deadline:
        service = aws("ecs", "describe-express-gateway-service", "--service-arn", service_arn)["service"]
        for revision in service.get("activeConfigurations", []):
            for ingress in revision.get("ingressPaths", []):
                if ingress.get("accessType") == "PUBLIC" and ingress.get("endpoint"):
                    endpoint = ingress["endpoint"].rstrip("/")
                    try:
                        with urllib.request.urlopen(endpoint + "/healthz", timeout=15) as response:
                            health = json.load(response)
                        if (health.get("status") == "listening" and
                                health.get("slack_app_id") == PARTNER_OPS_APP_ID and
                                health.get("working_task_brief_enabled") is False and
                                health.get("capture_enabled") is capture_test):
                            return endpoint, health
                    except (OSError, ValueError):
                        pass
        time.sleep(15)
    raise TimeoutError("Partner Ops did not reach the guarded health state within 15 minutes.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subnets", required=True,
                        help="Comma-separated subnet IDs used by the foundation stack.")
    parser.add_argument("--deploy-disabled", action="store_true",
                        help="Required acknowledgement that this deploy keeps every action gate off.")
    parser.add_argument("--enable-capture-test", action="store_true",
                        help="Enable capture only for Colby in #ask-partners; all downstream gates stay off.")
    args = parser.parse_args()
    if not args.deploy_disabled:
        raise SystemExit("Pass --deploy-disabled. This script cannot activate Partner Ops processing.")
    subnets = [value.strip() for value in args.subnets.split(",") if value.strip()]
    outputs = stack_outputs()
    tag = clean_commit()
    image = build_and_push(outputs["RepositoryUri"], tag)
    task_arn = register(task_definition(image, outputs, subnets, capture_test=args.enable_capture_test))
    service_arn = deploy(task_arn, outputs, subnets)
    endpoint, health = wait_for_endpoint(service_arn, capture_test=args.enable_capture_test)
    print(json.dumps({
        "service": service_arn,
        "task_definition": task_arn,
        "image": image,
        "endpoint": endpoint,
        "health": health,
        "activation": "capture-test" if args.enable_capture_test else "disabled",
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, TimeoutError, subprocess.CalledProcessError) as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        raise SystemExit(1)
