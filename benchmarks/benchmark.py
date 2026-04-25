"""
benchmark.py — Multi-tenant performance benchmarking suite.

Measures and compares all three tenancy models under varying concurrent loads.

Usage:
    python -m benchmarks.benchmark [--tenants 10,100,500,1000] [--iterations 50]

Output:
    benchmarks/results/benchmark_<timestamp>.csv
    benchmarks/results/latency_vs_tenants.png
    benchmarks/results/throughput_comparison.png
    benchmarks/results/operation_heatmap.png

Metrics collected per run:
    - Query latency: p50, p95, p99 (ms)
    - Throughput: queries / second
    - Provisioning time (ms)
    - Cache hit vs miss latency ratio
"""
import argparse
import csv
import logging
import os
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import numpy as np
import matplotlib
matplotlib.use("Agg")  # headless rendering
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import psycopg2

from provisioning.config import Config
from provisioning.database import get_connection
from benchmarks.seed import seed_tenants, SeededTenant
from cache.redis_client import cache_get, cache_set, cache_invalidate_tenant

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# Operations exercised during each benchmark iteration
_OPERATIONS = ["select_orders", "select_products", "count_orders", "filter_by_status"]


# ── Data structures ───────────────────────────────────────────────────────────

class LatencyResult(NamedTuple):
    operation:    str
    model:        str
    tenant_count: int
    latency_ms:   float
    cached:       bool
    success:      bool


class BenchmarkSummary(NamedTuple):
    model:        str
    tenant_count: int
    operation:    str
    p50_ms:       float
    p95_ms:       float
    p99_ms:       float
    throughput:   float   # queries / second
    cached_p50:   float
    uncached_p50: float
    cache_speedup: float  # uncached_p50 / cached_p50


# ── Query workload ────────────────────────────────────────────────────────────

def _run_query(
    tenant: SeededTenant,
    operation: str,
    use_cache: bool,
) -> float:
    """
    Execute one benchmark query for the given tenant + operation.
    Returns elapsed time in milliseconds.
    Raises on error (counted as failure by caller).
    """
    cache_key = f"tenant:{tenant.tenant_id}:query:{operation}"

    if use_cache:
        cached = cache_get(cache_key)
        if cached is not None:
            return _timed_cache_hit(cache_key)

    start = time.perf_counter()

    with get_connection(
        tenant_id   = tenant.tenant_id if tenant.model != "db_per_tenant" else None,
        db_name     = tenant.db_name,
        schema_name = tenant.schema_name,
    ) as conn:
        with conn.cursor() as cur:
            if tenant.model == "shared_schema":
                cur.execute("SET LOCAL app.current_tenant = %s", (tenant.tenant_id,))

            if operation == "select_orders":
                cur.execute(
                    _model_sql(tenant.model,
                               "SELECT order_id, status, total_amount, created_at "
                               "FROM orders ORDER BY created_at DESC LIMIT 20",
                               tenant.tenant_id)
                )
                cur.fetchall()

            elif operation == "select_products":
                cur.execute(
                    _model_sql(tenant.model,
                               "SELECT product_id, name, price, sku "
                               "FROM products WHERE is_active = TRUE LIMIT 20",
                               tenant.tenant_id)
                )
                cur.fetchall()

            elif operation == "count_orders":
                cur.execute(
                    _model_sql(tenant.model,
                               "SELECT COUNT(*) FROM orders",
                               tenant.tenant_id)
                )
                cur.fetchone()

            elif operation == "filter_by_status":
                cur.execute(
                    _model_sql(tenant.model,
                               "SELECT order_id, total_amount FROM orders "
                               "WHERE status = 'pending' LIMIT 10",
                               tenant.tenant_id)
                )
                cur.fetchall()

    elapsed_ms = (time.perf_counter() - start) * 1000

    if use_cache:
        cache_set(cache_key, {"result": "cached", "op": operation}, ttl=Config.CACHE_TTL)

    return elapsed_ms


def _timed_cache_hit(cache_key: str) -> float:
    """Measure the latency of a pure Redis GET (cache hit path)."""
    start  = time.perf_counter()
    _      = cache_get(cache_key)
    return (time.perf_counter() - start) * 1000


