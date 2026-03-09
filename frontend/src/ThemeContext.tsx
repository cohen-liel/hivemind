import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from 'react';

/** Supported theme modes */
export type Theme = 'dark' | 'light';

/** Shape of the theme context value */
export interface ThemeContextValue {
  /** Current active theme */
  theme: Theme;
  /** Toggle between dark and light themes */
  toggleTheme: () => void;
  /** Explicitly set a specific theme */
  setTheme: (theme: Theme) => void;
}

const STORAGE_KEY = 'nexus-theme';
const DATA_ATTR = 'data-theme';

/**
 * Determine the initial theme:
 * 1. Check localStorage for persisted preference
 * 2. Respect prefers-color-scheme media query
 * 3. Default to 'dark' (as per spec: keep dark as default)
 */
function getInitialTheme(): Theme {
  // 1. Check localStorage
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') {
      return stored;
    }
  } catch {
    // localStorage may be unavailable (private browsing, etc.)
  }

  // 2. Respect OS-level preference
  if (typeof window !== 'undefined' && window.matchMedia) {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)');
    if (prefersDark.matches) {
      return 'dark';
    }
    const prefersLight = window.matchMedia('(prefers-color-scheme: light)');
    if (prefersLight.matches) {
      return 'light';
    }
  }

  // 3. Default to dark
  return 'dark';
}

/** Apply theme to the document root element */
function applyThemeToDOM(theme: Theme): void {
  document.documentElement.setAttribute(DATA_ATTR, theme);
}

/** Validate that a value is a valid Theme */
function isValidTheme(value: unknown): value is Theme {
  return value === 'dark' || value === 'light';
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

/** Props for the ThemeProvider component */
interface ThemeProviderProps {
  children: ReactNode;
}

/**
 * ThemeProvider — manages dark/light theme state with:
 * - localStorage persistence under 'nexus-theme' key
 * - prefers-color-scheme media query for initial theme
 * - Cross-tab sync via storage event
 * - Applies data-theme attribute to <html> for CSS variable switching
 */
export function ThemeProvider({ children }: ThemeProviderProps): React.ReactElement {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);

  // Apply theme to DOM on mount and whenever it changes
  useEffect(() => {
    applyThemeToDOM(theme);

    // Persist to localStorage
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Silently fail if localStorage is unavailable
    }
  }, [theme]);

  // Cross-tab sync: listen for storage changes from other tabs
  useEffect(() => {
    const handleStorage = (e: StorageEvent): void => {
      if (e.key === STORAGE_KEY && e.newValue && isValidTheme(e.newValue)) {
        setThemeState(e.newValue);
      }
    };

    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  // Listen for OS-level theme changes (only if no stored preference)
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

    const handleChange = (e: MediaQueryListEvent): void => {
      // Only auto-switch if user hasn't explicitly set a preference
      try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (!stored) {
          setThemeState(e.matches ? 'dark' : 'light');
        }
      } catch {
        // localStorage unavailable — follow OS preference
        setThemeState(e.matches ? 'dark' : 'light');
      }
    };

    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, []);

  const toggleTheme = useCallback((): void => {
    setThemeState(prev => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  const setTheme = useCallback((newTheme: Theme): void => {
    setThemeState(newTheme);
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

/**
 * Hook to consume the theme context.
 * Must be used within a ThemeProvider.
 */
export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}
