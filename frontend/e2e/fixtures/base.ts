import { test as base, type Page, type BrowserContext } from '@playwright/test';

// ============================================================================
// Auth bypass helpers
// ============================================================================

/** Inject the auth token into localStorage and a <meta> tag so the app skips login. */
async function injectAuth(page: Page, token: string): Promise<void> {
  await page.addInitScript((tkn: string) => {
    localStorage.setItem('hivemind-auth-token', tkn);
    // Inject meta tag for components that read auth from <meta>
    const meta = document.createElement('meta');
    meta.name = 'hivemind-auth-token';
    meta.content = tkn;
    document.head.appendChild(meta);
  }, token);
}

// ============================================================================
// WebSocket mock helpers
// ============================================================================

interface MockWSOptions {
  /** Whether the mock WS should auto-send an auth_ok response. Default: true */
  autoAuth?: boolean;
}

interface MockWebSocketHandle {
  /** Send a JSON event to the client as if it came from the server. */
  send: (event: Record<string, unknown>) => Promise<void>;
  /** Close the WebSocket connection. */
  close: (code?: number) => Promise<void>;
}

// ============================================================================
// Custom fixtures
// ============================================================================

interface HivemindFixtures {
  /** Page with auth token pre-injected (skips login screen). */
  authedPage: Page;
  /** Auth token used for the session. */
  authToken: string;
  /** Set up a WebSocket route mock that intercepts /ws connections. Returns a handle to send events. */
  mockWebSocket: (options?: MockWSOptions) => Promise<MockWebSocketHandle>;
}

/**
 * Extended Playwright test with Hivemind-specific fixtures:
 * - `authedPage`: Page with auth bypass (localStorage + meta tag injection)
 * - `authToken`: The device token used for auth
 * - `mockWebSocket`: Intercept and mock WebSocket connections
 */
export const test = base.extend<HivemindFixtures>({
  authToken: async ({}, use) => {
    // Use a stable test token
    await use('test-e2e-token-0000');
  },

  authedPage: async ({ page, authToken }, use) => {
    await injectAuth(page, authToken);
    await use(page);
  },

  mockWebSocket: async ({ page }, use) => {
    const setupMock = async (options: MockWSOptions = {}): Promise<MockWebSocketHandle> => {
      const { autoAuth = true } = options;

      const handle: MockWebSocketHandle = {
        send: async () => { /* replaced once route is set up */ },
        close: async () => { /* replaced once route is set up */ },
      };

      await page.routeWebSocket(/\/ws/, (ws) => {
        // Auto-respond to auth message
        if (autoAuth) {
          ws.onMessage((msg) => {
            try {
              const data = JSON.parse(String(msg));
              if (data.type === 'auth' || data.token) {
                ws.send(JSON.stringify({ type: 'auth_ok', user_id: 1 }));
              }
            } catch {
              // ignore non-JSON messages
            }
          });
        }

        handle.send = async (event: Record<string, unknown>): Promise<void> => {
          ws.send(JSON.stringify(event));
        };

        handle.close = async (code = 1000): Promise<void> => {
          ws.close({ code, reason: 'test-close' });
        };
      });

      return handle;
    };

    await use(setupMock);
  },
});

export { expect } from '@playwright/test';
