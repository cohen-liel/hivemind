/**
 * Math Solver — LLM-as-Translator + SymPy-as-Solver Architecture
 *
 * Pipeline:
 *   1. LLM translates the math question into Python/SymPy code
 *   2. Python subprocess executes the code and captures stdout
 *   3. Multiple paths are generated in parallel (temperature variation)
 *   4. Majority voting on execution outputs determines the final answer
 *
 * The LLM never "solves" the problem — it only translates.
 * SymPy does the actual computation, giving verified results.
 */

import { invokeVllm } from "./vllmClient";
import { executeSymPyCode, extractAnswer } from "./sympyExecutor";
import {
  createProblem,
  createSolutionPath,
  updateProblem,
} from "./db";

// ── Types ──

export interface SolverPath {
  pathIndex: number;
  generatedCode: string;
  executionOutput: string;
  executionStatus: "success" | "error" | "timeout";
  finalAnswer: string;
  errorMessage?: string;
}

export interface SolverResult {
  problemId: number;
  question: string;
  paths: SolverPath[];
  finalAnswer: string;
  confidence: number;
  totalPaths: number;
  successfulPaths: number;
  backend: "vllm" | "forge";
}

// ── Code Generation Prompt ──

const SYSTEM_PROMPT = `You are a math-to-code translator. Your ONLY job is to translate math problems into Python code that uses SymPy to compute the answer.

RULES:
1. Output ONLY valid Python code. No explanations, no markdown, no code fences.
2. Always import what you need from sympy at the top (e.g., from sympy import *).
3. The code MUST end with print(result) or print(answer) — the stdout is captured as the answer.
4. Use SymPy functions for symbolic computation: solve(), integrate(), diff(), simplify(), etc.
5. For numeric answers, use float() or N() to get a decimal when appropriate.
6. For exact symbolic answers, let SymPy's default representation handle it.
7. Handle edge cases gracefully (e.g., no solution, multiple solutions).
8. If there are multiple solutions, print them all separated by commas.
9. Keep the code concise but correct.

EXAMPLES:

Problem: "Solve x^2 - 5x + 6 = 0"
Code:
from sympy import symbols, solve
x = symbols('x')
solutions = solve(x**2 - 5*x + 6, x)
print(', '.join(str(s) for s in solutions))

Problem: "What is the integral of x^2 * sin(x) dx?"
Code:
from sympy import symbols, integrate, sin
x = symbols('x')
result = integrate(x**2 * sin(x), x)
print(result)

Problem: "What is 15! / (10! * 5!)?"
Code:
from sympy import factorial
result = factorial(15) / (factorial(10) * factorial(5))
print(int(result))`;

// ── Code Generation ──

async function generateSymPyCode(
  question: string,
  pathIndex: number
): Promise<{ code: string; backend: "vllm" | "forge" }> {
  const temperature = 0.3 + pathIndex * 0.1; // Low base temp for code, slight variation

  const response = await invokeVllm({
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      {
        role: "user",
        content: `Translate this math problem into Python/SymPy code that computes and prints the answer.\n\nProblem: ${question}\n\nPython code:`,
      },
    ],
    temperature: Math.min(temperature, 1.0),
  });

  const content = response.choices[0]?.message?.content;
  if (!content || typeof content !== "string") {
    throw new Error("Empty LLM response");
  }

  // Clean up the code — remove markdown fences if the LLM adds them
  let code = content.trim();
  if (code.startsWith("```python")) {
    code = code.slice("```python".length);
  } else if (code.startsWith("```")) {
    code = code.slice(3);
  }
  if (code.endsWith("```")) {
    code = code.slice(0, -3);
  }
  code = code.trim();

  return { code, backend: response.backend };
}

// ── Majority Voting ──

