import { COOKIE_NAME } from "@shared/const";
import { getSessionCookieOptions } from "./_core/cookies";
import { systemRouter } from "./_core/systemRouter";
import { publicProcedure, protectedProcedure, router } from "./_core/trpc";
import { z } from "zod";
import { solveProblem } from "./mathSolver";
import { checkVllmStatus } from "./vllmClient";
import {
  listProblems,
  getProblemById,
  getPathsByProblemId,
} from "./db";

export const appRouter = router({
  system: systemRouter,
  auth: router({
    me: publicProcedure.query((opts) => opts.ctx.user),
    logout: publicProcedure.mutation(({ ctx }) => {
      const cookieOptions = getSessionCookieOptions(ctx.req);
      ctx.res.clearCookie(COOKIE_NAME, { ...cookieOptions, maxAge: -1 });
      return { success: true } as const;
    }),
  }),

  math: router({
    solve: publicProcedure
      .input(
        z.object({
          question: z.string().min(1).max(5000),
          numPaths: z.number().min(2).max(10).default(5),
        })
      )
      .mutation(async ({ input, ctx }) => {
        const userId = ctx.user?.id;
        const result = await solveProblem(
          input.question,
          input.numPaths,
          userId ?? undefined
        );
        return result;
      }),

    history: publicProcedure
      .input(
        z.object({
          limit: z.number().min(1).max(100).default(20),
        }).optional()
      )
      .query(async ({ ctx, input }) => {
        const userId = ctx.user?.id;
        const limit = input?.limit ?? 20;
        const problemsList = await listProblems(userId ?? undefined, limit);
        return problemsList;
      }),

    getResult: publicProcedure
      .input(z.object({ problemId: z.number() }))
      .query(async ({ input }) => {
        const problem = await getProblemById(input.problemId);
        if (!problem) return null;
        const paths = await getPathsByProblemId(input.problemId);
        // Map paths to include both legacy and new SymPy architecture fields
        const parsedPaths = paths.map((p) => ({
          pathIndex: p.pathIndex,
          // New SymPy architecture fields (primary)
          generatedCode: p.generatedCode || p.verificationCode || "",
          executionOutput: p.executionOutput || p.verificationOutput || "",
          executionStatus: (p.executionStatus || (p.verificationStatus === "correct" ? "success" : "error")) as "success" | "error" | "timeout",
          finalAnswer: p.finalAnswer || "",
          // Legacy fields for backward compatibility
          reasoningSteps:
            typeof p.reasoningSteps === "string"
              ? JSON.parse(p.reasoningSteps)
              : p.reasoningSteps || [],
          verificationStatus: p.verificationStatus,
          verificationCode: p.verificationCode || "",
          verificationOutput: p.verificationOutput || "",
        }));
        return {
          ...problem,
          paths: parsedPaths,
        };
      }),

    modelStatus: publicProcedure.query(async () => {
      const status = await checkVllmStatus();
      return status;
    }),
  }),
});

export type AppRouter = typeof appRouter;
