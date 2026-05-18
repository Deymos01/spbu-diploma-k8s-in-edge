#!/usr/bin/env python3

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


LABEL = "experiment=edge-scheduling"
DEFAULT_NAMESPACE = "edge-exp"
DEFAULT_RAW_ROOT = Path("data/raw")


def run(cmd, timeout=120, input_text=None):
    return subprocess.run(
        cmd,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def run_checked(cmd, timeout=120, input_text=None):
    result = run(cmd, timeout=timeout, input_text=input_text)
    if result.returncode != 0:
        print("ERROR: command failed", file=sys.stderr)
        print("$", " ".join(cmd), file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    return result


def kubectl_json(cmd, timeout=120):
    result = run_checked(cmd + ["-o", "json"], timeout=timeout)
    return json.loads(result.stdout)


def check_kubectl():
    path = shutil.which("kubectl")
    if not path:
        print("ERROR: kubectl not found", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] kubectl: {path}")


def ensure_namespace(namespace):
    text = f"""apiVersion: v1
kind: Namespace
metadata:
  name: {namespace}
  labels:
    experiment: edge-scheduling
"""
    run_checked(["kubectl", "apply", "-f", "-"], input_text=text, timeout=60)
    print(f"[OK] namespace: {namespace}")


def delete_old_pods(namespace, timeout):
    print(f"Deleting old pods: namespace={namespace}, selector={LABEL}")
    run_checked(
        [
            "kubectl",
            "delete",
            "pods",
            "-n",
            namespace,
            "-l",
            LABEL,
            "--ignore-not-found=true",
            "--wait=true",
            f"--timeout={timeout}s",
        ],
        timeout=timeout + 30,
    )
    print("[OK] old pods removed")


def count_pods_in_manifest(path):
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() in {"kind: Pod", "kind: \"Pod\""}:
            count += 1
    return count


def pod_stats(namespace):
    data = kubectl_json(
        ["kubectl", "get", "pods", "-n", namespace, "-l", LABEL],
        timeout=120,
    )

    stats = {
        "total": 0,
        "scheduled": 0,
        "pending": 0,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "other": 0,
    }

    for pod in data.get("items", []):
        stats["total"] += 1

        spec = pod.get("spec", {})
        if spec.get("nodeName"):
            stats["scheduled"] += 1

        phase = pod.get("status", {}).get("phase", "Unknown")
        if phase == "Pending":
            stats["pending"] += 1
        elif phase == "Running":
            stats["running"] += 1
        elif phase == "Succeeded":
            stats["succeeded"] += 1
        elif phase == "Failed":
            stats["failed"] += 1
        else:
            stats["other"] += 1

    return stats


def stats_line(stats):
    return (
        f"total={stats['total']} scheduled={stats['scheduled']} "
        f"pending={stats['pending']} running={stats['running']} "
        f"succeeded={stats['succeeded']} failed={stats['failed']} other={stats['other']}"
    )


def wait_for_scheduling(namespace, expected, timeout):
    start = time.monotonic()
    last_scheduled = -1
    stable_count = 0
    stats = {}

    while True:
        stats = pod_stats(namespace)
        elapsed = time.monotonic() - start
        print(f"[{elapsed:6.1f}s] {stats_line(stats)}")

        if stats["total"] >= expected:
            if stats["scheduled"] == expected:
                return stats, False

            if stats["scheduled"] == last_scheduled:
                stable_count += 1
            else:
                stable_count = 0
                last_scheduled = stats["scheduled"]

            if stable_count >= 5:
                return stats, False

        if elapsed >= timeout:
            return stats, True

        time.sleep(2)


def save_command(cmd, path, timeout=120):
    result = run_checked(cmd, timeout=timeout)
    path.write_text(result.stdout + "\n", encoding="utf-8")


def save_raw_state(raw_dir, namespace, workload, experiment_name, expected, stats, timed_out):
    raw_dir.mkdir(parents=True, exist_ok=True)

    save_command(
        ["kubectl", "get", "pods", "-n", namespace, "-l", LABEL, "-o", "json"],
        raw_dir / "pods.json",
    )
    save_command(["kubectl", "get", "nodes", "-o", "json"], raw_dir / "nodes.json")
    save_command(
        ["kubectl", "get", "events", "-n", namespace, "--sort-by=.lastTimestamp", "-o", "json"],
        raw_dir / "events.json",
    )
    save_command(
        ["kubectl", "get", "pods", "-n", namespace, "-l", LABEL, "-o", "wide"],
        raw_dir / "pods_wide.txt",
    )
    save_command(["kubectl", "get", "nodes", "-L", "node-class"], raw_dir / "nodes_table.txt")

    (raw_dir / "workload.yaml").write_text(workload.read_text(encoding="utf-8"), encoding="utf-8")

    metadata = {
        "experiment_name": experiment_name,
        "namespace": namespace,
        "selector": LABEL,
        "workload": str(workload),
        "expected_pods": expected,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "timed_out": timed_out,
        "summary": stats,
    }
    (raw_dir / "experiment_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"[OK] raw data saved: {raw_dir}")


def parse_args():
    parser = argparse.ArgumentParser(description="Run one workload experiment")
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--experiment-name", default=None)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--skip-cleanup", action="store_true")
    return parser.parse_args()


def main():
    if sys.version_info < (3, 10):
        print("ERROR: Python 3.10+ is required", file=sys.stderr)
        return 1

    args = parse_args()
    workload = args.workload.expanduser().resolve()

    if not workload.exists():
        print(f"ERROR: workload file not found: {workload}", file=sys.stderr)
        return 1

    expected = count_pods_in_manifest(workload)
    if expected == 0:
        print(f"ERROR: no Pod objects found in {workload}", file=sys.stderr)
        return 1

    experiment_name = args.experiment_name or workload.stem
    raw_dir = args.raw_root.expanduser().resolve() / experiment_name

    print("Running experiment")
    print(f"Experiment: {experiment_name}")
    print(f"Namespace: {args.namespace}")
    print(f"Workload: {workload}")
    print(f"Expected pods: {expected}")
    print(f"Raw output: {raw_dir}")
    print()

    check_kubectl()
    ensure_namespace(args.namespace)

    if args.skip_cleanup:
        print("[WARN] old pods cleanup skipped")
    else:
        delete_old_pods(args.namespace, args.timeout)

    run_checked(["kubectl", "apply", "-f", str(workload)], timeout=300)
    print("[OK] workload applied")
    print()
    print("Waiting for scheduler...")

    stats, timed_out = wait_for_scheduling(args.namespace, expected, args.timeout)

    print()
    if timed_out:
        print("[WARN] scheduling wait timeout")
    else:
        print("[OK] scheduling stabilized")

    print("Final summary:")
    print(" ", stats_line(stats))

    save_raw_state(
        raw_dir=raw_dir,
        namespace=args.namespace,
        workload=workload,
        experiment_name=experiment_name,
        expected=expected,
        stats=stats,
        timed_out=timed_out,
    )
    print()

    return 2 if timed_out else 0


if __name__ == "__main__":
    raise SystemExit(main())