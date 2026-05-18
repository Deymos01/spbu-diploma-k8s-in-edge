#!/usr/bin/env python3

import argparse
import csv
import glob
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path


RESULTS_ROOT = Path("results")
RAW_ROOT = Path("data/raw")

STRATEGIES = [
    "least-allocated", 
    "most-allocated", 
    "requested-ratio", 
    "edgefit",
    "edgefit-balance035",
    "edgefit-class045",
    "edgefit-packing040",
]
ORDERS = ["grouped", "small-first", "heavy-first", "interleaved", "shuffled"]
PROFILES = ["A", "B", "C", "D", "E"]
NODE_CLASSES = ["powerful", "gateway", "iot-a", "iot-b", "iot-c", "<unscheduled>"]
SCENARIOS = ["light", "balanced-medium", "cpu-fragmentation", "memory-pressure", "cpu-memory-imbalance"]

SCENARIO_SHORT = {
    "light": "L",
    "balanced-medium": "BM",
    "cpu-fragmentation": "CF",
    "memory-pressure": "MP",
    "cpu-memory-imbalance": "CMI",
}

ORDER_SHORT = {
    "grouped": "G",
    "small-first": "SF",
    "heavy-first": "HF",
    "interleaved": "INT",
    "shuffled": "SH42",
}

STRATEGY_SHORT = {
    "least-allocated": "LA",
    "most-allocated": "MA",
    "requested-ratio": "RR",
    "edgefit": "EF",
    "edgefit-balance035": "EF-B35",
    "edgefit-class045": "EF-C45",
    "edgefit-packing040": "EF-P40",
}

NODE_CLASS_SHORT = {
    "powerful": "P",
    "gateway": "GW",
    "iot-a": "IA",
    "iot-b": "IB",
    "iot-c": "IC",
    "<unscheduled>": "UNS",
}

SUMMARY_FIELDS = [
    "total_pods",
    "scheduled_pods",
    "pending_pods",
    "scheduling_success_ratio",
    "total_nodes",
    "active_nodes",
    "active_nodes_ratio",
    "average_cpu_util",
    "average_memory_util",
    "stddev_cpu_util",
    "stddev_memory_util",
    "average_cpu_memory_imbalance",
    "total_cpu_capacity",
    "total_memory_capacity",
    "total_requested_cpu",
    "total_requested_memory",
    "cluster_cpu_util",
    "cluster_memory_util",
]

INT_FIELDS = {"total_pods", "scheduled_pods", "pending_pods", "total_nodes", "active_nodes", "node_count", "active_nodes", "inactive_nodes", "pod_count"}


def order_index(value, order_list):
    try:
        return order_list.index(value)
    except ValueError:
        return len(order_list)


def read_csv(path):
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        path.write_text("\n", encoding="utf-8")
        return

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def to_number(value, digits):
    if value == "" or value is None:
        return ""

    number = float(value)
    if number == int(number):
        return int(number)
    return round(number, digits)


def short_label(row):
    scenario = SCENARIO_SHORT.get(row["scenario"], row["scenario"])
    order = ORDER_SHORT.get(row["order"], row["order"])
    strategy = STRATEGY_SHORT.get(row["strategy"], row["strategy"])
    return f"{scenario}/{order}/{strategy}"


def parse_experiment_name(name):
    normalized = name.replace("_", "-").lower()

    strategy = None
    for item in sorted(STRATEGIES, key=len, reverse=True):
        if normalized.endswith("-" + item):
            strategy = item
            break

    if strategy is None:
        print(f"ERROR: cannot detect strategy in experiment name: {name}", file=sys.stderr)
        sys.exit(1)

    prefix = normalized[: -len(strategy)].rstrip("-")
    scenario = prefix
    order = "unknown"

    for item in sorted(ORDERS, key=len, reverse=True):
        if prefix.endswith("-" + item):
            order = item
            scenario = prefix[: -len(item)].rstrip("-")
            break

    return scenario, order, strategy


