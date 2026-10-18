import { useEffect, useState } from "react";

/** Live result of a CSS media query. */
export function useMedia(query: string) {
  const [match, setMatch] = useState(() => typeof matchMedia === "function" && matchMedia(query).matches);
  useEffect(() => {
    const mq = matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

/** Below this width the Activity panel is a drawer over the chat (see .right.drawer). */
export const NARROW = "(max-width: 1180px)";
