import { trpc } from "@/lib/trpc";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  History as HistoryIcon,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  Loader2,
  Brain,
} from "lucide-react";
import { useLocation } from "wouter";

function StatusBadge({ status }: { status: string }) {
  switch (status) {
    case "completed":
      return (
        <Badge className="bg-correct/15 text-correct border-correct/30 gap-1">
          <CheckCircle2 className="h-3 w-3" />
          Completed
        </Badge>
      );
    case "solving":
      return (
        <Badge className="bg-primary/15 text-primary border-primary/30 gap-1">
          <Loader2 className="h-3 w-3 animate-spin" />
          Solving
        </Badge>
      );
    case "error":
      return (
        <Badge className="bg-failed/15 text-failed border-failed/30 gap-1">
          <XCircle className="h-3 w-3" />
          Error
        </Badge>
      );
    default:
      return (
        <Badge className="bg-uncertain/15 text-uncertain border-uncertain/30 gap-1">
          <Clock className="h-3 w-3" />
          Pending
        </Badge>
      );
  }
}

export default function History() {
  const [, setLocation] = useLocation();
  const { data: problems, isLoading } = trpc.math.history.useQuery({ limit: 50 });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight glow-neon-text text-primary flex items-center gap-3">
          <HistoryIcon className="h-7 w-7" />
          Problem History
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Previously solved problems and their results
        </p>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-8 w-8 text-primary animate-spin" />
        </div>
      )}

      {!isLoading && (!problems || problems.length === 0) && (
        <Card className="bg-card/60 border-border/50">
          <CardContent className="pt-6">
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <div className="h-16 w-16 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center mb-4">
                <Brain className="h-8 w-8 text-primary/50" />
              </div>
              <p className="text-lg font-medium text-foreground/70">
                No problems solved yet
              </p>
              <p className="text-sm text-muted-foreground mt-1">
                Go to the Solver page and submit your first math problem
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {problems && problems.length > 0 && (
        <div className="space-y-3">
          {problems.map((problem) => (
            <Card
              key={problem.id}
              className="bg-card/60 border-border/50 backdrop-blur-sm hover:border-primary/30 transition-all cursor-pointer group"
              onClick={() => setLocation(`/problem/${problem.id}`)}
            >
              <CardContent className="pt-5 pb-5">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-mono text-foreground/90 truncate group-hover:text-primary transition-colors">
                      {problem.question}
                    </p>
                    <div className="flex items-center gap-4 mt-2">
                      <StatusBadge status={problem.status} />
                      {problem.finalAnswer && (
                        <span className="text-xs font-mono text-primary">
                          Answer: {problem.finalAnswer}
                        </span>
                      )}
                      {problem.confidence && (
                        <span className="text-xs text-muted-foreground">
                          {Number(problem.confidence).toFixed(0)}% confidence
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-4 shrink-0">
                    {problem.totalPaths !== null && problem.totalPaths > 0 && (
                      <div className="text-right">
                        <p className="text-xs text-muted-foreground">
                          {problem.correctPaths}/{problem.totalPaths} paths verified
                        </p>
                      </div>
                    )}
                    <p className="text-xs text-muted-foreground">
                      {new Date(problem.createdAt).toLocaleDateString()}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