def expand_paths(values):
    paths = []
    for value in values:
        matches = glob.glob(value)
        if matches:
            paths.extend(matches)
        else:
            paths.append(value)

    result = []
    seen = set()
    for item in paths:
        path = Path(item).expanduser().resolve()
        if path not in seen:
            result.append(path)
            seen.add(path)
    return result


def get_experiments(values, raw_root):
    experiments = []

    for path in expand_paths(values):
        name = path.name
        scenario, order, strategy = parse_experiment_name(name)
        experiments.append(
            {
                "scenario": scenario,
                "order": order,
                "strategy": strategy,
                "experiment_name": name,
                "processed_dir": path,
                "raw_dir": raw_root / name,
            }
        )

    experiments.sort(
        key=lambda item: (
            order_index(item["scenario"], SCENARIOS),
            item["scenario"],
            order_index(item["order"], ORDERS),
            order_index(item["strategy"], STRATEGIES),
        )
    )
    return experiments


def load_summary(experiment, digits):
    rows = read_csv(experiment["processed_dir"] / "metrics_summary.csv")
    if len(rows) != 1:
        print(f"ERROR: bad metrics_summary.csv in {experiment['processed_dir']}", file=sys.stderr)
        sys.exit(1)

    source = rows[0]
    row = {
        "scenario": experiment["scenario"],
        "order": experiment["order"],
        "strategy": experiment["strategy"],
        "experiment_name": experiment["experiment_name"],
    }

    for field in SUMMARY_FIELDS:
        row[field] = to_number(source.get(field, ""), digits)

    return row


def load_per_class(experiment, digits):
    rows = []

    for source in read_csv(experiment["processed_dir"] / "per_class_metrics.csv"):
        row = {
            "scenario": experiment["scenario"],
            "order": experiment["order"],
            "strategy": experiment["strategy"],
            "experiment_name": experiment["experiment_name"],
            "node_class": source.get("node_class", ""),
        }

        for key, value in source.items():
            if key in {"experiment_name", "node_class"}:
                continue
            row[key] = int(float(value)) if key in INT_FIELDS and value != "" else to_number(value, digits)

        rows.append(row)

    return rows


def load_per_node(experiment, digits):
    rows = []

    for source in read_csv(experiment["processed_dir"] / "per_node_metrics.csv"):
        row = {
            "scenario": experiment["scenario"],
            "order": experiment["order"],
            "strategy": experiment["strategy"],
            "experiment_name": experiment["experiment_name"],
            "node_name": source.get("node_name", ""),
            "node_class": source.get("node_class", ""),
            "active": source.get("active", ""),
        }

        for key, value in source.items():
            if key in {"experiment_name", "node_name", "node_class", "active"}:
                continue
            row[key] = int(float(value)) if key == "pod_count" and value != "" else to_number(value, digits)

        rows.append(row)

    return rows


def build_pending_by_profile(experiments):
    result = []

    for experiment in experiments:
        rows = read_csv(experiment["raw_dir"] / "pod_placement.csv")
        counters = {}

        for row in rows:
            profile = row.get("profile") or "<unknown>"
            counters.setdefault(profile, {"total_pods": 0, "scheduled_pods": 0, "pending_pods": 0})

            scheduled = bool(row.get("node_name"))
            pending = row.get("phase") == "Pending" or not scheduled

            counters[profile]["total_pods"] += 1
            counters[profile]["scheduled_pods"] += int(scheduled)
            counters[profile]["pending_pods"] += int(pending)

        for profile in sorted(counters, key=lambda item: order_index(item, PROFILES)):
            values = counters[profile]
            result.append(
                {
                    "scenario": experiment["scenario"],
                    "order": experiment["order"],
                    "strategy": experiment["strategy"],
                    "profile": profile,
                    **values,
                }
            )

    return result


def build_placement_by_class(per_class_rows):
    result = []

    for row in per_class_rows:
        result.append(
            {
                "scenario": row["scenario"],
                "order": row["order"],
                "strategy": row["strategy"],
                "node_class": row["node_class"],
                "pod_count": row.get("pod_count", 0),
                "requested_cpu": row.get("requested_cpu", 0),
                "requested_memory": row.get("requested_memory", 0),
                "active_nodes": row.get("active_nodes", 0),
                "inactive_nodes": row.get("inactive_nodes", 0),
                "avg_cpu_util": row.get("average_cpu_util", 0),
                "avg_memory_util": row.get("average_memory_util", 0),
            }
        )

    return result


