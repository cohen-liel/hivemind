/**
 * useUIStatePersistence — Persist and restore UI layout state via localStorage.
 *
 * Handles: panel split ratio, selected tabs (desktop/mobile), scroll positions.
 * All reads/writes are wrapped in try-catch for private browsing / quota errors.
 */

import { useCallback, useEffect, useRef } from 'react';
import type { DesktopTab, MobileView } from '../reducers/projectReducer';

// ── localStorage keys ──
const PANEL_WIDTH_KEY = 'hivemind-panel-width';
const DESKTOP_TAB_KEY = 'hivemind-desktop-tab';
const MOBILE_VIEW_KEY = 'hivemind-mobile-view';
const SCROLL_PREFIX = 'hivemind-scroll-';

// ── Helpers ──

function safeGetItem(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSetItem(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Quota exceeded or private browsing — silently ignore
  }
}

// ============================================================================
// Panel Width Persistence
// ============================================================================

const VALID_DESKTOP_TABS = new Set<DesktopTab>([
  'hivemind', 'agents', 'plan', 'code', 'diff', 'trace',
]);
const VALID_MOBILE_VIEWS = new Set<MobileView>([
  'orchestra', 'activity', 'code', 'changes', 'plan', 'trace',
]);

/** Get persisted panel width (percentage 30-80), default 65 */
export function getPersistedPanelWidth(): number {
  const stored = safeGetItem(PANEL_WIDTH_KEY);
  if (stored) {
    const val = parseFloat(stored);
    if (!isNaN(val) && val >= 30 && val <= 80) return val;
  }
  return 65;
}

/** Save panel width */
export function setPersistedPanelWidth(width: number): void {
  safeSetItem(PANEL_WIDTH_KEY, String(Math.round(width * 10) / 10));
}

// ============================================================================
// Tab Persistence
// ============================================================================

/** Get persisted desktop tab */
export function getPersistedDesktopTab(): DesktopTab {
  const stored = safeGetItem(DESKTOP_TAB_KEY);
  if (stored && VALID_DESKTOP_TABS.has(stored as DesktopTab)) {
    return stored as DesktopTab;
  }
  return 'hivemind';
}

/** Save desktop tab */
export function setPersistedDesktopTab(tab: DesktopTab): void {
  safeSetItem(DESKTOP_TAB_KEY, tab);
}

/** Get persisted mobile view */
export function getPersistedMobileView(): MobileView {
  const stored = safeGetItem(MOBILE_VIEW_KEY);
  if (stored && VALID_MOBILE_VIEWS.has(stored as MobileView)) {
    return stored as MobileView;
  }
  return 'orchestra';
}

/** Save mobile view */
export function setPersistedMobileView(view: MobileView): void {
  safeSetItem(MOBILE_VIEW_KEY, view);
}

// ============================================================================
// Scroll Position Persistence
// ============================================================================

/**
 * Hook to persist and restore scroll position for a scrollable element.
 * Saves position on scroll (debounced) and restores on mount/reconnection.
 *
 * @param scrollKey - Unique identifier for this scroll container (e.g. "activity-panel")
 * @param connected - WebSocket connected state (triggers restore on reconnection)
 */
export function useScrollPersistence(
  scrollKey: string,
  connected: boolean,
): React.RefCallback<HTMLElement> {
  const elRef = useRef<HTMLElement | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevConnectedRef = useRef(connected);
  const fullKey = SCROLL_PREFIX + scrollKey;

  // Save scroll position (debounced 300ms)
  const saveScroll = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      if (elRef.current) {
        safeSetItem(fullKey, String(Math.round(elRef.current.scrollTop)));
      }
    }, 300);
  }, [fullKey]);

  // Restore scroll position
  const restoreScroll = useCallback(() => {
    const stored = safeGetItem(fullKey);
    if (stored && elRef.current) {
      const scrollTop = parseInt(stored, 10);
      if (!isNaN(scrollTop) && scrollTop >= 0) {
        // Use requestAnimationFrame to ensure DOM is ready
        requestAnimationFrame(() => {
          if (elRef.current) {
            elRef.current.scrollTop = scrollTop;
          }
        });
      }
    }
  }, [fullKey]);

  // Restore on reconnection
  useEffect(() => {
    if (connected && !prevConnectedRef.current) {
      // Just reconnected — restore scroll
      restoreScroll();
    }
    prevConnectedRef.current = connected;
  }, [connected, restoreScroll]);

  // Ref callback: attach/detach scroll listener and restore on mount
  const refCallback = useCallback(
    (node: HTMLElement | null) => {
      // Detach from previous node
      if (elRef.current) {
        elRef.current.removeEventListener('scroll', saveScroll);
      }

      elRef.current = node;

      if (node) {
        node.addEventListener('scroll', saveScroll, { passive: true });
        // Restore on initial mount
        restoreScroll();
      }
    },
    [saveScroll, restoreScroll],
  );

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, []);

  return refCallback;
}

