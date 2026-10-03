import { Icon } from "./Icon";
import { useTheme } from "../hooks/useTheme";

/** Sun/moon segmented control bound to the theme provider. */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  return (
    <div className="theme-toggle" role="group" aria-label="Тема оформления">
      <button
        type="button"
        className={theme === "light" ? "is-active" : ""}
        onClick={() => setTheme("light")}
        title="Светлая тема"
        aria-pressed={theme === "light"}
      >
        <Icon name="sun" />
      </button>
      <button
        type="button"
        className={theme === "dark" ? "is-active" : ""}
        onClick={() => setTheme("dark")}
        title="Тёмная тема (T)"
        aria-pressed={theme === "dark"}
      >
        <Icon name="moon" />
      </button>
    </div>
  );
}