def _model_sql(model: str, base_sql: str, tenant_id: str) -> str:
    """
    For shared_schema, RLS handles the WHERE; for schema/db isolation
    the search_path / separate DB already scopes the query.
    No extra WHERE needed — RLS via SET LOCAL is already active.
    """
    return base_sql


# ── Provisioning benchmark ────────────────────────────────────────────────────

def _benchmark_provisioning(
    model: str,
    count: int,
    base_slug: str,
) -> list[float]:
    """Time the provisioning of `count` tenants for a single model."""
    from provisioning import provisioner

    times: list[float] = []
    for i in range(count):
        slug = f"prov-bench-{model[:3]}-{uuid.uuid4().hex[:6]}"
        start = time.perf_counter()
        try:
            t = provisioner.provision_tenant(
                name           = f"Prov Test {slug}",
                slug           = slug,
                tier           = "free" if model == "shared_schema" else (
                                 "pro" if model == "schema_per_tenant" else "enterprise"),
                model          = model,
                admin_email    = f"admin@{slug}.test",
                admin_password = "provbench123",
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)
            # Clean up immediately
            provisioner.deactivate_tenant(t["tenant_id"])
        except Exception as exc:
            logger.warning("Provisioning bench failed for %s: %s", slug, exc)
    return times


# ── Core benchmark runner ─────────────────────────────────────────────────────

def run_benchmark(
    tenant_counts:  list[int],
    iterations:     int,
    include_cache:  bool = True,
) -> list[BenchmarkSummary]:
    """
    Main entry point.  For each (tenant_count, model, operation) combination:
      1. Seeds exactly tenant_count tenants (idempotent)
      2. Issues `iterations` concurrent queries per operation
      3. Computes p50/p95/p99/throughput
      4. Repeats with Redis caching enabled
    """
    all_results: list[LatencyResult] = []
    summaries:   list[BenchmarkSummary] = []

    for tenant_count in tenant_counts:
        logger.info("=== Benchmarking %d tenants ===", tenant_count)

        tenants = seed_tenants(tenant_count)
        if not tenants:
            logger.error("No tenants seeded for count=%d — skipping.", tenant_count)
            continue

        # Group tenants by model for per-model analysis
        by_model: dict[str, list[SeededTenant]] = {}
        for t in tenants:
            by_model.setdefault(t.model, []).append(t)

        for model, model_tenants in by_model.items():
            for operation in _OPERATIONS:
                logger.info("  model=%-20s op=%-20s tenants=%d",
                            model, operation, len(model_tenants))

                # Invalidate cache before uncached run
                for t in model_tenants:
                    cache_invalidate_tenant(t.tenant_id)

                # ── Uncached run ───────────────────────────────────────────
                uncached_times = _concurrent_run(
                    model_tenants, operation, iterations,
                    use_cache=False,
                )

                # ── Cached run ─────────────────────────────────────────────
                cached_times: list[float] = []
                if include_cache:
                    # Prime the cache with one pass
                    _concurrent_run(model_tenants, operation, 1, use_cache=True)
                    cached_times = _concurrent_run(
                        model_tenants, operation, iterations,
                        use_cache=True,
                    )

                for ms in uncached_times:
                    all_results.append(LatencyResult(
                        operation, model, tenant_count, ms, False, True
                    ))
                for ms in cached_times:
                    all_results.append(LatencyResult(
                        operation, model, tenant_count, ms, True, True
                    ))

                if not uncached_times:
                    continue

                p50  = statistics.median(uncached_times)
                p95  = float(np.percentile(uncached_times, 95))
                p99  = float(np.percentile(uncached_times, 99))
                tput = len(uncached_times) / (sum(uncached_times) / 1000)

                cp50 = statistics.median(cached_times) if cached_times else p50
                speedup = p50 / cp50 if cp50 > 0 else 1.0

                summaries.append(BenchmarkSummary(
                    model        = model,
                    tenant_count = tenant_count,
                    operation    = operation,
                    p50_ms       = round(p50,  2),
                    p95_ms       = round(p95,  2),
                    p99_ms       = round(p99,  2),
                    throughput   = round(tput, 1),
                    cached_p50   = round(cp50, 2),
                    uncached_p50 = round(p50,  2),
                    cache_speedup= round(speedup, 2),
                ))

                logger.info(
                    "    p50=%.1f ms  p95=%.1f ms  p99=%.1f ms  "
                    "tput=%.0f q/s  cache_speedup=%.1fx",
                    p50, p95, p99, tput, speedup,
                )

    return summaries