def build_pod_profile_by_node_class(experiments):
    result = []

    for experiment in experiments:
        rows = read_csv(experiment["raw_dir"] / "pod_placement.csv")
        counters = {}

        for row in rows:
            node_class = row.get("node_class") or "<unscheduled>"
            profile = row.get("profile") or "<unknown>"
            key = (node_class, profile)
            counters[key] = counters.get(key, 0) + 1

        for node_class in sorted({key[0] for key in counters}, key=lambda item: order_index(item, NODE_CLASSES)):
            for profile in sorted({key[1] for key in counters}, key=lambda item: order_index(item, PROFILES)):
                count = counters.get((node_class, profile), 0)
                if count == 0:
                    continue

                result.append(
                    {
                        "scenario": experiment["scenario"],
                        "order": experiment["order"],
                        "strategy": experiment["strategy"],
                        "node_class": node_class,
                        "profile": profile,
                        "pod_count": count,
                    }
                )

    return result


def build_matrix(summary_rows):
    rows = []

    for row in summary_rows:
        rows.append(
            {
                "scenario": row["scenario"],
                "order": row["order"],
                "strategy": row["strategy"],
                "scheduled_ratio": row.get("scheduling_success_ratio", ""),
                "pending_pods": row.get("pending_pods", ""),
                "active_nodes_ratio": row.get("active_nodes_ratio", ""),
                "cluster_cpu_util": row.get("cluster_cpu_util", ""),
                "cluster_memory_util": row.get("cluster_memory_util", ""),
                "avg_cpu_util": row.get("average_cpu_util", ""),
                "avg_memory_util": row.get("average_memory_util", ""),
                "cpu_stddev": row.get("stddev_cpu_util", ""),
                "memory_stddev": row.get("stddev_memory_util", ""),
                "avg_imbalance": row.get("average_cpu_memory_imbalance", ""),
            }
        )

    return rows


def plot_import():
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("ERROR: install matplotlib first: python3 -m pip install matplotlib", file=sys.stderr)
        sys.exit(1)
    return plt


def metric_title(field):
    titles = {
        "scheduling_success_ratio": "Scheduled ratio",
        "pending_pods": "Pending pods",
        "active_nodes_ratio": "Active nodes ratio",
        "average_cpu_memory_imbalance": "Average CPU/RAM imbalance",
        "cluster_cpu_util": "Cluster CPU utilization",
        "cluster_memory_util": "Cluster memory utilization",
    }
    return titles.get(field, field)


def heatmap_data(rows, field):
    scenario_orders = []
    strategies = []
    values = {}

    for row in rows:
        y_key = (row["scenario"], row["order"])
        if y_key not in scenario_orders:
            scenario_orders.append(y_key)
        if row["strategy"] not in strategies:
            strategies.append(row["strategy"])
        values[(row["scenario"], row["order"], row["strategy"])] = float(row[field])

    strategies.sort(key=lambda item: order_index(item, STRATEGIES))
    matrix = []
    for scenario, order in scenario_orders:
        matrix.append([values.get((scenario, order, strategy), math.nan) for strategy in strategies])

    x_labels = [STRATEGY_SHORT.get(item, item) for item in strategies]
    y_labels = [f"{SCENARIO_SHORT.get(s, s)}/{ORDER_SHORT.get(o, o)}" for s, o in scenario_orders]
    return x_labels, y_labels, matrix


