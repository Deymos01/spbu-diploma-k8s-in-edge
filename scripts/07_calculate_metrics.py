#!/usr/bin/env python3

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path


PROCESSED_ROOT = Path("data/processed")


def read_csv(path):
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def to_float(value):
    if value == "" or value is None:
        return 0.0
    return float(value)


def to_int(value):
    if value == "" or value is None:
        return 0
    return int(float(value))


def mean(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def stddev(values):
    if len(values) <= 1:
        return 0.0
    return statistics.pstdev(values)


def ratio(a, b):
    if b == 0:
        return 0.0
    return a / b


def round_row(row, digits):
    result = {}
    for key, value in row.items():
        if isinstance(value, float):
            result[key] = round(value, digits)
        else:
            result[key] = value
    return result


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        path.write_text("\n", encoding="utf-8")
        return

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def experiment_name_from_dir(input_dir, explicit_name):
    if explicit_name:
        return explicit_name

    metadata_path = input_dir / "experiment_metadata.json"
    if metadata_path.exists():
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if data.get("experiment_name"):
                return data["experiment_name"]
        except json.JSONDecodeError:
            pass

    return input_dir.name


def prepare_nodes(rows):
    nodes = []

    for row in rows:
        cpu_util = to_float(row["cpu_util"])
        memory_util = to_float(row["memory_util"])

        nodes.append(
            {
                "node_name": row["node_name"],
                "node_class": row["node_class"],
                "cpu_capacity": to_float(row["cpu_capacity"]),
                "memory_capacity": to_float(row["memory_capacity"]),
                "requested_cpu": to_float(row["requested_cpu"]),
                "requested_memory": to_float(row["requested_memory"]),
                "cpu_util": cpu_util,
                "memory_util": memory_util,
                "pod_count": to_int(row["pod_count"]),
                "active": to_int(row["pod_count"]) > 0,
                "cpu_memory_imbalance": abs(cpu_util - memory_util),
            }
        )

    return nodes


def calculate_summary(experiment_name, pods, nodes):
    total_pods = len(pods)
    scheduled_pods = sum(1 for pod in pods if pod.get("node_name"))
    pending_pods = sum(1 for pod in pods if pod.get("phase") == "Pending" or not pod.get("node_name"))

    total_nodes = len(nodes)
    active_nodes = sum(1 for node in nodes if node["active"])

    cpu_utils = [node["cpu_util"] for node in nodes]
    memory_utils = [node["memory_util"] for node in nodes]
    imbalances = [node["cpu_memory_imbalance"] for node in nodes]

    total_cpu_capacity = sum(node["cpu_capacity"] for node in nodes)
    total_memory_capacity = sum(node["memory_capacity"] for node in nodes)
    total_requested_cpu = sum(node["requested_cpu"] for node in nodes)
    total_requested_memory = sum(node["requested_memory"] for node in nodes)

    return {
        "experiment_name": experiment_name,
        "total_pods": total_pods,
        "scheduled_pods": scheduled_pods,
        "pending_pods": pending_pods,
        "scheduling_success_ratio": ratio(scheduled_pods, total_pods),
        "total_nodes": total_nodes,
        "active_nodes": active_nodes,
        "active_nodes_ratio": ratio(active_nodes, total_nodes),
        "average_cpu_util": mean(cpu_utils),
        "average_memory_util": mean(memory_utils),
        "stddev_cpu_util": stddev(cpu_utils),
        "stddev_memory_util": stddev(memory_utils),
        "average_cpu_memory_imbalance": mean(imbalances),
        "total_cpu_capacity": total_cpu_capacity,
        "total_memory_capacity": total_memory_capacity,
        "total_requested_cpu": total_requested_cpu,
        "total_requested_memory": total_requested_memory,
        "cluster_cpu_util": ratio(total_requested_cpu, total_cpu_capacity),
        "cluster_memory_util": ratio(total_requested_memory, total_memory_capacity),
    }


def calculate_per_class(experiment_name, nodes):
    grouped = {}
    for node in nodes:
        node_class = node["node_class"] or "<unknown>"
        grouped.setdefault(node_class, []).append(node)

    rows = []
    for node_class in sorted(grouped):
        items = grouped[node_class]
        node_count = len(items)
        active_nodes = sum(1 for node in items if node["active"])
        inactive_nodes = node_count - active_nodes

        cpu_capacity = sum(node["cpu_capacity"] for node in items)
        memory_capacity = sum(node["memory_capacity"] for node in items)
        requested_cpu = sum(node["requested_cpu"] for node in items)
        requested_memory = sum(node["requested_memory"] for node in items)

        cpu_utils = [node["cpu_util"] for node in items]
        memory_utils = [node["memory_util"] for node in items]
        imbalances = [node["cpu_memory_imbalance"] for node in items]

        rows.append(
            {
                "experiment_name": experiment_name,
                "node_class": node_class,
                "node_count": node_count,
                "active_nodes": active_nodes,
                "inactive_nodes": inactive_nodes,
                "inactive_nodes_ratio": ratio(inactive_nodes, node_count),
                "active_nodes_ratio": ratio(active_nodes, node_count),
                "pod_count": sum(node["pod_count"] for node in items),
                "cpu_capacity": cpu_capacity,
                "memory_capacity": memory_capacity,
                "requested_cpu": requested_cpu,
                "requested_memory": requested_memory,
                "cpu_util": ratio(requested_cpu, cpu_capacity),
                "memory_util": ratio(requested_memory, memory_capacity),
                "average_cpu_util": mean(cpu_utils),
                "average_memory_util": mean(memory_utils),
                "stddev_cpu_util": stddev(cpu_utils),
                "stddev_memory_util": stddev(memory_utils),
                "average_cpu_memory_imbalance": mean(imbalances),
            }
        )

    return rows


def calculate_per_node(experiment_name, nodes):
    rows = []

    for node in sorted(nodes, key=lambda item: item["node_name"]):
        rows.append(
            {
                "experiment_name": experiment_name,
                "node_name": node["node_name"],
                "node_class": node["node_class"],
                "active": node["active"],
                "pod_count": node["pod_count"],
                "cpu_capacity": node["cpu_capacity"],
                "memory_capacity": node["memory_capacity"],
                "requested_cpu": node["requested_cpu"],
                "requested_memory": node["requested_memory"],
                "cpu_util": node["cpu_util"],
                "memory_util": node["memory_util"],
                "cpu_memory_imbalance": node["cpu_memory_imbalance"],
            }
        )

    return rows


def print_summary(summary, per_class, digits):
    print("Metrics summary:")
    print(f"- experiment: {summary['experiment_name']}")
    print(f"- pods scheduled: {summary['scheduled_pods']}/{summary['total_pods']}")
    print(f"- pending pods: {summary['pending_pods']}")
    print(f"- scheduling success ratio: {summary['scheduling_success_ratio']:.{digits}f}")
    print(f"- active nodes: {summary['active_nodes']}/{summary['total_nodes']}")
    print(f"- active nodes ratio: {summary['active_nodes_ratio']:.{digits}f}")
    print(f"- average CPU util: {summary['average_cpu_util']:.{digits}f}")
    print(f"- average memory util: {summary['average_memory_util']:.{digits}f}")
    print(f"- CPU util stddev: {summary['stddev_cpu_util']:.{digits}f}")
    print(f"- memory util stddev: {summary['stddev_memory_util']:.{digits}f}")
    print(f"- average CPU/RAM imbalance: {summary['average_cpu_memory_imbalance']:.{digits}f}")
    print(f"- cluster CPU util: {summary['cluster_cpu_util']:.{digits}f}")
    print(f"- cluster memory util: {summary['cluster_memory_util']:.{digits}f}")
    print()
    print("Per-class summary:")
    for row in per_class:
        print(
            f"- {row['node_class']}: "
            f"active={row['active_nodes']}/{row['node_count']}, "
            f"cpu={row['cpu_util']:.{digits}f}, "
            f"memory={row['memory_util']:.{digits}f}"
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Calculate experiment metrics")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--experiment-name", default=None)
    parser.add_argument("--digits", type=int, default=6)
    return parser.parse_args()


def main():
    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10+ is required", file=sys.stderr)
        return 1

    args = parse_args()
    input_dir = args.input_dir.expanduser().resolve()

    if not input_dir.exists():
        print(f"ERROR: input dir not found: {input_dir}", file=sys.stderr)
        return 1

    experiment_name = experiment_name_from_dir(input_dir, args.experiment_name)
    output_dir = args.output_dir or (PROCESSED_ROOT / experiment_name)
    output_dir = output_dir.expanduser().resolve()

    pods = read_csv(input_dir / "pod_placement.csv")
    nodes = prepare_nodes(read_csv(input_dir / "node_allocations.csv"))

    summary = calculate_summary(experiment_name, pods, nodes)
    per_class = calculate_per_class(experiment_name, nodes)
    per_node = calculate_per_node(experiment_name, nodes)

    summary = round_row(summary, args.digits)
    per_class = [round_row(row, args.digits) for row in per_class]
    per_node = [round_row(row, args.digits) for row in per_node]

    write_json(output_dir / "metrics_summary.json", {"summary": summary, "per_class": per_class})
    write_csv(output_dir / "metrics_summary.csv", [summary])
    write_csv(output_dir / "per_class_metrics.csv", per_class)
    write_csv(output_dir / "per_node_metrics.csv", per_node)

    print(f"[OK] wrote {output_dir / 'metrics_summary.json'}")
    print(f"[OK] wrote {output_dir / 'metrics_summary.csv'}")
    print(f"[OK] wrote {output_dir / 'per_class_metrics.csv'}")
    print(f"[OK] wrote {output_dir / 'per_node_metrics.csv'}")
    print()
    print_summary(summary, per_class, args.digits)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())