#!/usr/bin/env python3
"""Test OSS Integration #9: ChromaDB Vector Memory vs Flat JSON

Benchmarks:
1. Write performance: adding lessons
2. Read performance: retrieving relevant lessons
3. Relevance quality: does vector search find better matches?
4. Scale: how does each approach handle 1000+ lessons?
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cross_project_memory import CrossProjectMemory

# Sample lessons that simulate real cross-project knowledge
SAMPLE_LESSONS = [
    ("auth", "Always hash passwords with bcrypt, never store plaintext", ["python", "security"]),
    ("api", "Use pagination for list endpoints to avoid memory issues", ["python", "fastapi"]),
    ("db", "Add database indexes on foreign keys for JOIN performance", ["postgresql", "sql"]),
    ("testing", "Mock external API calls in unit tests to avoid flaky tests", ["python", "pytest"]),
    ("docker", "Use multi-stage builds to reduce Docker image size by 80%", ["docker", "devops"]),
    (
        "react",
        "Use React.memo() for expensive components to prevent re-renders",
        ["react", "frontend"],
    ),
    ("css", "Use CSS Grid for 2D layouts and Flexbox for 1D layouts", ["css", "frontend"]),
    ("git", "Use conventional commits for automated changelog generation", ["git", "devops"]),
    (
        "security",
        "Validate and sanitize all user inputs to prevent SQL injection",
        ["python", "security"],
    ),
    (
        "perf",
        "Use connection pooling for database connections in production",
        ["postgresql", "python"],
    ),
    (
        "error",
        "Implement structured logging with correlation IDs for debugging",
        ["python", "observability"],
    ),
    (
        "cache",
        "Use Redis for session storage and caching in distributed systems",
        ["redis", "python"],
    ),
    (
        "deploy",
        "Blue-green deployments minimize downtime during releases",
        ["devops", "kubernetes"],
    ),
    (
        "api_design",
        "Use versioned API endpoints (v1, v2) for backward compatibility",
        ["api", "rest"],
    ),
    (
        "monitoring",
        "Set up alerts for error rate spikes, not just server metrics",
        ["devops", "observability"],
    ),
    (
        "typescript",
        "Use strict TypeScript config to catch null reference errors at compile time",
        ["typescript", "frontend"],
    ),
    (
        "database",
        "Use database transactions for multi-table updates to ensure consistency",
        ["sql", "python"],
    ),
    (
        "testing_e2e",
        "Run E2E tests in CI but keep them separate from unit tests for speed",
        ["testing", "ci"],
    ),
    (
        "auth_jwt",
        "Set short JWT expiry times and use refresh tokens for security",
        ["security", "jwt"],
    ),
    (
        "python_async",
        "Use asyncio.gather for concurrent I/O operations instead of sequential awaits",
        ["python", "async"],
    ),
]

# Queries to test relevance
RELEVANCE_QUERIES = [
    ("How should I handle user authentication securely?", ["auth", "security", "auth_jwt"]),
    ("My database queries are slow, what should I do?", ["db", "perf", "database"]),
    ("How to write good tests for my Python API?", ["testing", "testing_e2e"]),
    ("Best practices for deploying to production?", ["deploy", "docker", "monitoring"]),
    ("How to structure a React frontend?", ["react", "css", "typescript"]),
]


def benchmark_flat_json(num_lessons: int = 100):
    """Benchmark the current flat JSON approach."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mem = CrossProjectMemory(tmpdir)

        # Write benchmark
        t0 = time.time()
        for i in range(num_lessons):
            lesson = SAMPLE_LESSONS[i % len(SAMPLE_LESSONS)]
            mem.add_lesson(
                project_id=f"project_{i // 20}",
                category=lesson[0],
                lesson=f"{lesson[1]} (instance {i})",
                tech_stack=lesson[2],
                severity="info",
            )
        write_time = time.time() - t0

        # Read benchmark (build_context_for_task)
        t0 = time.time()
        for query, _ in RELEVANCE_QUERIES:
            context = mem.build_context_for_task(query, max_tokens=2000)
        read_time = time.time() - t0

        # Check file size
        file_size = (Path(tmpdir) / "cross_project_memory.json").stat().st_size

        # Relevance check: does the context contain relevant lessons?
        relevance_scores = []
        for query, expected_categories in RELEVANCE_QUERIES:
            context = mem.build_context_for_task(query, max_tokens=2000)
            # Count how many expected categories appear in the context
            found = sum(1 for cat in expected_categories if cat in context.lower())
            relevance_scores.append(found / len(expected_categories))

        avg_relevance = sum(relevance_scores) / len(relevance_scores)

        return {
            "write_time": write_time,
            "read_time": read_time,
            "file_size": file_size,
            "avg_relevance": avg_relevance,
            "num_lessons": num_lessons,
        }