def save_heatmap(plt, rows, field, path):
    x_labels, y_labels, matrix = heatmap_data(rows, field)
    if not matrix:
        return

    fig = plt.figure(figsize=(8, max(4.5, len(y_labels) * 0.5 + 1.5)))
    ax = fig.add_subplot(1, 1, 1)
    image = ax.imshow(matrix, aspect="auto")

    ax.set_title(metric_title(field))
    ax.set_xticks(range(len(x_labels)))
    ax.set_xticklabels(x_labels)
    ax.set_yticks(range(len(y_labels)))
    ax.set_yticklabels(y_labels)
    ax.set_xlabel("strategy")
    ax.set_ylabel("scenario/order")

    for y, row in enumerate(matrix):
        for x, value in enumerate(row):
            if math.isnan(value):
                continue
            label = str(int(value)) if field == "pending_pods" else f"{value:.2f}"
            ax.text(x, y, label, ha="center", va="center", fontsize=8)

    fig.colorbar(image, ax=ax)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_pending_stacked(plt, rows, path):
    keys = []
    data = {}

    for row in rows:
        key = (row["scenario"], row["order"], row["strategy"])
        pending = int(row["pending_pods"])
        if key not in keys:
            keys.append(key)
        data.setdefault(key, {})[row["profile"]] = pending

    keys = [key for key in keys if sum(data.get(key, {}).values()) > 0]
    if not keys:
        return

    labels = [f"{SCENARIO_SHORT.get(s, s)}/{ORDER_SHORT.get(o, o)}/{STRATEGY_SHORT.get(st, st)}" for s, o, st in keys]
    fig = plt.figure(figsize=(max(8, len(labels) * 0.65), 5.5))
    ax = fig.add_subplot(1, 1, 1)
    bottom = [0] * len(keys)
    x = list(range(len(keys)))

    for profile in PROFILES:
        values = [data.get(key, {}).get(profile, 0) for key in keys]
        if not any(values):
            continue
        ax.bar(x, values, bottom=bottom, label=profile)
        bottom = [a + b for a, b in zip(bottom, values)]

    ax.set_title("Pending pods by profile")
    ax.set_ylabel("pending pods")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.legend(title="profile")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_placement_100pct(plt, rows, path):
    keys = []
    data = {}

    for row in rows:
        key = (row["scenario"], row["order"], row["strategy"])
        if key not in keys:
            keys.append(key)
        node_class = row["node_class"]
        data.setdefault(key, {})[node_class] = int(float(row.get("pod_count", 0)))

    labels = [f"{SCENARIO_SHORT.get(s, s)}/{ORDER_SHORT.get(o, o)}/{STRATEGY_SHORT.get(st, st)}" for s, o, st in keys]
    fig = plt.figure(figsize=(max(10, len(labels) * 0.55), 6))
    ax = fig.add_subplot(1, 1, 1)
    bottom = [0.0] * len(keys)
    x = list(range(len(keys)))

    for node_class in NODE_CLASSES:
        if node_class == "<unscheduled>":
            continue

        values = []
        for key in keys:
            total = sum(data.get(key, {}).values())
            value = data.get(key, {}).get(node_class, 0)
            values.append(100 * value / total if total else 0)

        if not any(values):
            continue

        ax.bar(x, values, bottom=bottom, label=NODE_CLASS_SHORT.get(node_class, node_class))
        bottom = [a + b for a, b in zip(bottom, values)]

    ax.set_title("Pod placement by node class")
    ax.set_ylabel("share of pods, %")
    ax.set_ylim(0, 100)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=80, ha="right")
    ax.legend(title="node class")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def profile_node_chart_name(scenario, order, strategy, suffix):
    label = f"{SCENARIO_SHORT.get(scenario, scenario)}-{ORDER_SHORT.get(order, order)}-{STRATEGY_SHORT.get(strategy, strategy)}"
    return label.lower().replace("/", "-") + f"_{suffix}.png"


