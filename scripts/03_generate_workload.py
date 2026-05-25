#!/usr/bin/env python3

import argparse
import random
import sys
from pathlib import Path


EXPERIMENT_LABEL = "edge-scheduling"
DEFAULT_NAMESPACE = "edge-exp"
DEFAULT_IMAGE = "registry.k8s.io/pause:3.9"
SCENARIOS_FILE = Path("configs/workloads/scenarios.yaml")
WORKLOAD_DIR = Path("manifests/workloads")

PROFILES = {
    "A": {"cpu": "500m", "memory": "500Mi", "kind": "light"},
    "B": {"cpu": "1", "memory": "1Gi", "kind": "medium"},
    "C": {"cpu": "2", "memory": "2Gi", "kind": "heavy"},
    "D": {"cpu": "2", "memory": "512Mi", "kind": "cpu-bound"},
    "E": {"cpu": "500m", "memory": "4Gi", "kind": "memory-bound"},
}

DEFAULT_SCENARIOS = {
    "light": {"A": 120, "B": 0, "C": 0, "D": 0, "E": 0},
    "balanced-medium": {"A": 120, "B": 40, "C": 20, "D": 0, "E": 0},
    "cpu-fragmentation": {"A": 200, "B": 40, "C": 20, "D": 30, "E": 30},
    "memory-pressure": {"A": 100, "B": 20, "C": 0, "D": 0, "E": 190},
    "cpu-memory-imbalance": {"A": 0, "B": 20, "C": 0, "D": 70, "E": 70},
}

ALL_SCHEDULERS = [
    "least-allocated-scheduler",
    "most-allocated-scheduler",
    "requested-ratio-scheduler",
    "requested-ratio-soft-scheduler",
    "requested-ratio-aggressive-scheduler",
    "requested-ratio-balanced-scheduler",
    "edge-aware-scheduler",
]

SCHEDULERS = set(ALL_SCHEDULERS)

ORDERS = {
    "grouped": ["A", "B", "C", "D", "E"],
    "small-first": ["A", "B", "E", "C", "D"],
    "heavy-first": ["C", "D", "E", "B", "A"],
}


