#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path


EXPERIMENT_LABEL = "edge-scheduling"

NODE_CLASSES = [
    {
        "name": "powerful",
        "count": 10,
        "cpu": "8",
        "memory": "32Gi",
        "region": "core-edge",
    },
    {
        "name": "gateway",
        "count": 30,
        "cpu": "4",
        "memory": "16Gi",
        "region": "local-gateway",
    },
    {
        "name": "iot-a",
        "count": 20,
        "cpu": "2",
        "memory": "4Gi",
        "region": "field-area",
    },
    {
        "name": "iot-b",
        "count": 20,
        "cpu": "2",
        "memory": "3Gi",
        "region": "field-area",
    },
    {
        "name": "iot-c",
        "count": 20,
        "cpu": "1",
        "memory": "2Gi",
        "region": "field-area",
    },
]


def node_yaml(name, node_class, pods_capacity):
    return f"""---
apiVersion: v1
kind: Node
metadata:
  name: {name}
  labels:
    experiment: {EXPERIMENT_LABEL}
    node-class: {node_class['name']}
    node-role.kubernetes.io/edge: "true"
    topology.kubernetes.io/region: {node_class['region']}
    kubernetes.io/hostname: {name}
    kubernetes.io/arch: amd64
    kubernetes.io/os: linux
    kwok.x-k8s.io/node: fake
    type: kwok
  annotations:
    node.alpha.kubernetes.io/ttl: "0"
    kwok.x-k8s.io/node: fake
status:
  capacity:
    cpu: "{node_class['cpu']}"
    memory: "{node_class['memory']}"
    pods: "{pods_capacity}"
  allocatable:
    cpu: "{node_class['cpu']}"
    memory: "{node_class['memory']}"
    pods: "{pods_capacity}"
  nodeInfo:
    architecture: amd64
    bootID: ""
    containerRuntimeVersion: kwok
    kernelVersion: kwok
    kubeProxyVersion: kwok
    kubeletVersion: kwok
    machineID: ""
    operatingSystem: linux
    osImage: kwok
    systemUUID: ""
  phase: Running
"""


def generate_nodes(prefix, pods_capacity):
    manifests = []
    total = 0

    for node_class in NODE_CLASSES:
        for index in range(1, node_class["count"] + 1):
            name = f"{prefix}-{node_class['name']}-{index:03d}"
            manifests.append(node_yaml(name, node_class, pods_capacity))
            total += 1

    return "".join(manifests), total


def parse_args():
    parser = argparse.ArgumentParser(description="Generate KWOK node manifest")
    parser.add_argument("--output", type=Path, default=Path("manifests/nodes.yaml"))
    parser.add_argument("--node-prefix", default="edge")
    parser.add_argument("--pods-capacity", type=int, default=110)
    return parser.parse_args()


def main():
    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10+ is required", file=sys.stderr)
        return 1

    args = parse_args()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    content, total = generate_nodes(args.node_prefix, args.pods_capacity)
    output.write_text(content, encoding="utf-8")

    print(f"Generated nodes: {total}")
    print(f"Output: {output}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())