def save_profile_by_node_class_chart(plt, rows, path, title, percent):
    data = {}
    for row in rows:
        node_class = row["node_class"]
        profile = row["profile"]
        data.setdefault(node_class, {})[profile] = int(float(row.get("pod_count", 0)))

    node_classes = [item for item in NODE_CLASSES if item != "<unscheduled>" and item in data]
    if not node_classes:
        return False

    x = list(range(len(node_classes)))
    bottom = [0.0] * len(node_classes)

    fig = plt.figure(figsize=(8, 5.5))
    ax = fig.add_subplot(1, 1, 1)

    for profile in PROFILES:
        values = []
        for node_class in node_classes:
            total = sum(data.get(node_class, {}).values())
            count = data.get(node_class, {}).get(profile, 0)

            if percent:
                values.append(100 * count / total if total else 0)
            else:
                values.append(count)

        if not any(values):
            continue

        ax.bar(x, values, bottom=bottom, label=profile)
        bottom = [a + b for a, b in zip(bottom, values)]

    ax.set_title(title)
    ax.set_xlabel("node class")
    ax.set_xticks(x)
    ax.set_xticklabels([NODE_CLASS_SHORT.get(item, item) for item in node_classes])
    ax.legend(title="pod profile")
    ax.grid(axis="y", alpha=0.3)

    if percent:
        ax.set_ylabel("share of pods on node class, %")
        ax.set_ylim(0, 100)
    else:
        ax.set_ylabel("pod count")

    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return True