export function majorityVote(paths: SolverPath[]): {
  answer: string;
  confidence: number;
  successCount: number;
} {
  // Only consider successful executions
  const successPaths = paths.filter((p) => p.executionStatus === "success");

  if (successPaths.length === 0) {
    return { answer: "No successful computations", confidence: 0, successCount: 0 };
  }

  const answerCounts = new Map<string, number>();

  for (const path of successPaths) {
    // Normalize: trim whitespace, lowercase, remove trailing periods
    const normalized = path.finalAnswer
      .trim()
      .toLowerCase()
      .replace(/\.+$/, "")
      .replace(/\s+/g, " ");
    answerCounts.set(normalized, (answerCounts.get(normalized) || 0) + 1);
  }

  let bestNormalized = "";
  let bestCount = 0;
  for (const [answer, count] of Array.from(answerCounts.entries())) {
    if (count > bestCount) {
      bestCount = count;
      bestNormalized = answer;
    }
  }

  // Find the original (non-normalized) answer from the first matching path
  const originalAnswer =
    successPaths.find(
      (p) =>
        p.finalAnswer
          .trim()
          .toLowerCase()
          .replace(/\.+$/, "")
          .replace(/\s+/g, " ") === bestNormalized
    )?.finalAnswer.trim() || bestNormalized;

  // Confidence = agreement ratio among ALL paths (not just successful)
  const confidence = (bestCount / paths.length) * 100;

  return {
    answer: originalAnswer,
    confidence,
    successCount: successPaths.length,
  };
}

// ── Main Solver Pipeline ──

export async function solveProblem(
  question: string,
  numPaths: number = 5,
  userId?: number
): Promise<SolverResult> {
  // 1. Create problem record
  const problemId = await createProblem({
    question,
    userId: userId ?? null,
    status: "solving",
  });

  const solverPaths: SolverPath[] = [];
  let usedBackend: "vllm" | "forge" = "forge";

  try {
    // 2. Generate SymPy code for each path in parallel
    const codePromises = Array.from({ length: numPaths }, (_, i) =>
      generateSymPyCode(question, i)
        .then((result) => ({ success: true as const, result, index: i }))
        .catch((error) => ({
          success: false as const,
          error: String(error),
          index: i,
        }))
    );

    const codeResults = await Promise.all(codePromises);

    // 3. Execute each generated code snippet
    for (const codeResult of codeResults) {
      if (!codeResult.success) {
        // LLM failed to generate code for this path
        const path: SolverPath = {
          pathIndex: codeResult.index,
          generatedCode: "",
          executionOutput: "",
          executionStatus: "error",
          finalAnswer: "Error",
          errorMessage: `Code generation failed: ${codeResult.error}`,
        };

        await createSolutionPath({
          problemId,
          pathIndex: codeResult.index,
          generatedCode: "",
          executionOutput: codeResult.error,
          executionStatus: "error",
          finalAnswer: "Error",
          // Legacy fields (keep for backward compatibility)
          reasoningSteps: JSON.stringify([]),
          verificationStatus: "failed",
          verificationCode: "",
          verificationOutput: codeResult.error,
        });

        solverPaths.push(path);
        continue;
      }

      const { code, backend } = codeResult.result;
      usedBackend = backend;

      // 4. Execute the SymPy code
      const execution = await executeSymPyCode(code);
      const answer = execution.status === "success"
        ? extractAnswer(execution.output)
        : "Error";

      const path: SolverPath = {
        pathIndex: codeResult.index,
        generatedCode: code,
        executionOutput: execution.output || execution.errorMessage || "",
        executionStatus: execution.status,
        finalAnswer: answer,
        errorMessage: execution.errorMessage,
      };

      // 5. Save to database
      await createSolutionPath({
        problemId,
        pathIndex: codeResult.index,
        generatedCode: code,
        executionOutput: execution.output || execution.errorMessage || "",
        executionStatus: execution.status,
        finalAnswer: answer,
        // Legacy fields mapped from new architecture
        reasoningSteps: JSON.stringify([]),
        verificationStatus:
          execution.status === "success" ? "correct" : "failed",
        verificationCode: code,
        verificationOutput: execution.output || execution.errorMessage || "",
      });

      solverPaths.push(path);
    }

    // 6. Majority vote on execution outputs
    const { answer: finalAnswer, confidence, successCount } =
      majorityVote(solverPaths);

    // 7. Update problem record
    await updateProblem(problemId, {
      finalAnswer,
      confidence: String(confidence),
      totalPaths: numPaths,
      correctPaths: successCount,
      status: "completed",
    });

    return {
      problemId,
      question,
      paths: solverPaths.sort((a, b) => a.pathIndex - b.pathIndex),
      finalAnswer,
      confidence,
      totalPaths: numPaths,
      successfulPaths: successCount,
      backend: usedBackend,
    };
  } catch (error) {
    await updateProblem(problemId, { status: "error" });
    throw error;
  }
}