def _concurrent_run(
    tenants:    list[SeededTenant],
    operation:  str,
    iterations: int,
    use_cache:  bool,
) -> list[float]:
    """
    Fire `iterations` queries across `tenants` using a thread pool.
    Each thread picks a tenant from the list round-robin.
    Returns list of latencies (ms) for successful calls only.
    """
    times: list[float] = []
    tasks = [
        (tenants[i % len(tenants)], operation, use_cache)
        for i in range(iterations)
    ]

    max_workers = min(32, iterations)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_run_query, tenant, op, cached): i
            for i, (tenant, op, cached) in enumerate(tasks)
        }
        for future in as_completed(futures):
            try:
                times.append(future.result())
            except Exception as exc:
                logger.debug("Query failed: %s", exc)

    return times


# ── Provisioning benchmark (separate) ────────────────────────────────────────

def run_provisioning_benchmark(count_per_model: int = 5) -> dict[str, dict]:
    """Measure provisioning latency for all three models."""
    results = {}
    for model in ("shared_schema", "schema_per_tenant", "db_per_tenant"):
        times = _benchmark_provisioning(model, count_per_model, "provbench")
        if times:
            results[model] = {
                "count":  len(times),
                "p50_ms": round(statistics.median(times), 1),
                "p95_ms": round(float(np.percentile(times, 95)), 1),
                "min_ms": round(min(times), 1),
                "max_ms": round(max(times), 1),
            }
            logger.info(
                "Provisioning [%s]: p50=%.0f ms  p95=%.0f ms  min=%.0f ms  max=%.0f ms",
                model, results[model]["p50_ms"], results[model]["p95_ms"],
                results[model]["min_ms"], results[model]["max_ms"],
            )
    return results


# ── CSV export ────────────────────────────────────────────────────────────────

def export_csv(summaries: list[BenchmarkSummary], timestamp: str) -> Path:
    path = RESULTS_DIR / f"benchmark_{timestamp}.csv"
    fieldnames = list(BenchmarkSummary._fields)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for s in summaries:
            writer.writerow(s._asdict())
    logger.info("CSV written → %s", path)
    return path


# ── Matplotlib chart generators ───────────────────────────────────────────────

MODEL_COLORS = {
    "shared_schema":    "#2196F3",   # blue
    "schema_per_tenant":"#4CAF50",   # green
    "db_per_tenant":    "#FF9800",   # orange
}
MODEL_LABELS = {
    "shared_schema":    "Model A (Shared Schema)",
    "schema_per_tenant":"Model B (Schema-Per-Tenant)",
    "db_per_tenant":    "Model C (DB-Per-Tenant)",
}


def _filter_summaries(
    summaries: list[BenchmarkSummary],
    operation: str = "select_orders",
) -> dict[str, dict[int, BenchmarkSummary]]:
    """Return {model: {tenant_count: summary}} for a given operation."""
    out: dict[str, dict[int, BenchmarkSummary]] = {}
    for s in summaries:
        if s.operation != operation:
            continue
        out.setdefault(s.model, {})[s.tenant_count] = s
    return out


def chart_latency_vs_tenants(summaries: list[BenchmarkSummary], timestamp: str) -> Path:
    """
    Line chart: p50 / p95 / p99 latency vs tenant count, one line per model.
    Separate sub-plot per percentile.
    """
    fig, axes = plt.subplots(1, 3, figsize=(18, 5), sharey=False)
    fig.suptitle("Query Latency vs Tenant Count (operation: select_orders)",
                 fontsize=14, fontweight="bold")

    by_model = _filter_summaries(summaries, "select_orders")
    percentiles = [("p50_ms", "P50"), ("p95_ms", "P95"), ("p99_ms", "P99")]

    for ax, (field, label) in zip(axes, percentiles):
        for model, data_by_count in by_model.items():
            counts  = sorted(data_by_count.keys())
            latency = [getattr(data_by_count[c], field) for c in counts]
            ax.plot(
                counts, latency,
                marker="o", linewidth=2,
                color=MODEL_COLORS.get(model, "grey"),
                label=MODEL_LABELS.get(model, model),
            )
        ax.set_title(f"{label} Latency")
        ax.set_xlabel("Concurrent Tenants")
        ax.set_ylabel("Latency (ms)")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_xscale("log")

    plt.tight_layout()
    path = RESULTS_DIR / f"latency_vs_tenants_{timestamp}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("Chart written → %s", path)
    return path


