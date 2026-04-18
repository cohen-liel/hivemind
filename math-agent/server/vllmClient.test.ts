import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mock the ENV module
vi.mock("./_core/env", () => ({
  ENV: {
    vastaiVllmUrl: "",
    forgeApiUrl: "https://forge.manus.im",
    forgeApiKey: "test-key",
  },
}));

// Mock invokeLLM
vi.mock("./_core/llm", () => ({
  invokeLLM: vi.fn().mockResolvedValue({
    id: "forge-123",
    created: Date.now(),
    model: "gemini-2.5-flash",
    choices: [
      {
        index: 0,
        message: { role: "assistant", content: "test response" },
        finish_reason: "stop",
      },
    ],
  }),
}));

import { checkVllmStatus, invokeVllm } from "./vllmClient";
import { ENV } from "./_core/env";

describe("vllmClient", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  describe("checkVllmStatus", () => {
    it("returns unavailable when VASTAI_VLLM_URL is not set", async () => {
      (ENV as any).vastaiVllmUrl = "";
      const status = await checkVllmStatus();
      expect(status.available).toBe(false);
      expect(status.error).toContain("not configured");
    });

    it("returns available when vLLM endpoint responds", async () => {
      (ENV as any).vastaiVllmUrl = "http://1.2.3.4:8000/v1";
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            data: [{ id: "TIGER-Lab/MAmmoTH2-7B" }],
          }),
      }) as any;

      const status = await checkVllmStatus();
      expect(status.available).toBe(true);
      expect(status.model).toBe("TIGER-Lab/MAmmoTH2-7B");
    });

    it("returns unavailable when vLLM endpoint is down", async () => {
      (ENV as any).vastaiVllmUrl = "http://1.2.3.4:8000/v1";
      global.fetch = vi.fn().mockRejectedValue(new Error("Connection refused")) as any;

      const status = await checkVllmStatus();
      expect(status.available).toBe(false);
      expect(status.error).toContain("Connection refused");
    });
  });

  describe("invokeVllm", () => {
    it("falls back to Forge when VASTAI_VLLM_URL is not set", async () => {
      (ENV as any).vastaiVllmUrl = "";
      const result = await invokeVllm({
        messages: [{ role: "user", content: "test" }],
      });
      expect(result.backend).toBe("forge");
    });

    it("uses vLLM when endpoint is available", async () => {
      (ENV as any).vastaiVllmUrl = "http://1.2.3.4:8000/v1";
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "vllm-123",
            created: Date.now(),
            model: "TIGER-Lab/MAmmoTH2-7B",
            choices: [
              {
                index: 0,
                message: { role: "assistant", content: "vllm response" },
                finish_reason: "stop",
              },
            ],
          }),
      }) as any;

      const result = await invokeVllm({
        messages: [{ role: "user", content: "test" }],
      });
      expect(result.backend).toBe("vllm");
      expect(result.choices[0].message.content).toBe("vllm response");
    });

    it("falls back to Forge when vLLM endpoint fails", async () => {
      (ENV as any).vastaiVllmUrl = "http://1.2.3.4:8000/v1";
      global.fetch = vi.fn().mockRejectedValue(new Error("timeout")) as any;

      const result = await invokeVllm({
        messages: [{ role: "user", content: "test" }],
      });
      expect(result.backend).toBe("forge");
    });
  });
});
