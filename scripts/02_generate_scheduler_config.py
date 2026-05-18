#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path


DEFAULT_SHAPE = Path("configs/scheduler/requested-ratio-shape.json")
DEFAULT_OUTPUT = Path("configs/scheduler/scheduler-profiles.yaml")

DEFAULT_SHAPE_DATA = {
    "resources": [
        {"name": "cpu", "weight": 1},
        {"name": "memory", "weight": 1},
    ],
    "shape": [
        {"utilization": 0, "score": 0},
        {"utilization": 40, "score": 1},
        {"utilization": 60, "score": 3},
        {"utilization": 75, "score": 8},
        {"utilization": 90, "score": 10},
        {"utilization": 100, "score": 10},
    ],
}

PROFILES = [
    {
        "name": "least-allocated",
        "scheduler": "least-allocated-scheduler",
        "strategy": "LeastAllocated",
    },
    {
        "name": "most-allocated",
        "scheduler": "most-allocated-scheduler",
        "strategy": "MostAllocated",
    },
    {
        "name": "requested-ratio",
        "scheduler": "requested-ratio-scheduler",
        "strategy": "RequestedToCapacityRatio",
    },
]


def load_shape(path, reset=False):
    path.parent.mkdir(parents=True, exist_ok=True)

    if reset or not path.exists():
        path.write_text(json.dumps(DEFAULT_SHAPE_DATA, indent=2) + "\n", encoding="utf-8")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        print(f"ERROR: invalid JSON in {path}: {error}", file=sys.stderr)
        sys.exit(1)

    resources = data.get("resources")
    shape = data.get("shape")

    if not isinstance(resources, list) or not isinstance(shape, list):
        print("ERROR: shape file must contain 'resources' and 'shape' lists", file=sys.stderr)
        sys.exit(1)

    return resources, shape


def resources_yaml(resources):
    text = ""
    for item in resources:
        text += "              -\n"
        text += f"                name: {item['name']}\n"
        text += f"                weight: {item['weight']}\n"
    return text


def shape_yaml(shape):
    text = ""
    for item in shape:
        text += "                -\n"
        text += f"                  utilization: {item['utilization']}\n"
        text += f"                  score: {item['score']}\n"
    return text


def profile_yaml(profile, resources, shape):
    text = f"""  - schedulerName: {profile['scheduler']}
    pluginConfig:
      - name: NodeResourcesFit
        args:
          apiVersion: kubescheduler.config.k8s.io/v1
          kind: NodeResourcesFitArgs
          scoringStrategy:
            type: {profile['strategy']}
            resources:
"""
    text += resources_yaml(resources)

    if profile["strategy"] == "RequestedToCapacityRatio":
        text += "            requestedToCapacityRatio:\n"
        text += "              shape:\n"
        text += shape_yaml(shape)

    return text


def build_scheduler_config(resources, shape):
    text = """apiVersion: kubescheduler.config.k8s.io/v1
kind: KubeSchedulerConfiguration
profiles:
"""

    for profile in PROFILES:
        text += profile_yaml(profile, resources, shape)

    return text


def parse_args():
    parser = argparse.ArgumentParser(description="Generate kube-scheduler profiles")
    parser.add_argument("--shape", type=Path, default=DEFAULT_SHAPE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reset-shape", action="store_true")
    return parser.parse_args()


def main():
    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10+ is required", file=sys.stderr)
        return 1

    args = parse_args()
    shape_path = args.shape.expanduser().resolve()
    output_path = args.output.expanduser().resolve()

    resources, shape = load_shape(shape_path, reset=args.reset_shape)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_scheduler_config(resources, shape), encoding="utf-8")

    print("Generated scheduler config")
    print(f"Shape file: {shape_path}")
    print(f"Scheduler config: {output_path}")
    print("Profiles:")
    for profile in PROFILES:
        print(f"- {profile['name']}: {profile['scheduler']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())