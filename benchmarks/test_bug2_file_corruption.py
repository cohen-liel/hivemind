#!/usr/bin/env python3
"""Test Bug #2: Thread-Safety & File Corruption in cross_project_memory.py

This test simulates multiple concurrent writers to the cross-project memory
to see if file corruption actually occurs.
"""

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cross_project_memory import CrossProjectMemory


def test_concurrent_writes_threading():
    """Test with actual threads (simulates multiple agent processes)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        errors = []
        corruption_count = 0

        def writer(thread_id: int, iterations: int):
            nonlocal corruption_count
            mem = CrossProjectMemory(tmpdir)
            for i in range(iterations):
                try:
                    mem.add_lesson(
                        project_id=f"project_{thread_id}",
                        category="test",
                        lesson=f"Thread {thread_id} lesson {i}: " + "x" * 100,
                        severity="info",
                    )
                except Exception as e:
                    errors.append(f"Thread {thread_id} iter {i}: {e}")

        # Spawn 10 threads writing simultaneously
        threads = []
        for t in range(10):
            thread = threading.Thread(target=writer, args=(t, 50))
            threads.append(thread)

        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - t0

        # Check if the file is valid JSON
        mem_file = Path(tmpdir) / "cross_project_memory.json"
        try:
            data = json.loads(mem_file.read_text())
            lesson_count = len(data.get("lessons", []))
            expected = 10 * 50  # 10 threads * 50 iterations
            print(f"Threading test: {lesson_count}/{expected} lessons saved ({elapsed:.2f}s)")
            if lesson_count < expected:
                print(
                    f"  ⚠️ LOST {expected - lesson_count} lessons ({(expected - lesson_count) / expected * 100:.1f}% loss)"
                )
            else:
                print("  ✅ All lessons saved")
        except json.JSONDecodeError as e:
            print(f"  ❌ FILE CORRUPTED: {e}")
            corruption_count += 1

        if errors:
            print(f"  ❌ {len(errors)} errors during writes:")
            for err in errors[:5]:
                print(f"    {err}")

        return corruption_count, len(errors)


def test_concurrent_writes_same_instance():
    """Test with same instance from multiple threads (the actual usage pattern)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mem = CrossProjectMemory(tmpdir)
        errors = []

        def writer(thread_id: int, iterations: int):
            for i in range(iterations):
                try:
                    mem.add_lesson(
                        project_id=f"project_{thread_id}",
                        category="test",
                        lesson=f"Thread {thread_id} lesson {i}: " + "x" * 100,
                        severity="info",
                    )
                except Exception as e:
                    errors.append(f"Thread {thread_id} iter {i}: {e}")

        threads = []
        for t in range(10):
            thread = threading.Thread(target=writer, args=(t, 50))
            threads.append(thread)

        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - t0

        # Check results
        lesson_count = len(mem._data.get("lessons", []))
        expected = 10 * 50

        # Also check disk
        mem_file = Path(tmpdir) / "cross_project_memory.json"
        try:
            disk_data = json.loads(mem_file.read_text())
            disk_count = len(disk_data.get("lessons", []))
        except json.JSONDecodeError:
            disk_count = -1  # Corrupted

        print(
            f"\nSame-instance test: memory={lesson_count}, disk={disk_count}, expected={expected} ({elapsed:.2f}s)"
        )
        if lesson_count < expected:
            print(
                f"  ⚠️ LOST {expected - lesson_count} lessons in memory ({(expected - lesson_count) / expected * 100:.1f}% loss)"
            )
        if disk_count >= 0 and disk_count < expected:
            print(
                f"  ⚠️ LOST {expected - disk_count} lessons on disk ({(expected - disk_count) / expected * 100:.1f}% loss)"
            )
        elif disk_count < 0:
            print("  ❌ DISK FILE CORRUPTED")

        if errors:
            print(f"  ❌ {len(errors)} errors during writes")

        return lesson_count, disk_count, expected


def test_read_write_race():
    """Test read-while-write race condition."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mem = CrossProjectMemory(tmpdir)
        errors = []
        read_errors = []

        def writer():
            for i in range(100):
                try:
                    mem.add_lesson(
                        project_id="writer",
                        category="test",
                        lesson=f"Lesson {i}: " + "x" * 200,
                        severity="info",
                    )
                except Exception as e:
                    errors.append(f"Write {i}: {e}")

        def reader():
            for i in range(100):
                try:
                    mem.build_context_for_task("test task", max_tokens=5000)
                    # Just verify it doesn't crash
                except Exception as e:
                    read_errors.append(f"Read {i}: {e}")

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)

        t_write.start()
        t_read.start()
        t_write.join()
        t_read.join()

        print(f"\nRead-write race test: {len(errors)} write errors, {len(read_errors)} read errors")
        if errors:
            print(f"  Write errors: {errors[:3]}")
        if read_errors:
            print(f"  Read errors: {read_errors[:3]}")
        if not errors and not read_errors:
            print("  ✅ No errors (but data loss may still occur)")


if __name__ == "__main__":
    print("=" * 60)
    print("Bug #2: Thread-Safety & File Corruption Test")
    print("=" * 60)

    print("\n--- Test 1: Multiple instances writing concurrently ---")
    corruption, write_errors = test_concurrent_writes_threading()

    print("\n--- Test 2: Same instance from multiple threads ---")
    mem_count, disk_count, expected = test_concurrent_writes_same_instance()

    print("\n--- Test 3: Read-write race condition ---")
    test_read_write_race()

    print("\n" + "=" * 60)
    print("SUMMARY:")
    if corruption > 0:
        print("  ❌ FILE CORRUPTION DETECTED")
    if mem_count < expected:
        print(f"  ⚠️ DATA LOSS: {expected - mem_count} lessons lost in memory")
    if disk_count >= 0 and disk_count < expected:
        print(f"  ⚠️ DATA LOSS: {expected - disk_count} lessons lost on disk")
    if corruption == 0 and mem_count == expected and disk_count == expected:
        print("  ✅ No issues detected (may need more iterations)")
    print("=" * 60)
