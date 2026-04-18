import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Cpu,
  Zap,
  Brain,
  Target,
  Code2,
  Terminal,
  ArrowRight,
  Layers,
  GitBranch,
  Shield,
} from "lucide-react";

function ArchCard({
  icon: Icon,
  title,
  children,
  accent = false,
}: {
  icon: React.ElementType;
  title: string;
  children: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <Card
      className={`bg-card/60 border-border/50 backdrop-blur-sm ${accent ? "border-primary/30 glow-neon" : ""}`}
    >
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-3 text-base">
          <div
            className={`h-9 w-9 rounded-lg flex items-center justify-center shrink-0 ${accent ? "bg-primary/15 border border-primary/30" : "bg-muted/50 border border-border/50"}`}
          >
            <Icon
              className={`h-4.5 w-4.5 ${accent ? "text-primary" : "text-muted-foreground"}`}
            />
          </div>
          <span className={accent ? "text-primary" : ""}>{title}</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground leading-relaxed">
        {children}
      </CardContent>
    </Card>
  );
}

export default function Architecture() {
  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight glow-neon-text text-primary flex items-center gap-3">
          <Cpu className="h-7 w-7" />
          Architecture
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          How MathAgent achieves verified computation through code generation and execution
        </p>
      </div>

      {/* Hero: The Core Insight */}
      <Card className="bg-card/80 border-primary/30 backdrop-blur-sm glow-neon overflow-hidden relative">
        <div className="absolute inset-0 grid-bg opacity-30" />
        <CardContent className="pt-8 pb-8 relative">
          <div className="max-w-3xl mx-auto text-center space-y-4">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 border border-primary/20 text-xs font-semibold text-primary uppercase tracking-wider">
              <Zap className="h-3 w-3" />
              Core Principle
            </div>
            <h2 className="text-3xl font-bold text-foreground">
              LLM Translates &rarr; SymPy Computes &rarr; Majority Votes
            </h2>
            <p className="text-base text-muted-foreground leading-relaxed max-w-2xl mx-auto">
              Instead of asking an LLM to <em>solve</em> math problems (which leads to hallucinations),
              MathAgent uses the LLM as a <strong className="text-primary">translator</strong> that
              converts math questions into Python/SymPy code. The code is then{" "}
              <strong className="text-foreground">executed by a real Python interpreter</strong>,
              giving verified, computed answers — not guesses.
            </p>
          </div>
        </CardContent>
      </Card>

      {/* The Pipeline */}
      <div>
        <h2 className="text-lg font-semibold text-foreground mb-4 flex items-center gap-2">
          <Layers className="h-5 w-5 text-primary" />
          The Compute Pipeline
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
          <div className="flex flex-col items-center text-center p-5 rounded-xl bg-card/60 border border-border/50">
            <div className="h-12 w-12 rounded-xl bg-primary/10 border border-primary/30 flex items-center justify-center mb-3">
              <Brain className="h-6 w-6 text-primary" />
            </div>
            <h3 className="text-sm font-semibold text-foreground mb-1">
              1. Translate
            </h3>
            <p className="text-xs text-muted-foreground">
              LLM translates the math problem into N different Python/SymPy code snippets
            </p>
          </div>

          <div className="flex items-center justify-center md:hidden">
            <ArrowRight className="h-5 w-5 text-primary/50 rotate-90" />
          </div>
          <div className="hidden md:flex items-center justify-center -mx-2">
            <ArrowRight className="h-5 w-5 text-primary/50" />
          </div>

          <div className="flex flex-col items-center text-center p-5 rounded-xl bg-card/60 border border-border/50">
            <div className="h-12 w-12 rounded-xl bg-correct/10 border border-correct/30 flex items-center justify-center mb-3">
              <Terminal className="h-6 w-6 text-correct" />
            </div>
            <h3 className="text-sm font-semibold text-foreground mb-1">
              2. Execute
            </h3>
            <p className="text-xs text-muted-foreground">
              Python subprocess runs each code snippet with SymPy, capturing stdout as the answer
            </p>
          </div>

          <div className="flex items-center justify-center md:hidden">
            <ArrowRight className="h-5 w-5 text-primary/50 rotate-90" />
          </div>
          <div className="hidden md:flex items-center justify-center -mx-2">
            <ArrowRight className="h-5 w-5 text-primary/50" />
          </div>

          <div className="flex flex-col items-center text-center p-5 rounded-xl bg-card/60 border border-primary/30 glow-neon">
            <div className="h-12 w-12 rounded-xl bg-primary/10 border border-primary/30 flex items-center justify-center mb-3">
              <Target className="h-6 w-6 text-primary" />
            </div>
            <h3 className="text-sm font-semibold text-primary mb-1">
              3. Majority Vote
            </h3>
            <p className="text-xs text-muted-foreground">
              Compare execution outputs — the most frequent computed answer wins
            </p>
          </div>
        </div>
      </div>

      {/* Key Design Decisions */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ArchCard icon={Code2} title="Why Code Generation?" accent>
          <div className="space-y-3">
            <p>
              LLMs are remarkably good at <strong className="text-foreground">translating</strong>{" "}
              between representations — natural language to code, code to code, etc. But they are
              unreliable at <em>computing</em> results. They hallucinate arithmetic, lose track of
              variables, and make systematic errors on multi-step problems.
            </p>
            <p>
              By using the LLM only as a translator, we play to its strengths. The LLM generates
              Python/SymPy code that <em>describes</em> the computation, and then a real Python
              interpreter <strong className="text-primary">executes</strong> it. This means:
            </p>
            <ul className="space-y-2 mt-2">
              <li className="flex items-start gap-2">
                <Shield className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                <span>
                  <strong className="text-foreground">No arithmetic hallucinations</strong> — SymPy
                  does exact symbolic computation
                </span>
              </li>
              <li className="flex items-start gap-2">
                <Shield className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                <span>
                  <strong className="text-foreground">Verifiable results</strong> — you can see the
                  code and verify it yourself
                </span>
              </li>
              <li className="flex items-start gap-2">
                <Shield className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                <span>
                  <strong className="text-foreground">Error detection</strong> — if the code fails,
                  we know the translation was wrong (not a silent hallucination)
                </span>
              </li>
            </ul>
          </div>
        </ArchCard>

        <ArchCard icon={GitBranch} title="Multi-Path + Majority Voting">
          <div className="space-y-3">
            <p>
              Even with code generation, a single LLM translation might be wrong — it could
              misinterpret the problem or generate buggy code. MathAgent generates{" "}
              <strong className="text-foreground">N independent code paths</strong> with slight
              temperature variation, then uses <strong className="text-primary">majority voting</strong>{" "}
              on the execution outputs.
            </p>
            <p>
              This is powerful because:
            </p>
            <ul className="space-y-2 mt-2">
              <li className="flex items-start gap-2">
                <Zap className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                <span>
                  Different code approaches often converge on the same correct answer
                </span>
              </li>
              <li className="flex items-start gap-2">
                <Zap className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                <span>
                  Wrong translations tend to produce different wrong answers (they disagree)
                </span>
              </li>
              <li className="flex items-start gap-2">
                <Zap className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                <span>
                  Failed executions (errors/timeouts) are automatically excluded from voting
                </span>
              </li>
            </ul>
            <div className="mt-3 p-3 rounded-lg bg-primary/5 border border-primary/20">
              <p className="text-xs font-mono text-primary">
                P(correct) = 1 - (1 - p)^N
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                Where p = probability of a single code path being correct, N = number of paths.
                More paths exponentially increase accuracy.
              </p>
            </div>
          </div>
        </ArchCard>
      </div>

      {/* Comparison: Old vs New */}
      <Card className="bg-card/60 border-border/50">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Cpu className="h-4.5 w-4.5 text-primary" />
            Architecture Comparison
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50">
                  <th className="text-left py-3 px-4 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    Aspect
                  </th>
                  <th className="text-left py-3 px-4 text-xs font-semibold text-failed uppercase tracking-wider">
                    LLM Solves Directly
                  </th>
                  <th className="text-left py-3 px-4 text-xs font-semibold text-primary uppercase tracking-wider">
                    LLM Translates + SymPy Computes
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/30">
                <tr>
                  <td className="py-3 px-4 text-muted-foreground">LLM Role</td>
                  <td className="py-3 px-4 text-failed">Solver (unreliable)</td>
                  <td className="py-3 px-4 text-correct">Translator (reliable)</td>
                </tr>
                <tr>
                  <td className="py-3 px-4 text-muted-foreground">Computation</td>
                  <td className="py-3 px-4 text-failed">LLM guesses the answer</td>
                  <td className="py-3 px-4 text-correct">Python/SymPy computes exactly</td>
                </tr>
                <tr>
                  <td className="py-3 px-4 text-muted-foreground">Verification</td>
                  <td className="py-3 px-4 text-failed">LLM verifies itself (circular)</td>
                  <td className="py-3 px-4 text-correct">Code execution = verification</td>
                </tr>
                <tr>
                  <td className="py-3 px-4 text-muted-foreground">Error Detection</td>
                  <td className="py-3 px-4 text-failed">Silent hallucinations</td>
                  <td className="py-3 px-4 text-correct">Explicit errors (Python exceptions)</td>
                </tr>
                <tr>
                  <td className="py-3 px-4 text-muted-foreground">Transparency</td>
                  <td className="py-3 px-4 text-failed">Black box reasoning</td>
                  <td className="py-3 px-4 text-correct">Readable code you can audit</td>
                </tr>
                <tr>
                  <td className="py-3 px-4 text-muted-foreground">Majority Voting</td>
                  <td className="py-3 px-4 text-failed">Votes on text (noisy)</td>
                  <td className="py-3 px-4 text-correct font-semibold">Votes on computed outputs (precise)</td>
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Tech Stack */}
      <Card className="bg-card/60 border-border/50">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Layers className="h-4.5 w-4.5 text-primary" />
            Technology Stack
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="p-4 rounded-lg bg-background/60 border border-border/30">
              <h4 className="text-sm font-semibold text-foreground mb-2">Frontend</h4>
              <p className="text-xs text-muted-foreground">
                React 19 + Tailwind CSS 4 + shadcn/ui. Dark futuristic theme with real-time
                path visualization and code display.
              </p>
            </div>
            <div className="p-4 rounded-lg bg-background/60 border border-border/30">
              <h4 className="text-sm font-semibold text-foreground mb-2">Backend</h4>
              <p className="text-xs text-muted-foreground">
                Express + tRPC + Drizzle ORM + MySQL. LLM via Forge API (free). Python subprocess
                for SymPy code execution with 15s timeout.
              </p>
            </div>
            <div className="p-4 rounded-lg bg-background/60 border border-border/30">
              <h4 className="text-sm font-semibold text-foreground mb-2">Compute Engine</h4>
              <p className="text-xs text-muted-foreground">
                SymPy (Python CAS) for exact symbolic math. Supports algebra, calculus,
                combinatorics, number theory, linear algebra, and more.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Future */}
      <Card className="bg-card/60 border-border/50">
        <CardContent className="pt-6">
          <div className="text-center space-y-3 max-w-2xl mx-auto">
            <h3 className="text-lg font-semibold text-foreground">
              Roadmap
            </h3>
            <p className="text-sm text-muted-foreground">
              The current architecture uses Forge LLM (free, cloud-hosted) as the translator.
              Future improvements include: a dedicated math-tuned LLM for better code generation,
              support for AMC/AIME olympiad-level problems, multi-step problem decomposition,
              and optional self-hosted vLLM on GPU for faster parallel path generation.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
