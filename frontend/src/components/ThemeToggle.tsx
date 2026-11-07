import { Icon } from "./Icon";
import { useTheme } from "../hooks/useTheme";

/** Sun/moon segmented control bound to the theme provider. */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  return (
    <div className="theme-toggle" role="group" aria-label="Color theme">
      <button
        type="button"
        className={theme === "light" ? "is-active" : ""}
        onClick={() => setTheme("light")}
        title="Light theme"
        aria-pressed={theme === "light"}
      >
        <Icon name="sun" />
      </button>
      <button
        type="button"
        className={theme === "dark" ? "is-active" : ""}
        onClick={() => setTheme("dark")}
        title="Dark theme (T)"
        aria-pressed={theme === "dark"}
      >
        <Icon name="moon" />
      </button>
    </div>
  );
}
