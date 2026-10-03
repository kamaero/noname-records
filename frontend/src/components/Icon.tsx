/**
 * Minimal inline icon set — thin stroke, 24-grid.
 * Ported from the redesign reference (icons.js). Add new glyphs here as needed.
 */
import type { CSSProperties } from "react";

export type IconName =
  | "prep"
  | "check"
  | "validate"
  | "books"
  | "mic"
  | "micneo"
  | "wave"
  | "users"
  | "log"
  | "help"
  | "search"
  | "sun"
  | "moon"
  | "plus"
  | "filter"
  | "arrow"
  | "back"
  | "clock"
  | "dots"
  | "play"
  | "upload"
  | "doc"
  | "spark"
  | "flag"
  | "chevron"
  | "download"
  | "cast"
  | "ruble"
  | "menu"
  | "close";

// Each entry is the inner markup of a 24×24 viewBox, stroked with currentColor.
const PATHS: Record<IconName, JSX.Element> = {
  prep: <path d="M4 7h16M4 12h10M4 17h7" />,
  check: <path d="M4 12l5 5L20 6" />,
  validate: (
    <>
      <path d="M12 3l8 4v5c0 4.5-3.2 7.5-8 9-4.8-1.5-8-4.5-8-9V7z" />
      <path d="M9 12l2 2 4-4" />
    </>
  ),
  books: (
    <>
      <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z" />
      <path d="M4 5.5V20.5" />
    </>
  ),
  mic: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </>
  ),
  micneo: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3M19 4l1.5 1.5M19 7l1.5-1.5" />
    </>
  ),
  wave: <path d="M3 12h2l2-6 3 14 3-11 2 6 2-3h4" />,
  users: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 19c0-3 2.5-5 5.5-5s5.5 2 5.5 5" />
      <path d="M16 6.5a3 3 0 0 1 0 5.5M16.5 14c2.4.3 4 2.2 4 5" />
    </>
  ),
  log: (
    <>
      <path d="M5 4h14v16H5z" />
      <path d="M9 9h6M9 13h6M9 17h3" />
    </>
  ),
  help: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.2 9.2a2.8 2.8 0 0 1 5.4.9c0 1.9-2.6 2.2-2.6 4" />
      <path d="M12 17.5v.01" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M20 20l-3.8-3.8" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M5 5l1.8 1.8M17.2 17.2L19 19M19 5l-1.8 1.8M6.8 17.2L5 19" />
    </>
  ),
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z" />,
  plus: <path d="M12 5v14M5 12h14" />,
  filter: <path d="M3 5h18l-7 8v6l-4-2v-4z" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  back: <path d="M19 12H5M11 6l-6 6 6 6" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3.5 2" />
    </>
  ),
  dots: (
    <>
      <circle cx="5" cy="12" r="1.4" />
      <circle cx="12" cy="12" r="1.4" />
      <circle cx="19" cy="12" r="1.4" />
    </>
  ),
  play: <path d="M7 5l12 7-12 7z" />,
  upload: (
    <>
      <path d="M12 16V4M7 9l5-5 5 5" />
      <path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
    </>
  ),
  doc: (
    <>
      <path d="M6 3h8l5 5v13H6z" />
      <path d="M14 3v5h5" />
    </>
  ),
  spark: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />,
  flag: <path d="M5 21V4M5 4h11l-2 4 2 4H5" />,
  chevron: <path d="M9 6l6 6-6 6" />,
  download: (
    <>
      <path d="M12 4v12M7 11l5 5 5-5" />
      <path d="M4 20h16" />
    </>
  ),
  cast: (
    <>
      <circle cx="8" cy="8" r="3" />
      <circle cx="16" cy="8" r="3" />
      <path d="M2.5 19c0-2.8 2.3-4.5 5.5-4.5s5.5 1.7 5.5 4.5M14 14.6c3 .1 5.5 1.8 5.5 4.4" />
    </>
  ),
  ruble: <path d="M8 21V4h5a4 4 0 0 1 0 8H8M5 15h7" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
};

type IconProps = {
  name: IconName;
  className?: string;
  style?: CSSProperties;
};

export function Icon({ name, className, style }: IconProps) {
  return (
    <svg
      className={className}
      style={style}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}
