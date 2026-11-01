import { useCallback, useEffect, useRef } from "react";
import { crossfade } from "../management/motion.ts";
import type { BuildAgentOutput, BuildFact } from "../types.ts";
import type { View } from "./feed.tsx";
import { NARROW, calm } from "./motion.ts";

// How long each fact stays lit during the review's playback.
const FACT_MS = 520;

/**
 * At the review, the facts light up one after the other on the page they came from, and then
 * both built sites show; once per profile. It returns `stop`: any action of the merchant stops it.
 * `setView` and `setFact` must keep their identity, as React's state setters do.
 */
export function useReviewPlayback(
  profile: BuildAgentOutput | null,
  pageCount: number,
  setView: (view: View) => void,
  setFact: (fact: BuildFact | null) => void,
): () => void {
  const playback = useRef<number | null>(null);
  const reviewed = useRef<BuildAgentOutput | null>(null);
  const stop = useCallback(() => {
    if (playback.current !== null) window.clearTimeout(playback.current);
    playback.current = null;
  }, []);

  useEffect(() => {
    if (!profile || reviewed.current === profile) return;
    reviewed.current = profile;
    const builtView: View = window.innerWidth <= NARROW ? "manage" : "both";
    const boxed = profile.facts.filter((item) => item.box);
    if (boxed.length === 0 || pageCount === 0 || window.innerWidth <= NARROW || calm()) {
      playback.current = window.setTimeout(() => setView(builtView), 0);
      return stop;
    }
    let next = 0;
    const light = () => {
      if (next === boxed.length) {
        playback.current = null;
        crossfade(() => {
          setFact(null);
          setView(builtView);
        });
        return;
      }
      setFact(boxed[next]);
      next += 1;
      playback.current = window.setTimeout(light, FACT_MS);
    };
    playback.current = window.setTimeout(() => {
      setView("public");
      light();
    }, 0);
    return stop;
  }, [profile, pageCount, setView, setFact, stop]);

  return stop;
}
