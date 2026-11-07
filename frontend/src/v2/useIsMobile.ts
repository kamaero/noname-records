import { useEffect, useState } from "react";

const MOBILE_QUERY = "(max-width: 760px)";

export function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(() => (typeof window !== "undefined" ? window.matchMedia(MOBILE_QUERY).matches : false));
  useEffect(() => {
    const media = window.matchMedia(MOBILE_QUERY);
    const onChange = () => setMobile(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return mobile;
}

export function plural(n: number, one: string, _few: string, many: string): string {
  return n === 1 ? one : many;
}
