import { describe, expect, it, vi } from "vitest";
import { appRouter } from "./routers";
import type { TrpcContext } from "./_core/context";
import { executeSymPyCode, extractAnswer } from "./sympyExecutor";
import { majorityVote, type SolverPath } from "./mathSolver";

// ── Context Helpers ──

function createPublicContext(): TrpcContext {
  return {
    user: null,
    req: {
      protocol: "https",
      headers: {},
    } as TrpcContext["req"],
    res: {
      clearCookie: vi.fn(),
    } as unknown as TrpcContext["res"],
  };
}

// ── Router Input Validation ──

describe("math.solve input validation", () => {
  it("rejects empty question", async () => {
    const ctx = createPublicContext();
    const caller = appRouter.createCaller(ctx);
    await expect(
      caller.math.solve({ question: "", numPaths: 3 })
    ).rejects.toThrow();
  });

  it("rejects numPaths below minimum (1)", async () => {
    const ctx = createPublicContext();
    const caller = appRouter.createCaller(ctx);
    await expect(
      caller.math.solve({ question: "2+2", numPaths: 1 })
    ).rejects.toThrow();
  });

  it("rejects numPaths above maximum (11)", async () => {
    const ctx = createPublicContext();
    const caller = appRouter.createCaller(ctx);
    await expect(
      caller.math.solve({ question: "2+2", numPaths: 11 })
    ).rejects.toThrow();
  });
});

describe("math.history", () => {
  it("returns an array for unauthenticated user", async () => {
    const ctx = createPublicContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.math.history({ limit: 5 });
    expect(Array.isArray(result)).toBe(true);
  });

  it("accepts default input (no params)", async () => {
    const ctx = createPublicContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.math.history();
    expect(Array.isArray(result)).toBe(true);
  });
});

describe("math.getResult", () => {
  it("returns null for non-existent problem", async () => {
    const ctx = createPublicContext();
    const caller = appRouter.createCaller(ctx);
    const result = await caller.math.getResult({ problemId: 999999 });
    expect(result).toBeNull();
  });
});

// ── SymPy Executor (real Python execution) ──

describe("executeSymPyCode", () => {
  it("executes simple print and returns success", async () => {
    const result = await executeSymPyCode('print("hello world")');
    expect(result.status).toBe("success");
    expect(result.output).toBe("hello world");
  }, 20_000);

  it("executes SymPy solve and returns correct answer", async () => {
    const code = `
from sympy import symbols, solve
x = symbols('x')
solutions = solve(x**2 - 4, x)
print(', '.join(str(s) for s in sorted(solutions)))
`;
    const result = await executeSymPyCode(code);
    expect(result.status).toBe("success");
    expect(result.output).toBe("-2, 2");
  }, 20_000);

  it("executes SymPy factorial", async () => {
    const code = `
from sympy import factorial
result = factorial(10)
print(int(result))
`;
    const result = await executeSymPyCode(code);
    expect(result.status).toBe("success");
    expect(result.output).toBe("3628800");
  }, 20_000);

  it("returns error for invalid Python code", async () => {
    const result = await executeSymPyCode("this is not valid python");
    expect(result.status).toBe("error");
    expect(result.errorMessage).toBeTruthy();
  }, 20_000);

  it("returns error for code that produces no output", async () => {
    const result = await executeSymPyCode("x = 42");
    expect(result.status).toBe("error");
    expect(result.errorMessage).toContain("no output");
  }, 20_000);

  it("handles division by zero gracefully", async () => {
    const result = await executeSymPyCode("print(1/0)");
    expect(result.status).toBe("error");
    expect(result.errorMessage).toBeTruthy();
  }, 20_000);
});

// ── extractAnswer ──

describe("extractAnswer", () => {
  it("returns last non-empty line", () => {
    expect(extractAnswer("line1\nline2\nline3")).toBe("line3");
  });

  it("trims whitespace", () => {
    expect(extractAnswer("  42  \n  ")).toBe("42");
  });

  it("returns empty string for empty input", () => {
    expect(extractAnswer("")).toBe("");
  });

  it("handles single line", () => {
    expect(extractAnswer("42")).toBe("42");
  });

  it("skips blank lines", () => {
    expect(extractAnswer("first\n\n\nlast\n\n")).toBe("last");
  });
});

// ── Majority Voting ──

describe("majorityVote", () => {
  function makePath(
    index: number,
    answer: string,
    status: "success" | "error" | "timeout" = "success"
  ): SolverPath {
    return {
      pathIndex: index,
      generatedCode: "# code",
      executionOutput: answer,
      executionStatus: status,
      finalAnswer: answer,
    };
  }

  it("selects the most frequent answer", () => {
    const paths = [
      makePath(0, "42"),
      makePath(1, "42"),
      makePath(2, "43"),
      makePath(3, "42"),
      makePath(4, "43"),
    ];
    const result = majorityVote(paths);
    expect(result.answer).toBe("42");
    expect(result.confidence).toBe(60);
    expect(result.successCount).toBe(5);
  });

  it("handles unanimous agreement", () => {
    const paths = [
      makePath(0, "7"),
      makePath(1, "7"),
      makePath(2, "7"),
    ];
    const result = majorityVote(paths);
    expect(result.answer).toBe("7");
    expect(result.confidence).toBeCloseTo(100);
    expect(result.successCount).toBe(3);
  });

  it("excludes error paths from voting", () => {
    const paths = [
      makePath(0, "42"),
      makePath(1, "Error", "error"),
      makePath(2, "42"),
      makePath(3, "Error", "error"),
      makePath(4, "42"),
    ];
    const result = majorityVote(paths);
    expect(result.answer).toBe("42");
    // 3 out of 5 total paths
    expect(result.confidence).toBe(60);
    expect(result.successCount).toBe(3);
  });

  it("excludes timeout paths from voting", () => {
    const paths = [
      makePath(0, "10"),
      makePath(1, "", "timeout"),
      makePath(2, "10"),
    ];
    const result = majorityVote(paths);
    expect(result.answer).toBe("10");
    expect(result.successCount).toBe(2);
  });

  it("returns fallback when all paths fail", () => {
    const paths = [
      makePath(0, "Error", "error"),
      makePath(1, "Error", "error"),
    ];
    const result = majorityVote(paths);
    expect(result.answer).toBe("No successful computations");
    expect(result.confidence).toBe(0);
    expect(result.successCount).toBe(0);
  });

  it("normalizes case for comparison", () => {
    const paths = [
      makePath(0, "X = 5"),
      makePath(1, "x = 5"),
      makePath(2, "x = 5"),
    ];
    const result = majorityVote(paths);
    // All three should be counted as the same answer
    expect(result.confidence).toBeCloseTo(100);
  });

  it("handles tie by picking first encountered", () => {
    const paths = [
      makePath(0, "10"),
      makePath(1, "20"),
      makePath(2, "10"),
      makePath(3, "20"),
    ];
    const result = majorityVote(paths);
    expect(["10", "20"]).toContain(result.answer);
    expect(result.confidence).toBe(50);
  });
});
