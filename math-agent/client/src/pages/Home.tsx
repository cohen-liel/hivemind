import { useState, useMemo } from "react";
import { trpc } from "@/lib/trpc";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Brain,
  Play,
  CheckCircle2,
  XCircle,
  Clock,
  Zap,
  Target,
  Code2,
  Terminal,
  ChevronDown,
  ChevronUp,
  Loader2,
} from "lucide-react";

// ── Types ──

interface SolverPath {
  pathIndex: number;
  generatedCode: string;
  executionOutput: string;
  executionStatus: "success" | "error" | "timeout";
  finalAnswer: string;
  errorMessage?: string;
}

interface SolverResult {
  problemId: number;
  question: string;
  paths: SolverPath[];
  finalAnswer: string;
  confidence: number;
  totalPaths: number;
  successfulPaths: number;
  backend?: "vllm" | "forge";
}

const EXAMPLE_PROBLEMS = [
  "Solve for x: 3x^2 - 12x + 9 = 0",
  "What is the integral of x^2 * e^x dx?",
  "What is the sum of the first 100 prime numbers?",
  "Find the derivative of ln(sin(x^2))",
  "If a matrix A = [[1,2],[3,4]], what is A^(-1)?",
  "What is 20! / (15! * 5!)?",
];

// ── Status Badge ──

function ExecutionBadge({ status }: { status: string }) {
  if (status === "success") {
    return (
      <Badge className="bg-correct/15 text-correct border-correct/30 glow-correct gap-1">
        <CheckCircle2 className="h-3 w-3" />
        Computed
      </Badge>
    );
  }
  if (status === "timeout") {
    return (
      <Badge className="bg-uncertain/15 text-uncertain border-uncertain/30 glow-uncertain gap-1">
        <Clock className="h-3 w-3" />
        Timeout
      </Badge>
    );
  }
  return (
    <Badge className="bg-failed/15 text-failed border-failed/30 glow-failed gap-1">
      <XCircle className="h-3 w-3" />
      Error
    </Badge>
  );
}

// ── Path Card ──

