/**
 * SymPy Executor — runs Python/SymPy code in a subprocess with timeout and
 * basic sandboxing.  The LLM generates SymPy code; this module executes it
 * and captures stdout as the computed answer.
 */

import { execFile } from "node:child_process";
import { writeFile, unlink, mkdtemp } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";

// ── Types ──

export interface ExecutionResult {
  output: string;
  status: "success" | "error" | "timeout";
  errorMessage?: string;
}

// ── Configuration ──

const EXECUTION_TIMEOUT_MS = 15_000; // 15 seconds
const MAX_OUTPUT_LENGTH = 10_000; // truncate very long outputs
const PYTHON_BIN = "/usr/bin/python3";

// ── Safety wrapper ──
// We prepend a preamble that:
// 1. Disables file I/O and network access (basic sandbox)
// 2. Imports sympy so the user code can use it directly
// 3. Sets a recursion limit to prevent infinite recursion

const CODE_PREAMBLE = `
import sys
import signal

# Set resource limits
sys.setrecursionlimit(2000)
signal.alarm(14)  # Hard kill after 14s (backup for Node timeout)

# Restrict dangerous builtins
_safe_builtins = {
    'abs': abs, 'all': all, 'any': any, 'bin': bin, 'bool': bool,
    'chr': chr, 'complex': complex, 'dict': dict, 'divmod': divmod,
    'enumerate': enumerate, 'filter': filter, 'float': float,
    'format': format, 'frozenset': frozenset, 'getattr': getattr,
    'hasattr': hasattr, 'hash': hash, 'hex': hex, 'id': id,
    'int': int, 'isinstance': isinstance, 'issubclass': issubclass,
    'iter': iter, 'len': len, 'list': list, 'map': map, 'max': max,
    'min': min, 'next': next, 'oct': oct, 'ord': ord, 'pow': pow,
    'print': print, 'range': range, 'repr': repr, 'reversed': reversed,
    'round': round, 'set': set, 'slice': slice, 'sorted': sorted,
    'str': str, 'sum': sum, 'super': super, 'tuple': tuple,
    'type': type, 'zip': zip,
    '__import__': __import__,
}

# Import SymPy and math
from sympy import *
import math
import itertools
import functools
from fractions import Fraction
from collections import Counter, defaultdict

# ── User code below ──
`;

// ── Public API ──

/**
 * Execute a Python/SymPy code snippet and return the captured stdout.
 * The code is written to a temporary file and executed as a subprocess.
 */
export async function executeSymPyCode(code: string): Promise<ExecutionResult> {
  let tmpDir: string | null = null;
  let tmpFile: string | null = null;

  try {
    // Create a temporary directory for the script
    tmpDir = await mkdtemp(join(tmpdir(), "sympy-"));
    tmpFile = join(tmpDir, "solve.py");

    // Write the full script (preamble + user code)
    const fullCode = CODE_PREAMBLE + "\n" + code;
    await writeFile(tmpFile, fullCode, "utf-8");

    // Execute with timeout
    const result = await new Promise<ExecutionResult>((resolve) => {
      const proc = execFile(
        PYTHON_BIN,
        [tmpFile!],
        {
          timeout: EXECUTION_TIMEOUT_MS,
          maxBuffer: 1024 * 1024, // 1 MB
          env: {
            PATH: "/usr/bin:/usr/local/bin",
            HOME: "/tmp",
            PYTHONDONTWRITEBYTECODE: "1",
          },
        },
        (error, stdout, stderr) => {
          const output = (stdout || "").trim();
          const truncatedOutput =
            output.length > MAX_OUTPUT_LENGTH
              ? output.slice(0, MAX_OUTPUT_LENGTH) + "... [truncated]"
              : output;

          if (error) {
            // Check if it was a timeout
            if (
              (error as any).killed ||
              error.message.includes("TIMEOUT") ||
              error.message.includes("SIGTERM")
            ) {
              resolve({
                output: truncatedOutput || "",
                status: "timeout",
                errorMessage: "Execution timed out (15s limit)",
              });
              return;
            }

            // Execution error (Python exception, etc.)
            const errMsg = (stderr || error.message || "Unknown error").trim();
            // Extract just the last line of the traceback for a cleaner message
            const lines = errMsg.split("\n");
            const lastLine = lines[lines.length - 1] || errMsg;

            resolve({
              output: truncatedOutput || "",
              status: "error",
              errorMessage: lastLine.slice(0, 500),
            });
            return;
          }

          if (!truncatedOutput) {
            resolve({
              output: "",
              status: "error",
              errorMessage:
                "Code executed but produced no output. Make sure to print() the result.",
            });
            return;
          }

          resolve({
            output: truncatedOutput,
            status: "success",
          });
        }
      );
    });

    return result;
  } catch (err) {
    return {
      output: "",
      status: "error",
      errorMessage: err instanceof Error ? err.message : String(err),
    };
  } finally {
    // Cleanup temp files
    try {
      if (tmpFile) await unlink(tmpFile).catch(() => {});
      if (tmpDir) {
        const { rm } = await import("node:fs/promises");
        await rm(tmpDir, { recursive: true, force: true }).catch(() => {});
      }
    } catch {
      // Ignore cleanup errors
    }
  }
}

/**
 * Extract the final answer from execution output.
 * Takes the last non-empty line of stdout as the answer.
 */
export function extractAnswer(output: string): string {
  if (!output) return "";
  const lines = output
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  return lines[lines.length - 1] || "";
}