def chart_throughput_comparison(summaries: list[BenchmarkSummary], timestamp: str) -> Path:
    """
    Grouped bar chart: throughput (q/s) per model × operation, at max tenant count.
    """
    max_count = max(s.tenant_count for s in summaries)
    ops       = _OPERATIONS
    models    = list(MODEL_COLORS.keys())

    data = {
        model: [
            next(
                (s.throughput for s in summaries
                 if s.model == model and s.operation == op
                 and s.tenant_count == max_count),
                0,
            )
            for op in ops
        ]
        for model in models
    }

    x     = np.arange(len(ops))
    width = 0.25
    fig, ax = plt.subplots(figsize=(12, 6))

    for i, model in enumerate(models):
        bars = ax.bar(
            x + i * width, data[model], width,
            label=MODEL_LABELS.get(model, model),
            color=MODEL_COLORS[model], alpha=0.85,
        )
        for bar in bars:
            h = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2, h + 1,
                f"{h:.0f}", ha="center", va="bottom", fontsize=8,
            )

    ax.set_title(f"Throughput Comparison (queries/sec) @ {max_count} tenants",
                 fontsize=13, fontweight="bold")
    ax.set_ylabel("Queries / Second")
    ax.set_xticks(x + width)
    ax.set_xticklabels([op.replace("_", "\n") for op in ops])
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    path = RESULTS_DIR / f"throughput_comparison_{timestamp}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("Chart written → %s", path)
    return path


def chart_operation_heatmap(summaries: list[BenchmarkSummary], timestamp: str) -> Path:
    """
    Heatmap: p50 latency indexed by (tenant_count × operation) per model.
    One heatmap per tenancy model, arranged in a row.
    """
    models  = [m for m in MODEL_COLORS if any(s.model == m for s in summaries)]
    ops     = _OPERATIONS
    counts  = sorted({s.tenant_count for s in summaries})

    fig, axes = plt.subplots(1, len(models), figsize=(6 * len(models), 5))
    if len(models) == 1:
        axes = [axes]

    fig.suptitle("P50 Latency Heatmap (ms) — Tenant Count × Operation",
                 fontsize=13, fontweight="bold")

    for ax, model in zip(axes, models):
        matrix = np.zeros((len(counts), len(ops)))
        for i, count in enumerate(counts):
            for j, op in enumerate(ops):
                match = next(
                    (s.p50_ms for s in summaries
                     if s.model == model and s.tenant_count == count
                     and s.operation == op),
                    0,
                )
                matrix[i, j] = match

        im = ax.imshow(matrix, cmap="YlOrRd", aspect="auto")
        ax.set_title(MODEL_LABELS.get(model, model), fontsize=10)
        ax.set_xticks(range(len(ops)))
        ax.set_xticklabels([o.replace("_", "\n") for o in ops], fontsize=8)
        ax.set_yticks(range(len(counts)))
        ax.set_yticklabels(counts)
        ax.set_ylabel("Tenant Count")
        plt.colorbar(im, ax=ax, label="ms")

        for i in range(len(counts)):
            for j in range(len(ops)):
                ax.text(j, i, f"{matrix[i,j]:.0f}",
                        ha="center", va="center", fontsize=8,
                        color="black" if matrix[i, j] < matrix.max() * 0.6 else "white")

    plt.tight_layout()
    path = RESULTS_DIR / f"operation_heatmap_{timestamp}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("Chart written → %s", path)
    return path