def benchmark_chromadb(num_lessons: int = 100):
    """Benchmark ChromaDB vector approach."""
    import chromadb

    with tempfile.TemporaryDirectory() as tmpdir:
        client = chromadb.PersistentClient(path=tmpdir)
        collection = client.get_or_create_collection(
            name="cross_project_memory",
            metadata={"hnsw:space": "cosine"},
        )

        # Write benchmark
        t0 = time.time()
        documents = []
        metadatas = []
        ids = []
        for i in range(num_lessons):
            lesson = SAMPLE_LESSONS[i % len(SAMPLE_LESSONS)]
            doc = f"[{lesson[0].upper()}] {lesson[1]} (instance {i})"
            documents.append(doc)
            metadatas.append(
                {
                    "project_id": f"project_{i // 20}",
                    "category": lesson[0],
                    "tech_stack": ",".join(lesson[2]),
                    "severity": "info",
                }
            )
            ids.append(f"lesson_{i}")

        # Batch add (ChromaDB supports batch operations)
        batch_size = 100
        for start in range(0, len(documents), batch_size):
            end = min(start + batch_size, len(documents))
            collection.add(
                documents=documents[start:end],
                metadatas=metadatas[start:end],
                ids=ids[start:end],
            )
        write_time = time.time() - t0

        # Read benchmark (query for relevant lessons)
        t0 = time.time()
        for query, _ in RELEVANCE_QUERIES:
            results = collection.query(
                query_texts=[query],
                n_results=5,
            )
        read_time = time.time() - t0

        # Check storage size
        dir_size = sum(f.stat().st_size for f in Path(tmpdir).rglob("*") if f.is_file())

        # Relevance check
        relevance_scores = []
        for query, expected_categories in RELEVANCE_QUERIES:
            results = collection.query(
                query_texts=[query],
                n_results=5,
            )
            # Check if returned documents match expected categories
            found = 0
            for doc in results["documents"][0]:
                for cat in expected_categories:
                    if cat.upper() in doc.upper() or cat.lower() in doc.lower():
                        found += 1
                        break
            relevance_scores.append(min(found / len(expected_categories), 1.0))

        avg_relevance = sum(relevance_scores) / len(relevance_scores)

        return {
            "write_time": write_time,
            "read_time": read_time,
            "file_size": dir_size,
            "avg_relevance": avg_relevance,
            "num_lessons": num_lessons,
        }


def run_benchmark(num_lessons: int):
    """Run both benchmarks and compare."""
    print(f"\n{'=' * 60}")
    print(f"Benchmark: {num_lessons} lessons")
    print(f"{'=' * 60}")

    json_results = benchmark_flat_json(num_lessons)
    chroma_results = benchmark_chromadb(num_lessons)

    print(f"\n{'Metric':<25} {'Flat JSON':>15} {'ChromaDB':>15} {'Winner':>10}")
    print(f"{'-' * 65}")

    # Write time
    json_w = json_results["write_time"]
    chroma_w = chroma_results["write_time"]
    winner_w = "JSON" if json_w < chroma_w else "ChromaDB"
    print(f"{'Write time':<25} {json_w:>14.3f}s {chroma_w:>14.3f}s {winner_w:>10}")

    # Read time
    json_r = json_results["read_time"]
    chroma_r = chroma_results["read_time"]
    winner_r = "JSON" if json_r < chroma_r else "ChromaDB"
    print(f"{'Read time (5 queries)':<25} {json_r:>14.3f}s {chroma_r:>14.3f}s {winner_r:>10}")

    # Storage size
    json_s = json_results["file_size"]
    chroma_s = chroma_results["file_size"]
    winner_s = "JSON" if json_s < chroma_s else "ChromaDB"
    print(f"{'Storage size':<25} {json_s:>13,}B {chroma_s:>13,}B {winner_s:>10}")

    # Relevance
    json_rel = json_results["avg_relevance"]
    chroma_rel = chroma_results["avg_relevance"]
    winner_rel = "JSON" if json_rel > chroma_rel else "ChromaDB" if chroma_rel > json_rel else "TIE"
    print(f"{'Relevance score':<25} {json_rel:>14.1%} {chroma_rel:>14.1%} {winner_rel:>10}")

    return json_results, chroma_results


if __name__ == "__main__":
    print("=" * 60)
    print("ChromaDB vs Flat JSON Memory Benchmark")
    print("=" * 60)

    # Test at different scales
    all_results = {}
    for n in [20, 100, 500, 1000]:
        json_r, chroma_r = run_benchmark(n)
        all_results[n] = {"json": json_r, "chroma": chroma_r}

    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    print("\nScale analysis (write time ratio: ChromaDB/JSON):")
    for n, results in all_results.items():
        ratio = results["chroma"]["write_time"] / max(results["json"]["write_time"], 0.001)
        print(
            f"  {n:>5} lessons: ChromaDB is {ratio:.1f}x {'slower' if ratio > 1 else 'faster'} for writes"
        )

    print("\nRelevance comparison:")
    for n, results in all_results.items():
        json_rel = results["json"]["avg_relevance"]
        chroma_rel = results["chroma"]["avg_relevance"]
        diff = chroma_rel - json_rel
        print(
            f"  {n:>5} lessons: JSON={json_rel:.1%}, ChromaDB={chroma_rel:.1%} (diff={diff:+.1%})"
        )
