import { useEffect } from 'react';

/**
 * Dynamically sets the page title. Resets to 'Nexus' on unmount.
 * Usage: usePageTitle('Dashboard') => 'Nexus — Dashboard'
 *        usePageTitle('My Project', 'running') => 'Nexus — My Project ● Running'
 */
export function usePageTitle(subtitle?: string, status?: string) {
  useEffect(() => {
    const parts = ['Nexus'];
    if (subtitle) parts.push(subtitle);
    if (status && status !== 'idle') {
      const statusLabel = status.charAt(0).toUpperCase() + status.slice(1);
      document.title = `${parts.join(' — ')} ● ${statusLabel}`;
    } else {
      document.title = parts.join(' — ');
    }
    return () => { document.title = 'Nexus'; };
  }, [subtitle, status]);
}