def save_profile_by_node_class_charts(plt, rows, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    files = []
    groups = {}

    for row in rows:
        key = (row["scenario"], row["order"], row["strategy"])
        groups.setdefault(key, []).append(row)

    keys = sorted(
        groups,
        key=lambda item: (
            order_index(item[0], SCENARIOS),
            item[0],
            order_index(item[1], ORDERS),
            order_index(item[2], STRATEGIES),
        ),
    )

    for scenario, order, strategy in keys:
        short = f"{SCENARIO_SHORT.get(scenario, scenario)}/{ORDER_SHORT.get(order, order)}/{STRATEGY_SHORT.get(strategy, strategy)}"

        count_path = output_dir / profile_node_chart_name(scenario, order, strategy, "count")
        if save_profile_by_node_class_chart(
            plt,
            groups[(scenario, order, strategy)],
            count_path,
            f"Pod profile count by node class: {short}",
            percent=False,
        ):
            files.append(count_path)

        percent_path = output_dir / profile_node_chart_name(scenario, order, strategy, "100pct")
        if save_profile_by_node_class_chart(
            plt,
            groups[(scenario, order, strategy)],
            percent_path,
            f"Pod profile share by node class: {short}",
            percent=True,
        ):
            files.append(percent_path)

    return files


def save_order_line(plt, rows, field, path):
    rows = [row for row in rows if row["scenario"] == "cpu-fragmentation"]
    orders = sorted({row["order"] for row in rows}, key=lambda item: order_index(item, ORDERS))
    strategies = sorted({row["strategy"] for row in rows}, key=lambda item: order_index(item, STRATEGIES))

    if len(orders) <= 1:
        return

    lookup = {(row["order"], row["strategy"]): float(row[field]) for row in rows}
    x = list(range(len(orders)))

    fig = plt.figure(figsize=(9, 5.5))
    ax = fig.add_subplot(1, 1, 1)

    for strategy in strategies:
        values = [lookup.get((order, strategy), math.nan) for order in orders]
        ax.plot(x, values, marker="o", label=STRATEGY_SHORT.get(strategy, strategy))

    ax.set_title(f"CF: {metric_title(field)} by order")
    ax.set_ylabel(metric_title(field))
    ax.set_xticks(x)
    ax.set_xticklabels([ORDER_SHORT.get(order, order) for order in orders])
    ax.legend(title="strategy")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def build_plots(summary_rows, pending_rows, placement_rows, profile_node_rows, plots_dir):
    plt = plot_import()
    plots_dir.mkdir(parents=True, exist_ok=True)

    files = []
    heatmaps = [
        ("scheduling_success_ratio", "01_heatmap_scheduled_ratio.png"),
        ("pending_pods", "02_heatmap_pending_pods.png"),
        ("active_nodes_ratio", "03_heatmap_active_nodes_ratio.png"),
        ("average_cpu_memory_imbalance", "04_heatmap_avg_imbalance.png"),
        ("cluster_cpu_util", "05_heatmap_cluster_cpu_util.png"),
        ("cluster_memory_util", "06_heatmap_cluster_memory_util.png"),
    ]

    for field, filename in heatmaps:
        path = plots_dir / filename
        save_heatmap(plt, summary_rows, field, path)
        files.append(path)

    path = plots_dir / "07_pending_by_profile_stacked.png"
    save_pending_stacked(plt, pending_rows, path)
    if path.exists():
        files.append(path)

    path = plots_dir / "08_placement_by_node_class_100pct.png"
    save_placement_100pct(plt, placement_rows, path)
    files.append(path)

    files.extend(
        save_profile_by_node_class_charts(
            plt,
            profile_node_rows,
            plots_dir / "pod_profile_by_node_class",
        )
    )

    path = plots_dir / "09_cpu_fragmentation_pending_by_order.png"
    save_order_line(plt, summary_rows, "pending_pods", path)
    if path.exists():
        files.append(path)

    path = plots_dir / "10_cpu_fragmentation_imbalance_by_order.png"
    save_order_line(plt, summary_rows, "average_cpu_memory_imbalance", path)
    if path.exists():
        files.append(path)

    return files


def print_summary(rows):
    print("Comparison summary:")
    for row in rows:
        print(
            f"- {row['scenario']}/{row['order']}/{row['strategy']}: "
            f"scheduled={row['scheduled_pods']}/{row['total_pods']}, "
            f"pending={row['pending_pods']}, "
            f"active_nodes_ratio={row['active_nodes_ratio']}, "
            f"imbalance={row['average_cpu_memory_imbalance']}"
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Compare experiment results")
    parser.add_argument("--experiments", nargs="+", required=True)
    parser.add_argument("--comparison-name", default="comparison")
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--results-root", type=Path, default=RESULTS_ROOT)
    parser.add_argument("--digits", type=int, default=6)
    parser.add_argument("--no-plots", action="store_true")
    return parser.parse_args()


def main():
    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10+ is required", file=sys.stderr)
        return 1

    args = parse_args()
    raw_root = args.raw_root.expanduser().resolve()
    output_dir = (args.results_root / args.comparison_name).expanduser().resolve()
    experiments = get_experiments(args.experiments, raw_root)

    summary_rows = []
    per_class_rows = []
    per_node_rows = []

    for experiment in experiments:
        if not experiment["processed_dir"].exists():
            print(f"ERROR: processed dir not found: {experiment['processed_dir']}", file=sys.stderr)
            return 1
        if not experiment["raw_dir"].exists():
            print(f"ERROR: raw dir not found: {experiment['raw_dir']}", file=sys.stderr)
            return 1

        summary_rows.append(load_summary(experiment, args.digits))
        per_class_rows.extend(load_per_class(experiment, args.digits))
        per_node_rows.extend(load_per_node(experiment, args.digits))

    pending_rows = build_pending_by_profile(experiments)
    placement_rows = build_placement_by_class(per_class_rows)
    profile_node_rows = build_pod_profile_by_node_class(experiments)
    matrix_rows = build_matrix(summary_rows)

    write_csv(output_dir / "comparison_summary.csv", summary_rows)
    write_csv(output_dir / "per_class_comparison.csv", per_class_rows)
    write_csv(output_dir / "per_node_comparison.csv", per_node_rows)
    write_csv(output_dir / "pending_by_profile.csv", pending_rows)
    write_csv(output_dir / "placement_by_class.csv", placement_rows)
    write_csv(output_dir / "pod_profile_by_node_class.csv", profile_node_rows)
    write_csv(output_dir / "scenario_strategy_matrix.csv", matrix_rows)

    plots = []
    if not args.no_plots:
        plots = build_plots(summary_rows, pending_rows, placement_rows, profile_node_rows, output_dir / "plots")

    write_json(
        output_dir / "comparison_metadata.json",
        {
            "comparison_name": args.comparison_name,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "experiments": [item["experiment_name"] for item in experiments],
            "plots": [str(path) for path in plots],
        },
    )

    print(f"[OK] results saved to: {output_dir}")
    print(f"[OK] experiments compared: {len(experiments)}")
    if plots:
        print(f"[OK] plots created: {len(plots)}")
    print()
    print_summary(summary_rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())