function PathCard({
  path,
  isExpanded,
  onToggle,
}: {
  path: SolverPath;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  return (
    <Card className="bg-card/60 border-border/50 backdrop-blur-sm transition-all hover:border-primary/30">
      <CardHeader className="pb-3 cursor-pointer" onClick={onToggle}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-8 w-8 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center text-sm font-mono font-bold text-primary">
              {path.pathIndex + 1}
            </div>
            <div>
              <CardTitle className="text-sm font-medium">
                Path #{path.pathIndex + 1}
              </CardTitle>
              <p className="text-xs text-muted-foreground font-mono mt-0.5">
                {path.executionStatus === "success" ? "SymPy computed" : path.executionStatus}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <ExecutionBadge status={path.executionStatus} />
            {isExpanded ? (
              <ChevronUp className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            )}
          </div>
        </div>
        <div className="mt-2 px-11">
          <p className="text-sm font-mono text-foreground/90">
            Answer:{" "}
            <span className="text-primary font-semibold">{path.finalAnswer}</span>
          </p>
        </div>
      </CardHeader>
      {isExpanded && (
        <CardContent className="pt-0">
          <div className="space-y-4 pl-11">
            {/* Generated SymPy Code */}
            {path.generatedCode && (
              <div className="p-3 rounded-lg bg-background/80 border border-border/50">
                <div className="flex items-center gap-2 mb-2">
                  <Code2 className="h-3.5 w-3.5 text-primary" />
                  <span className="text-xs font-semibold text-primary uppercase tracking-wider">
                    Generated SymPy Code
                  </span>
                </div>
                <pre className="text-xs font-mono text-muted-foreground overflow-x-auto whitespace-pre-wrap leading-relaxed">
                  {path.generatedCode}
                </pre>
              </div>
            )}

            {/* Execution Output */}
            <div className="p-3 rounded-lg bg-background/80 border border-border/50">
              <div className="flex items-center gap-2 mb-2">
                <Terminal className="h-3.5 w-3.5 text-primary" />
                <span className="text-xs font-semibold text-primary uppercase tracking-wider">
                  Execution Output
                </span>
                <ExecutionBadge status={path.executionStatus} />
              </div>
              <pre className="text-xs font-mono overflow-x-auto whitespace-pre-wrap leading-relaxed">
                <span className={
                  path.executionStatus === "success"
                    ? "text-correct"
                    : path.executionStatus === "timeout"
                      ? "text-uncertain"
                      : "text-failed"
                }>
                  {path.executionOutput || path.errorMessage || "No output"}
                </span>
              </pre>
            </div>
          </div>
        </CardContent>
      )}
    </Card>
  );
}

// ── Main Page ──

export default function Home() {
  const [question, setQuestion] = useState("");
  const [numPaths, setNumPaths] = useState(5);
  const [result, setResult] = useState<SolverResult | null>(null);
  const [expandedPaths, setExpandedPaths] = useState<Set<number>>(new Set());
  const [activeTab, setActiveTab] = useState("paths");

  const modelStatus = trpc.math.modelStatus.useQuery(undefined, {
    refetchInterval: 30_000,
  });

  const solveMutation = trpc.math.solve.useMutation({
    onSuccess: (data) => {
      setResult(data as unknown as SolverResult);
      setExpandedPaths(new Set([0]));
    },
  });

  const isSolving = solveMutation.isPending;

  const togglePath = (index: number) => {
    setExpandedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const pathStats = useMemo(() => {
    if (!result) return { success: 0, error: 0, timeout: 0 };
    return {
      success: result.paths.filter((p) => p.executionStatus === "success").length,
      error: result.paths.filter((p) => p.executionStatus === "error").length,
      timeout: result.paths.filter((p) => p.executionStatus === "timeout").length,
    };
  }, [result]);

  const handleSolve = () => {
    if (!question.trim()) return;
    setResult(null);
    solveMutation.mutate({ question: question.trim(), numPaths });
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight glow-neon-text text-primary flex items-center gap-3">
          <Brain className="h-7 w-7" />
          Math Solver
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          LLM translates to SymPy code &rarr; Python computes the answer &rarr; majority voting on results
        </p>
        {/* Model status indicator */}
        <div className="mt-2 flex items-center gap-2">
          {modelStatus.data?.available ? (
            <Badge className="bg-correct/15 text-correct border-correct/30 gap-1 text-xs">
              <span className="h-1.5 w-1.5 rounded-full bg-correct inline-block animate-pulse" />
              vLLM: {modelStatus.data.model || "connected"}
            </Badge>
          ) : (
            <Badge className="bg-uncertain/15 text-uncertain border-uncertain/30 gap-1 text-xs">
              <span className="h-1.5 w-1.5 rounded-full bg-uncertain inline-block" />
              Translator: Forge LLM
            </Badge>
          )}
          {result?.backend && (
            <Badge variant="outline" className="text-xs gap-1">
              <Zap className="h-3 w-3" />
              Last run: {result.backend === "vllm" ? "vLLM (Vast.ai)" : "Forge LLM"}
            </Badge>
          )}
        </div>
      </div>

      {/* Input Section */}
      <Card className="bg-card/80 border-border/50 backdrop-blur-sm">
        <CardContent className="pt-6">
          <div className="space-y-4">
            <div>
              <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 block">
                Math Problem
              </label>
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Enter any math problem... (e.g., Solve for x: 2x + 5 = 13)"
                className="w-full h-28 bg-background/80 border border-border/50 rounded-lg p-3 text-sm font-mono text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary/50 resize-none transition-all"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    handleSolve();
                  }
                }}
              />
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2">
                <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  Paths
                </label>
                <select
                  value={numPaths}
                  onChange={(e) => setNumPaths(Number(e.target.value))}
                  className="bg-background/80 border border-border/50 rounded-md px-2 py-1 text-sm font-mono text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
                >
                  {[2, 3, 5, 7, 10].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </div>

              <Button
                onClick={handleSolve}
                disabled={isSolving || !question.trim()}
                className="gap-2"
              >
                {isSolving ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Computing...
                  </>
                ) : (
                  <>
                    <Play className="h-4 w-4" />
                    Solve
                  </>
                )}
              </Button>
            </div>

            {/* Example problems */}
            <div className="flex flex-wrap gap-2">
              <span className="text-xs text-muted-foreground self-center">
                Try:
              </span>
              {EXAMPLE_PROBLEMS.map((ex, i) => (
                <button
                  key={i}
                  onClick={() => setQuestion(ex)}
                  className="text-xs px-2.5 py-1 rounded-full border border-border/50 text-muted-foreground hover:text-primary hover:border-primary/30 transition-all truncate max-w-[250px]"
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Solving Animation */}
      {isSolving && (
        <Card className="bg-card/60 border-primary/20 backdrop-blur-sm glow-neon">
          <CardContent className="pt-6">
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <div className="relative">
                  <div className="h-10 w-10 rounded-xl bg-primary/10 border border-primary/30 flex items-center justify-center">
                    <Code2 className="h-5 w-5 text-primary animate-pulse" />
                  </div>
                  <div className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-primary animate-ping" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-foreground">
                    Generating {numPaths} SymPy code paths...
                  </p>
                  <p className="text-xs text-muted-foreground">
                    LLM translates to code &rarr; Python/SymPy computes &rarr; majority vote
                  </p>
                </div>
              </div>
              <div className="space-y-2">
                {Array.from({ length: numPaths }).map((_, i) => (
                  <div key={i} className="flex items-center gap-3">
                    <span className="text-xs font-mono text-muted-foreground w-16">
                      Path #{i + 1}
                    </span>
                    <div className="flex-1 h-1.5 rounded-full bg-background/50 overflow-hidden">
                      <div
                        className="h-full rounded-full bg-primary/60 animate-pulse"
                        style={{ width: `${Math.random() * 60 + 20}%` }}
                      />
                    </div>
                    <Loader2 className="h-3 w-3 text-primary animate-spin" />
                  </div>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Results */}
      {result && (
        <div className="space-y-4">
          {/* Final Answer Card */}
          <Card className="bg-card/80 border-primary/30 backdrop-blur-sm glow-neon">
            <CardContent className="pt-6">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div className="flex items-start gap-4">
                  <div className="h-12 w-12 rounded-xl bg-primary/10 border border-primary/30 flex items-center justify-center shrink-0">
                    <Target className="h-6 w-6 text-primary" />
                  </div>
                  <div>
                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                      Computed Answer (Majority Vote)
                    </p>
                    <p className="text-2xl font-bold font-mono text-primary glow-neon-text mt-1">
                      {result.finalAnswer}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-6">
                  <div className="text-center">
                    <p className="text-2xl font-bold text-foreground">
                      {result.confidence.toFixed(0)}%
                    </p>
                    <p className="text-xs text-muted-foreground">Agreement</p>
                  </div>
                  <div className="h-10 w-px bg-border/50" />
                  <div className="flex gap-3">
                    <div className="text-center">
                      <p className="text-lg font-bold text-correct">
                        {pathStats.success}
                      </p>
                      <p className="text-xs text-muted-foreground">Computed</p>
                    </div>
                    <div className="text-center">
                      <p className="text-lg font-bold text-failed">
                        {pathStats.error}
                      </p>
                      <p className="text-xs text-muted-foreground">Error</p>
                    </div>
                    <div className="text-center">
                      <p className="text-lg font-bold text-uncertain">
                        {pathStats.timeout}
                      </p>
                      <p className="text-xs text-muted-foreground">Timeout</p>
                    </div>
                  </div>
                </div>
              </div>
              {/* Confidence bar */}
              <div className="mt-4">
                <div className="h-2 rounded-full bg-background/50 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-primary/60 to-primary transition-all duration-1000"
                    style={{ width: `${result.confidence}%` }}
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Tabs: Paths / Side-by-Side / Overview */}
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList className="bg-card/60 border border-border/50">
              <TabsTrigger value="paths" className="gap-1.5 data-[state=active]:bg-primary/10 data-[state=active]:text-primary">
                <Code2 className="h-3.5 w-3.5" />
                Code Paths
              </TabsTrigger>
              <TabsTrigger value="sidebyside" className="gap-1.5 data-[state=active]:bg-primary/10 data-[state=active]:text-primary">
                <Target className="h-3.5 w-3.5" />
                Side-by-Side ({result.paths.length})
              </TabsTrigger>
              <TabsTrigger value="overview" className="gap-1.5 data-[state=active]:bg-primary/10 data-[state=active]:text-primary">
                <Brain className="h-3.5 w-3.5" />
                Overview
              </TabsTrigger>
            </TabsList>

            <TabsContent value="paths" className="mt-4 space-y-3">
              {result.paths.map((path) => (
                <PathCard
                  key={path.pathIndex}
                  path={path}
                  isExpanded={expandedPaths.has(path.pathIndex)}
                  onToggle={() => togglePath(path.pathIndex)}
                />
              ))}
            </TabsContent>

            <TabsContent value="sidebyside" className="mt-4">
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {result.paths.map((path) => (
                  <Card
                    key={path.pathIndex}
                    className={`bg-card/60 backdrop-blur-sm transition-all ${
                      path.executionStatus === "success"
                        ? "border-correct/30"
                        : path.executionStatus === "timeout"
                          ? "border-uncertain/30"
                          : "border-failed/30"
                    }`}
                  >
                    <CardHeader className="pb-2">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <div className="h-7 w-7 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center text-xs font-mono font-bold text-primary">
                            {path.pathIndex + 1}
                          </div>
                          <CardTitle className="text-sm">Path #{path.pathIndex + 1}</CardTitle>
                        </div>
                        <ExecutionBadge status={path.executionStatus} />
                      </div>
                    </CardHeader>
                    <CardContent className="pt-0">
                      <p className="text-lg font-mono font-bold text-primary mb-3">
                        {path.finalAnswer}
                      </p>
                      {path.generatedCode && (
                        <div className="p-2 rounded bg-background/60 border border-border/30 mb-2">
                          <div className="flex items-center gap-1 mb-1">
                            <Code2 className="h-3 w-3 text-primary" />
                            <span className="text-[10px] font-semibold text-primary uppercase">SymPy Code</span>
                          </div>
                          <pre className="text-[10px] font-mono text-muted-foreground overflow-x-auto whitespace-pre-wrap max-h-32">
                            {path.generatedCode}
                          </pre>
                        </div>
                      )}
                      <div className="p-2 rounded bg-background/60 border border-border/30">
                        <div className="flex items-center gap-1 mb-1">
                          <Terminal className="h-3 w-3 text-primary" />
                          <span className="text-[10px] font-semibold text-primary uppercase">Output</span>
                        </div>
                        <pre className={`text-[10px] font-mono overflow-x-auto whitespace-pre-wrap max-h-20 ${
                          path.executionStatus === "success" ? "text-correct" : "text-failed"
                        }`}>
                          {path.executionOutput || "No output"}
                        </pre>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="overview" className="mt-4">
              <Card className="bg-card/60 border-border/50">
                <CardContent className="pt-6">
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    {result.paths.map((path) => (
                      <div
                        key={path.pathIndex}
                        className={`p-4 rounded-lg border transition-all cursor-pointer hover:scale-[1.02] ${
                          path.executionStatus === "success"
                            ? "border-correct/30 bg-correct/5"
                            : path.executionStatus === "timeout"
                              ? "border-uncertain/30 bg-uncertain/5"
                              : "border-failed/30 bg-failed/5"
                        }`}
                        onClick={() => {
                          setActiveTab("paths");
                          setExpandedPaths(new Set([path.pathIndex]));
                        }}
                      >
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-sm font-semibold">
                            Path #{path.pathIndex + 1}
                          </span>
                          <ExecutionBadge status={path.executionStatus} />
                        </div>
                        <p className="text-lg font-mono font-bold text-foreground">
                          {path.finalAnswer}
                        </p>
                        <p className="text-xs text-muted-foreground mt-1">
                          {path.executionStatus === "success"
                            ? "Computed by SymPy"
                            : path.executionStatus === "timeout"
                              ? "Execution timed out"
                              : "Execution error"}
                        </p>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </div>
      )}
    </div>
  );
}