def write_default_scenarios(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return

    lines = ["scenarios:"]
    for name, counts in DEFAULT_SCENARIOS.items():
        lines.append(f"  {name}:")
        for profile in ["A", "B", "C", "D", "E"]:
            lines.append(f"    {profile}: {counts[profile]}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_scenarios(path):
    write_default_scenarios(path)
    scenarios = {}
    current = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip() or line.strip() == "scenarios:":
            continue

        if line.startswith("  ") and not line.startswith("    "):
            current = line.strip().rstrip(":")
            scenarios[current] = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
            continue

        if line.startswith("    ") and current:
            key, value = line.strip().split(":", 1)
            key = key.strip().upper()
            if key in PROFILES:
                scenarios[current][key] = int(value.strip())

    return scenarios


def get_counts(args, scenarios):
    if args.scenario_name not in scenarios:
        names = ", ".join(sorted(scenarios))
        print(f"ERROR: unknown scenario '{args.scenario_name}'. Available: {names}", file=sys.stderr)
        sys.exit(1)

    counts = dict(scenarios[args.scenario_name])

    for profile in ["A", "B", "C", "D", "E"]:
        value = getattr(args, profile.lower())
        if value is not None:
            counts[profile] = value

    if sum(counts.values()) == 0:
        print("ERROR: workload must contain at least one pod", file=sys.stderr)
        sys.exit(1)

    for profile, count in counts.items():
        if count < 0:
            print(f"ERROR: profile {profile} count must be non-negative", file=sys.stderr)
            sys.exit(1)

    return counts


def get_schedulers(values):
    if values == ["all"]:
        return ALL_SCHEDULERS

    for scheduler in values:
        if scheduler not in SCHEDULERS:
            print(f"ERROR: unknown scheduler: {scheduler}", file=sys.stderr)
            print("Available:", file=sys.stderr)
            for item in ALL_SCHEDULERS:
                print(f"- {item}", file=sys.stderr)
            print("- all", file=sys.stderr)
            sys.exit(1)

    return values


def make_plan(counts, order, seed):
    plan = []

    if order in ORDERS:
        for profile in ORDERS[order]:
            for index in range(1, counts[profile] + 1):
                plan.append((profile, index))
        return plan

    if order == "interleaved":
        max_count = max(counts.values())
        for index in range(1, max_count + 1):
            for profile in ["A", "B", "C", "D", "E"]:
                if index <= counts[profile]:
                    plan.append((profile, index))
        return plan

    if order == "shuffled":
        if seed is None:
            print("ERROR: --order shuffled requires --seed", file=sys.stderr)
            sys.exit(1)
        for profile in ["A", "B", "C", "D", "E"]:
            for index in range(1, counts[profile] + 1):
                plan.append((profile, index))
        random.Random(seed).shuffle(plan)
        return plan

    print(f"ERROR: unknown order {order}", file=sys.stderr)
    sys.exit(1)


def namespace_yaml(namespace):
    return f"""---
apiVersion: v1
kind: Namespace
metadata:
  name: {namespace}
  labels:
    experiment: {EXPERIMENT_LABEL}
"""


def pod_yaml(name, scenario, order, profile, scheduler, namespace):
    data = PROFILES[profile]
    return f"""---
apiVersion: v1
kind: Pod
metadata:
  name: {name}
  namespace: {namespace}
  labels:
    experiment: {EXPERIMENT_LABEL}
    scenario: {scenario}
    order: {order}
    profile: {profile}
    profile-kind: {data['kind']}
    scheduler: {scheduler}
spec:
  schedulerName: {scheduler}
  restartPolicy: Never
  containers:
    - name: pause
      image: {DEFAULT_IMAGE}
      resources:
        requests:
          cpu: "{data['cpu']}"
          memory: "{data['memory']}"
"""


def build_manifest(scenario, counts, order, seed, scheduler, namespace):
    plan = make_plan(counts, order, seed)
    text = namespace_yaml(namespace)

    for profile, index in plan:
        name = f"{scenario}-{profile.lower()}-{index:04d}"
        text += pod_yaml(name, scenario, order, profile, scheduler, namespace)

    return text, plan


def default_output(scenario, order, scheduler):
    short_scheduler = scheduler.removesuffix("-scheduler")
    return WORKLOAD_DIR / f"{scenario}-{order}-{short_scheduler}.yaml"


def parse_args():
    parser = argparse.ArgumentParser(description="Generate workload manifests")
    parser.add_argument("--scenario-name", required=True)
    parser.add_argument("--scheduler", nargs="+", required=True, help="One or more scheduler names, or: all")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--order", default="grouped", choices=["grouped", "small-first", "heavy-first", "interleaved", "shuffled"])
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None, help="Allowed only when one scheduler is used")
    parser.add_argument("--a", type=int, default=None)
    parser.add_argument("--b", type=int, default=None)
    parser.add_argument("--c", type=int, default=None)
    parser.add_argument("--d", type=int, default=None)
    parser.add_argument("--e", type=int, default=None)
    return parser.parse_args()


def main():
    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10+ is required", file=sys.stderr)
        return 1

    args = parse_args()
    schedulers = get_schedulers(args.scheduler)

    if args.output and len(schedulers) > 1:
        print("ERROR: --output can be used only with one scheduler", file=sys.stderr)
        return 1

    scenarios = load_scenarios(SCENARIOS_FILE)
    counts = get_counts(args, scenarios)

    print("Generating workload manifests")
    print(f"Scenario: {args.scenario_name}")
    print(f"Order: {args.order}")
    if args.seed is not None:
        print(f"Seed: {args.seed}")
    print(f"Namespace: {args.namespace}")
    print("Schedulers:")
    for scheduler in schedulers:
        print(f"- {scheduler}")
    print()

    outputs = []
    last_plan = []

    for scheduler in schedulers:
        output = args.output or default_output(args.scenario_name, args.order, scheduler)
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        manifest, plan = build_manifest(
            scenario=args.scenario_name,
            counts=counts,
            order=args.order,
            seed=args.seed,
            scheduler=scheduler,
            namespace=args.namespace,
        )
        output.write_text(manifest, encoding="utf-8")
        outputs.append(output)
        last_plan = plan
        print(f"[OK] {scheduler}: {output}")

    print()
    print(f"Total pods in each manifest: {len(last_plan)}")
    print("Profile counts:")
    for profile in ["A", "B", "C", "D", "E"]:
        print(f"- {profile}: {counts[profile]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())