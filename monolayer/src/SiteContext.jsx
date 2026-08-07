import { createContext, useContext } from "react";

// Shared mutable site state: lenis instance, reduced-motion flag.
export const SiteStateContext = createContext({
  lenis: null,
  reducedMotion: false,
  isTouch: false,
});

export const useSite = () => useContext(SiteStateContext);
