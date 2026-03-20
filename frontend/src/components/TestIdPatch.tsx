/**
 * TestIdPatch — Data attribute helpers for E2E test selectors.
 *
 * This module documents the data-testid attributes that tests rely on.
 * In practice, Hivemind components use semantic ARIA roles, labels, and
 * CSS class selectors instead of data-testid attributes, so no component
 * patches are currently required.
 *
 * If future tests need stable selectors that can't be expressed via ARIA,
 * add data-testid attributes to the components here and document the mapping.
 *
 * Pattern:
 *   export const TEST_IDS = {
 *     COMPONENT_ELEMENT: 'component-element',
 *   } as const;
 *
 * Then in the component:
 *   <div data-testid={TEST_IDS.COMPONENT_ELEMENT} />
 */

// Currently no data-testid patches are needed.
// All E2E tests use ARIA roles, labels, text content, and CSS class selectors.
// This file exists as a placeholder per the task spec.

export const TEST_IDS = {
  // Placeholder — add entries here if semantic selectors prove insufficient.
  // Example:
  // WS_RECONNECT_BANNER: 'ws-reconnect-banner',
  // ERROR_BOUNDARY_FALLBACK: 'error-boundary-fallback',
} as const;

export default TEST_IDS;
