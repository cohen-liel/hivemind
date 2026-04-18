import { trpc } from "@/lib/trpc";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Brain,
  Target,
  CheckCircle2,
  XCircle,
  Clock,
  Code2,
  Terminal,
  ArrowLeft,
  Loader2,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { useLocation, useParams } from "wouter";
import { useState } from "react";

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

export default function ProblemDetail() {
  const params = useParams<{ id: string }>();
  const [, setLocation] = useLocation();
  const [expandedPaths, setExpandedPaths] = useState<Set<number>>(new Set([0]));

  const problemId = Number(params.id);
  const { data: result, isLoading } = trpc.math.getResult.useQuery(
    { problemId },
    { enabled: !isNaN(problemId) }
  );

  const togglePath = (index: number) => {
    setExpandedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-8 w-8 text-primary animate-spin" />
      </div>
    );
  }

  if (!result) {
    return (
      <div className="space-y-6">
        <Button
          variant="ghost"
          onClick={() => setLocation("/history")}
          className="gap-2 text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to History
        </Button>
        <Card className="bg-card/60 border-border/50">
          <CardContent className="pt-6">
            <div className="text-center py-12">
              <p className="text-lg text-muted-foreground">Problem not found</p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  const paths = result.paths || [];
  const successCount = paths.filter((p: any) => p.executionStatus === "success").length;
  const errorCount = paths.filter((p: any) => p.executionStatus === "error").length;
  const timeoutCount = paths.filter((p: any) => p.executionStatus === "timeout").length;

  return (
    <div className="space-y-6">
      {/* Back button */}
      <Button
        variant="ghost"
        onClick={() => setLocation("/history")}
        className="gap-2 text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to History
      </Button>

      {/* Question */}
      <Card className="bg-card/80 border-border/50">
        <CardContent className="pt-6">
          <div className="flex items-start gap-3">
            <Brain className="h-5 w-5 text-primary mt-0.5 shrink-0" />
            <div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                Problem
              </p>
              <p className="text-base font-mono text-foreground mt-1">
                {result.question}
              </p>
              <p className="text-xs text-muted-foreground mt-2">
                Solved on {new Date(result.createdAt).toLocaleString()}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Final Answer */}
      {result.finalAnswer && (
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
                {result.confidence && (
                  <div className="text-center">
                    <p className="text-2xl font-bold text-foreground">
                      {Number(result.confidence).toFixed(0)}%
                    </p>
                    <p className="text-xs text-muted-foreground">Agreement</p>
                  </div>
                )}
                <div className="h-10 w-px bg-border/50" />
                <div className="flex gap-3">
                  <div className="text-center">
                    <p className="text-lg font-bold text-correct">{successCount}</p>
                    <p className="text-xs text-muted-foreground">Computed</p>
                  </div>
                  <div className="text-center">
                    <p className="text-lg font-bold text-failed">{errorCount}</p>
                    <p className="text-xs text-muted-foreground">Error</p>
                  </div>
                  <div className="text-center">
                    <p className="text-lg font-bold text-uncertain">{timeoutCount}</p>
                    <p className="text-xs text-muted-foreground">Timeout</p>
                  </div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Solution Paths */}
      <div>
        <h2 className="text-lg font-semibold text-foreground mb-4">
          Code Paths ({paths.length})
        </h2>
        <div className="space-y-3">
          {paths.map((path: any, idx: number) => (
            <Card
              key={idx}
              className="bg-card/60 border-border/50 backdrop-blur-sm transition-all hover:border-primary/30"
            >
              <CardHeader
                className="pb-3 cursor-pointer"
                onClick={() => togglePath(idx)}
              >
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
                    {expandedPaths.has(idx) ? (
                      <ChevronUp className="h-4 w-4 text-muted-foreground" />
                    ) : (
                      <ChevronDown className="h-4 w-4 text-muted-foreground" />
                    )}
                  </div>
                </div>
                <div className="mt-2 px-11">
                  <p className="text-sm font-mono text-foreground/90">
                    Answer:{" "}
                    <span className="text-primary font-semibold">
                      {path.finalAnswer}
                    </span>
                  </p>
                </div>
              </CardHeader>
              {expandedPaths.has(idx) && (
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
                          {path.executionOutput || "No output"}
                        </span>
                      </pre>
                    </div>
                  </div>
                </CardContent>
              )}
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