def chart_cache_comparison(summaries: list[BenchmarkSummary], timestamp: str) -> Path:
    """
    Bar chart: cached vs uncached p50 latency for each model × operation.
    """
    ops    = _OPERATIONS
    models = list(MODEL_COLORS.keys())

    fig, axes = plt.subplots(1, len(ops), figsize=(5 * len(ops), 5), sharey=False)
    fig.suptitle("Cached vs Uncached P50 Latency (ms)", fontsize=13, fontweight="bold")

    for ax, op in zip(axes, ops):
        x     = np.arange(len(models))
        width = 0.35
        model_labels = [MODEL_LABELS.get(m, m).split("(")[0].strip() for m in models]

        uncached = []
        cached   = []
        for model in models:
            match = next(
                (s for s in summaries if s.model == model and s.operation == op),
                None,
            )
            uncached.append(match.uncached_p50 if match else 0)
            cached.append(  match.cached_p50   if match else 0)

        bars_u = ax.bar(x - width / 2, uncached, width, label="Uncached",
                        color="#EF5350", alpha=0.85)
        bars_c = ax.bar(x + width / 2, cached,   width, label="Cached (Redis)",
                        color="#66BB6A", alpha=0.85)

        for bars in (bars_u, bars_c):
            for bar in bars:
                h = bar.get_height()
                if h > 0:
                    ax.text(bar.get_x() + bar.get_width() / 2, h + 0.3,
                            f"{h:.1f}", ha="center", va="bottom", fontsize=7)

        ax.set_title(op.replace("_", " ").title(), fontsize=9)
        ax.set_xticks(x)
        ax.set_xticklabels(model_labels, fontsize=7, rotation=15, ha="right")
        ax.set_ylabel("P50 Latency (ms)")
        ax.legend(fontsize=7)
        ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    path = RESULTS_DIR / f"cache_comparison_{timestamp}.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info("Chart written → %s", path)
    return path


# ── Print summary table ───────────────────────────────────────────────────────

def print_summary_table(summaries: list[BenchmarkSummary]) -> None:
    try:
        from tabulate import tabulate
        rows = [list(s) for s in summaries]
        headers = list(BenchmarkSummary._fields)
        print("\n" + tabulate(rows, headers=headers, tablefmt="rounded_outline",
                              floatfmt=".1f"))
    except ImportError:
        for s in summaries:
            print(s)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Multi-tenant benchmark suite")
    parser.add_argument(
        "--tenants", default="10,100,500,1000",
        help="Comma-separated tenant counts (default: 10,100,500,1000)"
    )
    parser.add_argument(
        "--iterations", type=int, default=50,
        help="Query iterations per (model, operation, count) cell (default: 50)"
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Skip the Redis caching measurement pass"
    )
    parser.add_argument(
        "--prov-count", type=int, default=5,
        help="Number of provisioning operations to time per model (default: 5)"
    )
    args = parser.parse_args()

    tenant_counts = [int(x.strip()) for x in args.tenants.split(",")]
    timestamp     = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"\n{'='*60}")
    print(f"  Multi-Tenant SaaS Benchmark  —  {timestamp}")
    print(f"  Tenant counts : {tenant_counts}")
    print(f"  Iterations    : {args.iterations}")
    print(f"  Cache enabled : {not args.no_cache}")
    print(f"{'='*60}\n")

    # ── Provisioning benchmark ─────────────────────────────────────────────
    print("Phase 0: Provisioning latency benchmark …")
    prov_results = run_provisioning_benchmark(args.prov_count)
    print("\nProvisioning latency:")
    for model, stats in prov_results.items():
        print(f"  {model:<25}  p50={stats['p50_ms']:.0f} ms  "
              f"p95={stats['p95_ms']:.0f} ms  "
              f"min={stats['min_ms']:.0f} ms  max={stats['max_ms']:.0f} ms")

    # ── Query benchmark ────────────────────────────────────────────────────
    print("\nPhase 1: Query latency benchmark …")
    summaries = run_benchmark(
        tenant_counts  = tenant_counts,
        iterations     = args.iterations,
        include_cache  = not args.no_cache,
    )

    # ── Reports ────────────────────────────────────────────────────────────
    print_summary_table(summaries)
    csv_path = export_csv(summaries, timestamp)

    print("\nGenerating charts …")
    chart_latency_vs_tenants(summaries, timestamp)
    chart_throughput_comparison(summaries, timestamp)
    chart_operation_heatmap(summaries, timestamp)
    if not args.no_cache:
        chart_cache_comparison(summaries, timestamp)

    print(f"\n✓ Results written to {RESULTS_DIR}")
    print(f"  CSV     : {csv_path.name}")
    print(f"  Charts  : latency_vs_tenants_{timestamp}.png")
    print(f"            throughput_comparison_{timestamp}.png")
    print(f"            operation_heatmap_{timestamp}.png")
    if not args.no_cache:
        print(f"            cache_comparison_{timestamp}.png")


if __name__ == "__main__":
    main()
