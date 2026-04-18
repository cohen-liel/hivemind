# MathAgent MVP — TODO

- [x] Database schema: problems table with question, finalAnswer, confidence, totalPaths, correctPaths, status, timestamps
- [x] Database schema: solution_paths table with problemId, pathIndex, reasoningSteps, finalAnswer, verificationStatus (correct/failed/uncertain), verificationCode, verificationOutput
- [x] Backend: multi-path reasoning endpoint — generates N parallel solution paths via LLM with chain-of-thought
- [x] Backend: code verification step — each path's answer verified via LLM-generated SymPy logic (LLM generates and evaluates code)
- [x] Backend: majority voting aggregator — compares answers, selects most frequent, calculates confidence score
- [x] Backend: problem history CRUD — save and retrieve solved problems from database
- [x] Frontend: dark-themed futuristic dashboard UI with neon accents and deep dark backgrounds
- [x] Frontend: math problem input interface with submit button and example problems
- [x] Frontend: animated progress indicators during solving
- [x] Frontend: reasoning path explorer panel — paths with answers and verification status (correct/failed/uncertain)
- [x] Frontend: majority voting result display with confidence score and confidence bar
- [x] Frontend: code verification display showing SymPy validation for each path
- [x] Frontend: architecture explainer section — Mamba SSM speed advantage, faster tokens = more attempts = higher accuracy
- [x] Frontend: problem history page — list of previously solved problems with results
- [x] Frontend: problem detail page — view full solution paths for a saved problem
- [x] Vitest tests for backend procedures (input validation, history, getResult, majority vote logic)
- [x] Frontend: improve path explorer to show side-by-side grid view at larger breakpoints
- [x] Fix: remove forced login — make solver accessible without authentication
- [x] Backend: add VASTAI_VLLM_URL environment variable for external vLLM endpoint
- [x] Backend: create vllmClient.ts module to call Vast.ai vLLM API (OpenAI-compatible)
- [x] Backend: update mathSolver.ts to use vLLM for reasoning paths (with fallback to built-in LLM)
- [x] Backend: add tRPC endpoint to check vLLM model status
- [x] Frontend: show which model backend is being used (Mamba/vLLM vs fallback)
- [x] Vitest: test vLLM client module

## Architecture Change: LLM-as-Translator + SymPy-as-Solver

- [x] Backend: create sympyExecutor.ts — Python subprocess executor with timeout and sandboxing
- [x] Backend: rewrite mathSolver.ts — LLM generates SymPy code → Python executes → majority vote on outputs
- [x] Database: add generatedCode, executionOutput, executionStatus columns to solution_paths
- [x] Backend: update db.ts for new schema fields
- [x] Backend: update routers.ts for new path structure
- [x] Frontend: update Home.tsx — show generated SymPy code and execution output instead of reasoning steps
- [x] Frontend: update ProblemDetail.tsx — same UI changes for code + execution display
- [x] Frontend: rewrite Architecture.tsx — explain new LLM→SymPy→Python approach
- [x] Vitest: update tests for new architecture
- [ ] Push code to GitHub repo