// ============================================================================
// Resizable Panel Hook
// ============================================================================

interface UseResizablePanelResult {
  /** Current panel width percentage */
  panelWidth: number;
  /** Whether user is currently dragging the divider */
  isDragging: boolean;
  /** Props to spread on the drag handle element */
  dragHandleProps: {
    onMouseDown: (e: React.MouseEvent) => void;
    onTouchStart: (e: React.TouchEvent) => void;
    role: string;
    'aria-label': string;
    'aria-orientation': 'vertical' | 'horizontal';
    tabIndex: number;
    onKeyDown: (e: React.KeyboardEvent) => void;
    style: React.CSSProperties;
  };
}

/**
 * Hook for a resizable split panel. Returns current width and drag handle props.
 * Persists panel width to localStorage on drag end.
 */
export function useResizablePanel(
  containerRef: React.RefObject<HTMLElement | null>,
): UseResizablePanelResult {
  const [panelWidth, setPanelWidth] = useStateWithInit(getPersistedPanelWidth);
  const isDraggingRef = useRef(false);
  const [isDragging, setIsDragging] = useStateWithInit(() => false);

  const handleDragStart = useCallback((clientX: number) => {
    isDraggingRef.current = true;
    setIsDragging(true);

    const container = containerRef.current;
    if (!container) return;

    const onMove = (moveClientX: number) => {
      if (!isDraggingRef.current || !container) return;
      const rect = container.getBoundingClientRect();
      const newWidth = ((moveClientX - rect.left) / rect.width) * 100;
      const clamped = Math.min(80, Math.max(30, newWidth));
      setPanelWidth(clamped);
    };

    const onEnd = () => {
      isDraggingRef.current = false;
      setIsDragging(false);
      // Persist final width
      const container = containerRef.current;
      if (container) {
        // Read current width from state via closure
        // We need to get the latest value
      }
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', onEnd);
      document.removeEventListener('touchmove', handleTouchMove);
      document.removeEventListener('touchend', onEnd);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    const handleMouseMove = (e: MouseEvent) => {
      e.preventDefault();
      onMove(e.clientX);
    };

    const handleTouchMove = (e: TouchEvent) => {
      if (e.touches.length > 0) {
        onMove(e.touches[0].clientX);
      }
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', onEnd);
    document.addEventListener('touchmove', handleTouchMove, { passive: true });
    document.addEventListener('touchend', onEnd);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    // Prevent text selection during drag
    void clientX;
  }, [containerRef, setPanelWidth]);

  // Persist on change (debounced via effect)
  const persistTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (persistTimerRef.current) clearTimeout(persistTimerRef.current);
    persistTimerRef.current = setTimeout(() => {
      setPersistedPanelWidth(panelWidth);
    }, 200);
    return () => {
      if (persistTimerRef.current) clearTimeout(persistTimerRef.current);
    };
  }, [panelWidth]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      setPanelWidth(w => Math.max(30, w - 2));
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      setPanelWidth(w => Math.min(80, w + 2));
    }
  }, [setPanelWidth]);

  const dragHandleProps = {
    onMouseDown: (e: React.MouseEvent) => {
      e.preventDefault();
      handleDragStart(e.clientX);
    },
    onTouchStart: (e: React.TouchEvent) => {
      if (e.touches.length > 0) {
        handleDragStart(e.touches[0].clientX);
      }
    },
    role: 'separator',
    'aria-label': 'Resize panel divider',
    'aria-orientation': 'vertical' as const,
    tabIndex: 0,
    onKeyDown: handleKeyDown,
    style: {
      width: '6px',
      cursor: 'col-resize',
      background: isDragging ? 'var(--accent-blue, #6366f1)' : 'transparent',
      transition: isDragging ? 'none' : 'background 0.15s ease',
      flexShrink: 0,
      position: 'relative' as const,
      zIndex: 10,
    } as React.CSSProperties,
  };

  return { panelWidth, isDragging, dragHandleProps };
}

// ── Internal helper ──

import { useState } from 'react';

function useStateWithInit<T>(init: () => T): [T, React.Dispatch<React.SetStateAction<T>>] {
  return useState(init);
}
