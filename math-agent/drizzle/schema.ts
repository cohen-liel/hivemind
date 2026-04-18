import { int, mysqlEnum, mysqlTable, text, timestamp, varchar, json, decimal } from "drizzle-orm/mysql-core";

export const users = mysqlTable("users", {
  id: int("id").autoincrement().primaryKey(),
  openId: varchar("openId", { length: 64 }).notNull().unique(),
  name: text("name"),
  email: varchar("email", { length: 320 }),
  loginMethod: varchar("loginMethod", { length: 64 }),
  role: mysqlEnum("role", ["user", "admin"]).default("user").notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
  lastSignedIn: timestamp("lastSignedIn").defaultNow().notNull(),
});

export type User = typeof users.$inferSelect;
export type InsertUser = typeof users.$inferInsert;

export const problems = mysqlTable("problems", {
  id: int("id").autoincrement().primaryKey(),
  userId: int("userId"),
  question: text("question").notNull(),
  finalAnswer: text("finalAnswer"),
  confidence: decimal("confidence", { precision: 5, scale: 2 }),
  totalPaths: int("totalPaths").default(0),
  correctPaths: int("correctPaths").default(0),
  status: mysqlEnum("status", ["pending", "solving", "completed", "error"]).default("pending").notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
  updatedAt: timestamp("updatedAt").defaultNow().onUpdateNow().notNull(),
});

export type Problem = typeof problems.$inferSelect;
export type InsertProblem = typeof problems.$inferInsert;

export const solutionPaths = mysqlTable("solution_paths", {
  id: int("id").autoincrement().primaryKey(),
  problemId: int("problemId").notNull(),
  pathIndex: int("pathIndex").notNull(),
  reasoningSteps: json("reasoningSteps"),
  finalAnswer: text("finalAnswer"),
  verificationStatus: mysqlEnum("verificationStatus", ["correct", "failed", "uncertain"]).default("uncertain").notNull(),
  verificationCode: text("verificationCode"),
  verificationOutput: text("verificationOutput"),
  // New SymPy architecture fields
  generatedCode: text("generatedCode"),
  executionOutput: text("executionOutput"),
  executionStatus: mysqlEnum("executionStatus", ["success", "error", "timeout"]).default("error").notNull(),
  createdAt: timestamp("createdAt").defaultNow().notNull(),
});

export type SolutionPath = typeof solutionPaths.$inferSelect;
export type InsertSolutionPath = typeof solutionPaths.$inferInsert;
