import { eq, desc } from "drizzle-orm";
import { drizzle } from "drizzle-orm/mysql2";
import {
  InsertUser, users,
  problems, InsertProblem, Problem,
  solutionPaths, InsertSolutionPath, SolutionPath,
} from "../drizzle/schema";
import { ENV } from './_core/env';

let _db: ReturnType<typeof drizzle> | null = null;

export async function getDb() {
  if (!_db && process.env.DATABASE_URL) {
    try {
      _db = drizzle(process.env.DATABASE_URL);
    } catch (error) {
      console.warn("[Database] Failed to connect:", error);
      _db = null;
    }
  }
  return _db;
}

export async function upsertUser(user: InsertUser): Promise<void> {
  if (!user.openId) {
    throw new Error("User openId is required for upsert");
  }
  const db = await getDb();
  if (!db) {
    console.warn("[Database] Cannot upsert user: database not available");
    return;
  }
  try {
    const values: InsertUser = { openId: user.openId };
    const updateSet: Record<string, unknown> = {};
    const textFields = ["name", "email", "loginMethod"] as const;
    type TextField = (typeof textFields)[number];
    const assignNullable = (field: TextField) => {
      const value = user[field];
      if (value === undefined) return;
      const normalized = value ?? null;
      values[field] = normalized;
      updateSet[field] = normalized;
    };
    textFields.forEach(assignNullable);
    if (user.lastSignedIn !== undefined) {
      values.lastSignedIn = user.lastSignedIn;
      updateSet.lastSignedIn = user.lastSignedIn;
    }
    if (user.role !== undefined) {
      values.role = user.role;
      updateSet.role = user.role;
    } else if (user.openId === ENV.ownerOpenId) {
      values.role = 'admin';
      updateSet.role = 'admin';
    }
    if (!values.lastSignedIn) {
      values.lastSignedIn = new Date();
    }
    if (Object.keys(updateSet).length === 0) {
      updateSet.lastSignedIn = new Date();
    }
    await db.insert(users).values(values).onDuplicateKeyUpdate({ set: updateSet });
  } catch (error) {
    console.error("[Database] Failed to upsert user:", error);
    throw error;
  }
}

export async function getUserByOpenId(openId: string) {
  const db = await getDb();
  if (!db) {
    console.warn("[Database] Cannot get user: database not available");
    return undefined;
  }
  const result = await db.select().from(users).where(eq(users.openId, openId)).limit(1);
  return result.length > 0 ? result[0] : undefined;
}

// ── Problem CRUD ──

export async function createProblem(data: InsertProblem): Promise<number> {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  const result = await db.insert(problems).values(data);
  return result[0].insertId;
}

export async function getProblemById(id: number): Promise<Problem | undefined> {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  const rows = await db.select().from(problems).where(eq(problems.id, id)).limit(1);
  return rows[0];
}

export async function updateProblem(id: number, data: Partial<InsertProblem>): Promise<void> {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.update(problems).set(data).where(eq(problems.id, id));
}

export async function listProblems(userId?: number, limit = 50): Promise<Problem[]> {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  if (userId) {
    return db.select().from(problems).where(eq(problems.userId, userId)).orderBy(desc(problems.createdAt)).limit(limit);
  }
  return db.select().from(problems).orderBy(desc(problems.createdAt)).limit(limit);
}

// ── Solution Path CRUD ──

export async function createSolutionPath(data: InsertSolutionPath): Promise<number> {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  const result = await db.insert(solutionPaths).values(data);
  return result[0].insertId;
}

export async function getPathsByProblemId(problemId: number): Promise<SolutionPath[]> {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  return db.select().from(solutionPaths).where(eq(solutionPaths.problemId, problemId));
}

export async function updateSolutionPath(id: number, data: Partial<InsertSolutionPath>): Promise<void> {
  const db = await getDb();
  if (!db) throw new Error("Database not available");
  await db.update(solutionPaths).set(data).where(eq(solutionPaths.id, id));
}
