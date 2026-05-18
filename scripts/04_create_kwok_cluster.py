#!/usr/bin/env python3

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd, env=None, timeout=900, check=False):
    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=timeout,
        check=False,
    )

    if check and result.returncode != 0:
        print("ERROR: command failed")
        print("$", " ".join(cmd))
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)

    return result


def check_tool(name):
    path = shutil.which(name)
    if not path:
        print(f"[FAIL] {name} not found in PATH")
        return False
    print(f"[OK] {name}: {path}")
    return True


def cluster_exists(name, env):
    result = run(["kwokctl", "get", "clusters", "-o", "name"], env=env, timeout=60)

    if result.returncode != 0:
        print("ERROR: cannot list KWOK clusters", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)

    clusters = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return name in clusters


def create_cluster(args, env):
    cmd = [
        "kwokctl",
        "create",
        "cluster",
        "--name",
        args.cluster_name,
        "--wait",
        args.wait,
        "--timeout",
        args.timeout,
    ]

    if args.runtime:
        cmd += ["--runtime", args.runtime]

    if args.kube_scheduler_config:
        config_path = args.kube_scheduler_config.expanduser().resolve()
        if not config_path.exists():
            print(f"ERROR: scheduler config not found: {config_path}", file=sys.stderr)
            sys.exit(1)
        cmd += ["--kube-scheduler-config", str(config_path)]

    print("Creating KWOK cluster:")
    print("$", " ".join(cmd))

    result = run(cmd, env=env, timeout=args.command_timeout, check=True)

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)


def check_kubectl(env):
    context = run(["kubectl", "config", "current-context"], env=env, timeout=30)
    if context.returncode != 0:
        print("ERROR: kubectl context is not available", file=sys.stderr)
        if context.stderr:
            print(context.stderr, file=sys.stderr)
        sys.exit(context.returncode)

    print(f"[OK] current context: {context.stdout.strip()}")

    nodes = run(["kubectl", "get", "nodes"], env=env, timeout=60)
    if nodes.returncode != 0:
        print("ERROR: kubectl get nodes failed", file=sys.stderr)
        if nodes.stderr:
            print(nodes.stderr, file=sys.stderr)
        sys.exit(nodes.returncode)

    print("[OK] kubectl get nodes works")
    if nodes.stdout.strip():
        print(nodes.stdout.strip())


def parse_args():
    parser = argparse.ArgumentParser(description="Create KWOK cluster")
    parser.add_argument("--cluster-name", default="edge-kwok")
    parser.add_argument("--runtime", default="docker")
    parser.add_argument("--kube-scheduler-config", type=Path, default=None)
    parser.add_argument("--kubeconfig", type=Path, default=None)
    parser.add_argument("--wait", default="5m")
    parser.add_argument("--timeout", default="10m")
    parser.add_argument("--command-timeout", type=int, default=900)
    return parser.parse_args()


def main():
    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10+ is required", file=sys.stderr)
        return 1

    args = parse_args()

    env = os.environ.copy()
    if args.kubeconfig:
        env["KUBECONFIG"] = str(args.kubeconfig.expanduser().resolve())

    print("KWOK cluster setup")
    print(f"Cluster name: {args.cluster_name}")
    print(f"Runtime: {args.runtime}")
    if args.kube_scheduler_config:
        print(f"Scheduler config: {args.kube_scheduler_config.expanduser().resolve()}")
    if args.kubeconfig:
        print(f"KUBECONFIG: {env['KUBECONFIG']}")
    print()

    if not check_tool("kwokctl") or not check_tool("kubectl"):
        return 1

    print()
    if cluster_exists(args.cluster_name, env):
        print(f"[OK] KWOK cluster already exists: {args.cluster_name}")
    else:
        create_cluster(args, env)
        print(f"[OK] KWOK cluster created: {args.cluster_name}")

    print()
    check_kubectl(env)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())