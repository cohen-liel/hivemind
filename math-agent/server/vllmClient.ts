/**
 * vLLM Client — calls an external vLLM server running on Vast.ai (or any
 * OpenAI-compatible endpoint).  Falls back to the built-in Forge LLM when
 * the external endpoint is not configured or unreachable.
 */

import { ENV } from "./_core/env";
import { invokeLLM, type InvokeParams, type InvokeResult } from "./_core/llm";

// ── Types ──

export interface VllmStatus {
  available: boolean;
  model: string | null;
  url: string | null;
  error: string | null;
}

// ── Helpers ──

function getVllmBaseUrl(): string | null {
  const url = ENV.vastaiVllmUrl?.trim();
  if (!url) return null;
  // Strip trailing /v1 or / so we can append /v1/chat/completions ourselves
  return url.replace(/\/v1\/?$/, "").replace(/\/$/, "");
}

// ── Public API ──

/**
 * Check whether the external vLLM endpoint is reachable and which model it
 * serves.
 */
export async function checkVllmStatus(): Promise<VllmStatus> {
  const base = getVllmBaseUrl();
  if (!base) {
    return { available: false, model: null, url: null, error: "VASTAI_VLLM_URL not configured" };
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8_000);

    const res = await fetch(`${base}/v1/models`, {
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!res.ok) {
      return { available: false, model: null, url: base, error: `HTTP ${res.status}` };
    }

    const body = (await res.json()) as { data?: Array<{ id: string }> };
    const modelId = body.data?.[0]?.id ?? null;

    return { available: true, model: modelId, url: base, error: null };
  } catch (err) {
    return {
      available: false,
      model: null,
      url: base,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

/**
 * Invoke the external vLLM model.  The API is OpenAI-compatible so we build
 * the request manually (the built-in `invokeLLM` helper always targets
 * Forge).
 *
 * If the external endpoint is unavailable we transparently fall back to the
 * built-in Forge LLM.
 */
export async function invokeVllm(
  params: InvokeParams & { temperature?: number }
): Promise<InvokeResult & { backend: "vllm" | "forge" }> {
  const base = getVllmBaseUrl();

  if (base) {
    try {
      const result = await callExternalVllm(base, params);
      return { ...result, backend: "vllm" };
    } catch (err) {
      console.warn(
        `[vllm] External endpoint failed, falling back to Forge: ${err instanceof Error ? err.message : err}`
      );
    }
  }

  // Fallback to built-in Forge LLM
  const result = await invokeLLM(params);
  return { ...result, backend: "forge" };
}

// ── Internal ──

async function callExternalVllm(
  baseUrl: string,
  params: InvokeParams & { temperature?: number }
): Promise<InvokeResult> {
  const messages = params.messages.map((m) => ({
    role: m.role,
    content: typeof m.content === "string" ? m.content : JSON.stringify(m.content),
  }));

  const payload: Record<string, unknown> = {
    messages,
    max_tokens: params.maxTokens ?? params.max_tokens ?? 4096,
    temperature: params.temperature ?? 0.7,
  };

  // vLLM supports response_format for guided decoding
  const rf = params.responseFormat ?? params.response_format;
  if (rf && rf.type === "json_schema") {
    payload.response_format = rf;
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120_000); // 2 min

  const res = await fetch(`${baseUrl}/v1/chat/completions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal: controller.signal,
  });
  clearTimeout(timeout);

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`vLLM ${res.status}: ${text.slice(0, 300)}`);
  }

  return (await res.json()) as InvokeResult;
}
