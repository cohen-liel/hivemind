import { useEffect } from 'react';

/**
 * iOS Safari doesn't resize the layout viewport when the virtual keyboard opens.
 * This hook uses the visualViewport API to track the actual visible height
 * and sets CSS variables that keep fixed containers docked to the keyboard.
 *
 * When the keyboard closes, it resets the scroll position so the page
 * doesn't stay pushed up.
 */
export function useIOSViewport() {
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;

    let prevHeight = vv.height;
    const fullHeight = window.innerHeight;

    let rafId: number | null = null;

    const update = () => {
      // Debounce via rAF to prevent layout thrashing from rapid resize events (UI-04)
      if (rafId !== null) return;
      rafId = requestAnimationFrame(() => {
        rafId = null;
        const h = vv.height;
        document.documentElement.style.setProperty('--app-height', `${h}px`);
        document.documentElement.style.setProperty('--app-offset', `${vv.offsetTop}px`);

        // Keyboard closed: height grew back toward full screen
        if (h > prevHeight && h >= fullHeight - 50) {
          // Reset any iOS scroll offset so page snaps back
          window.scrollTo(0, 0);
          document.documentElement.style.setProperty('--app-offset', '0px');
        }

        prevHeight = h;
      });
    };

    update();
    vv.addEventListener('resize', update);
    vv.addEventListener('scroll', update);

    // Also reset on blur (input loses focus = keyboard closing)
    const onBlur = (e: FocusEvent) => {
      const target = e.target as HTMLElement;
      if (target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.tagName === 'SELECT') {
        // Small delay to let iOS finish the keyboard animation
        setTimeout(() => {
          window.scrollTo(0, 0);
          document.documentElement.style.setProperty('--app-height', `${window.visualViewport?.height ?? fullHeight}px`);
          document.documentElement.style.setProperty('--app-offset', '0px');
        }, 100);
      }
    };
    document.addEventListener('focusout', onBlur);

    return () => {
      vv.removeEventListener('resize', update);
      vv.removeEventListener('scroll', update);
      document.removeEventListener('focusout', onBlur);
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
  }, []);
}
