#!/usr/bin/env python3

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


LABEL = "experiment=edge-scheduling"
DEFAULT_NAMESPACE = "edge-exp"


def run(cmd, timeout=120):
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def run_checked(cmd, timeout=120):
    result = run(cmd, timeout=timeout)
    if result.returncode != 0:
        print("ERROR: command failed", file=sys.stderr)
        print("$", " ".join(cmd), file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    return result


def check_kubectl():
    if not shutil.which("kubectl"):
        print("ERROR: kubectl not found", file=sys.stderr)
        sys.exit(1)


def refresh_raw_json(raw_dir, namespace):
    check_kubectl()
    raw_dir.mkdir(parents=True, exist_ok=True)

    commands = [
        (["kubectl", "get", "pods", "-n", namespace, "-l", LABEL, "-o", "json"], raw_dir / "pods.json"),
        (["kubectl", "get", "nodes", "-o", "json"], raw_dir / "nodes.json"),
    ]

    for cmd, output in commands:
        result = run_checked(cmd, timeout=180)
        json.loads(result.stdout)
        output.write_text(result.stdout + "\n", encoding="utf-8")


def load_json(path):
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def cpu_to_cores(value):
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    if text.endswith("m"):
        return float(text[:-1]) / 1000.0
    return float(text)


def memory_to_mib(value):
    if value is None:
        return 0.0

    text = str(value).strip()
    if not text:
        return 0.0

    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([a-zA-Z]*)", text)
    if not match:
        print(f"ERROR: unsupported memory value: {value}", file=sys.stderr)
        sys.exit(1)

    number = float(match.group(1))
    suffix = match.group(2)

    units = {
        "": 1 / 1024 / 1024,
        "Ki": 1 / 1024,
        "Mi": 1,
        "Gi": 1024,
        "Ti": 1024 * 1024,
        "K": 1000 / 1024 / 1024,
        "M": 1000 * 1000 / 1024 / 1024,
        "G": 1000 * 1000 * 1000 / 1024 / 1024,
        "T": 1000 * 1000 * 1000 * 1000 / 1024 / 1024,
    }

    if suffix not in units:
        print(f"ERROR: unsupported memory suffix: {value}", file=sys.stderr)
        sys.exit(1)

    return number * units[suffix]


def fmt(value, digits=6):
    value = round(float(value), digits)
    if value == int(value):
        return str(int(value))
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def labels(obj):
    return obj.get("metadata", {}).get("labels", {}) or {}


def name(obj):
    return obj.get("metadata", {}).get("name", "")


def node_info(nodes_json):
    node_classes = {}
    node_capacity = {}

    for node in nodes_json.get("items", []):
        node_name = name(node)
        node_labels = labels(node)
        status = node.get("status", {})
        allocatable = status.get("allocatable", {}) or status.get("capacity", {})
        capacity = status.get("capacity", {})

        cpu = allocatable.get("cpu", capacity.get("cpu", "0"))
        memory = allocatable.get("memory", capacity.get("memory", "0"))

        node_classes[node_name] = node_labels.get("node-class", "")
        node_capacity[node_name] = (cpu_to_cores(cpu), memory_to_mib(memory))

    return node_classes, node_capacity


def pod_requests(pod):
    cpu = 0.0
    memory = 0.0
    cpu_text = []
    memory_text = []

    for container in pod.get("spec", {}).get("containers", []):
        requests = container.get("resources", {}).get("requests", {})

        if "cpu" in requests:
            value = str(requests["cpu"])
            cpu_text.append(value)
            cpu += cpu_to_cores(value)

        if "memory" in requests:
            value = str(requests["memory"])
            memory_text.append(value)
            memory += memory_to_mib(value)

    return {
        "cpu": cpu,
        "memory": memory,
        "cpu_text": "+".join(cpu_text) if cpu_text else "0",
        "memory_text": "+".join(memory_text) if memory_text else "0Mi",
    }


def build_pod_rows(pods_json, node_classes):
    rows = []

    for pod in pods_json.get("items", []):
        pod_labels = labels(pod)
        spec = pod.get("spec", {})
        status = pod.get("status", {})
        requests = pod_requests(pod)
        node_name = spec.get("nodeName", "")

        rows.append(
            {
                "pod_name": name(pod),
                "profile": pod_labels.get("profile", ""),
                "schedulerName": spec.get("schedulerName", ""),
                "node_name": node_name,
                "node_class": node_classes.get(node_name, ""),
                "cpu_request": requests["cpu_text"],
                "memory_request": requests["memory_text"],
                "phase": status.get("phase", "Unknown"),
                "cpu_cores": requests["cpu"],
                "memory_mib": requests["memory"],
            }
        )

    rows.sort(key=lambda item: item["pod_name"])
    return rows


def write_pod_placement(path, pod_rows):
    fields = [
        "pod_name",
        "profile",
        "schedulerName",
        "node_name",
        "node_class",
        "cpu_request",
        "memory_request",
        "phase",
    ]

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in pod_rows:
            writer.writerow({field: row[field] for field in fields})


def write_node_allocations(path, pod_rows, node_classes, node_capacity):
    requested_cpu = {node: 0.0 for node in node_capacity}
    requested_memory = {node: 0.0 for node in node_capacity}
    pod_count = {node: 0 for node in node_capacity}

    for pod in pod_rows:
        node = pod["node_name"]
        if not node or node not in node_capacity:
            continue
        requested_cpu[node] += pod["cpu_cores"]
        requested_memory[node] += pod["memory_mib"]
        pod_count[node] += 1

    fields = [
        "node_name",
        "node_class",
        "cpu_capacity",
        "memory_capacity",
        "requested_cpu",
        "requested_memory",
        "cpu_util",
        "memory_util",
        "pod_count",
    ]

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()

        for node in sorted(node_capacity):
            cpu_cap, mem_cap = node_capacity[node]
            cpu_req = requested_cpu[node]
            mem_req = requested_memory[node]

            writer.writerow(
                {
                    "node_name": node,
                    "node_class": node_classes.get(node, ""),
                    "cpu_capacity": fmt(cpu_cap),
                    "memory_capacity": fmt(mem_cap),
                    "requested_cpu": fmt(cpu_req),
                    "requested_memory": fmt(mem_req),
                    "cpu_util": fmt(cpu_req / cpu_cap if cpu_cap else 0),
                    "memory_util": fmt(mem_req / mem_cap if mem_cap else 0),
                    "pod_count": pod_count[node],
                }
            )


def print_summary(pod_rows, node_capacity):
    total = len(pod_rows)
    scheduled = sum(1 for pod in pod_rows if pod["node_name"])
    pending = sum(1 for pod in pod_rows if pod["phase"] == "Pending")
    running = sum(1 for pod in pod_rows if pod["phase"] == "Running")
    active_nodes = len({pod["node_name"] for pod in pod_rows if pod["node_name"]})

    by_profile = {}
    by_class = {}

    for pod in pod_rows:
        by_profile[pod["profile"]] = by_profile.get(pod["profile"], 0) + 1
        node_class = pod["node_class"] or "<unscheduled>"
        by_class[node_class] = by_class.get(node_class, 0) + 1

    print("Collection summary:")
    print(f"- pods total: {total}")
    print(f"- pods scheduled: {scheduled}")
    print(f"- pods pending: {pending}")
    print(f"- pods running: {running}")
    print(f"- active nodes: {active_nodes}/{len(node_capacity)}")

    print("- pods by profile:")
    for key in sorted(by_profile):
        print(f"  {key}: {by_profile[key]}")

    print("- pods by node class:")
    for key in sorted(by_class):
        print(f"  {key}: {by_class[key]}")


def parse_args():
    parser = argparse.ArgumentParser(description="Collect experiment state")
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def main():
    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10+ is required", file=sys.stderr)
        return 1

    args = parse_args()
    raw_dir = args.raw_dir.expanduser().resolve()

    if args.refresh:
        refresh_raw_json(raw_dir, args.namespace)

    pods_path = raw_dir / "pods.json"
    nodes_path = raw_dir / "nodes.json"

    pods_json = load_json(pods_path)
    nodes_json = load_json(nodes_path)

    node_classes, node_capacity = node_info(nodes_json)
    pod_rows = build_pod_rows(pods_json, node_classes)

    pod_csv = raw_dir / "pod_placement.csv"
    node_csv = raw_dir / "node_allocations.csv"

    write_pod_placement(pod_csv, pod_rows)
    write_node_allocations(node_csv, pod_rows, node_classes, node_capacity)

    print(f"[OK] wrote {pod_csv}")
    print(f"[OK] wrote {node_csv}")
    print()
    print_summary(pod_rows, node_capacity)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())