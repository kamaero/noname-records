/**
 * Theme + view-density state for the redesigned shell.
 *
 * The design system reads everything from CSS custom properties on <html>
 * (see styles.css). This hook just keeps three attributes/properties in sync
 * with localStorage:
 *   - data-theme="dark|light"   → swaps the token palette (night/day)
 *   - data-density="compact|balanced|spacious" → row height / padding scale
 *   - --accent-h                → accent hue (OKLCH), re-colors the whole UI
 *
 * A tiny inline script in index.html applies the stored theme before first
 * paint to avoid a flash; this provider takes over once React mounts.
 */
import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type Theme = "dark" | "light";
export type Density = "compact" | "balanced" | "spacious";

const THEME_KEY = "bt-theme";
const DENSITY_KEY = "bt-density";
const ACCENT_KEY = "bt-accent";
const DEFAULT_ACCENT = "154"; // emerald

function readStored<T extends string>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  return (window.localStorage.getItem(key) as T) || fallback;
}

function initialTheme(): Theme {
  const stored = typeof window !== "undefined" && window.localStorage.getItem(THEME_KEY);
  if (stored === "dark" || stored === "light") return stored;
  // Honour the OS preference on first visit.
  if (typeof window !== "undefined" && window.matchMedia) {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  return "dark";
}

type ThemeContextValue = {
  theme: Theme;
  density: Density;
  accentH: string;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
  setDensity: (d: Density) => void;
  setAccentH: (h: string) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(initialTheme);
  const [density, setDensityState] = useState<Density>(() =>
    readStored<Density>(DENSITY_KEY, "balanced"),
  );
  const [accentH, setAccentHState] = useState<string>(() =>
    readStored(ACCENT_KEY, DEFAULT_ACCENT),
  );

  // Reflect state onto <html> + persist.
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    window.localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    document.documentElement.setAttribute("data-density", density);
    window.localStorage.setItem(DENSITY_KEY, density);
  }, [density]);

  useEffect(() => {
    document.documentElement.style.setProperty("--accent-h", accentH);
    window.localStorage.setItem(ACCENT_KEY, accentH);
  }, [accentH]);

  const setTheme = useCallback((t: Theme) => setThemeState(t), []);
  const toggleTheme = useCallback(
    () => setThemeState((t) => (t === "dark" ? "light" : "dark")),
    [],
  );
  const setDensity = useCallback((d: Density) => setDensityState(d), []);
  const setAccentH = useCallback((h: string) => setAccentHState(h), []);

  // Keyboard: "t" toggles theme (ignored while typing in a field).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== "t") return;
      const el = e.target as HTMLElement | null;
      if (el && /^(input|textarea|select)$/i.test(el.tagName)) return;
      if (el?.isContentEditable) return;
      toggleTheme();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleTheme]);

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, density, accentH, setTheme, toggleTheme, setDensity, setAccentH }),
    [theme, density, accentH, setTheme, toggleTheme, setDensity, setAccentH],
  );

  return createElement(ThemeContext.Provider, { value }, children);
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within <ThemeProvider>");
  return ctx;
}
