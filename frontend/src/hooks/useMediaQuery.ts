import { useEffect, useState } from "react";

/** True while the media query matches; re-evaluates on viewport changes. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => (typeof window !== "undefined" ? window.matchMedia(query).matches : false));
  useEffect(() => {
    const media = window.matchMedia(query);
    const onChange = () => setMatches(media.matches);
    onChange();
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/** The shell's phone breakpoint: one-row topbar, tabs behind the menu button. */
export const SHELL_MOBILE_QUERY = "(max-width: 719.98px)";
