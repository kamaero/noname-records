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

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}
