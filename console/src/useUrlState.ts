import { useCallback, useEffect, useState } from "react";

export interface Route {
  runId: string | null;
  turn: number | null;
}

function read(): Route {
  const match = /^\/runs\/([^/]+)/.exec(window.location.pathname);
  const turn = new URLSearchParams(window.location.search).get("turn");
  return {
    runId: match ? decodeURIComponent(match[1]) : null,
    turn: turn === null ? null : Number(turn),
  };
}

function href(route: Route): string {
  if (route.runId === null) return "/";
  const query = route.turn === null ? "" : `?turn=${route.turn}`;
  return `/runs/${encodeURIComponent(route.runId)}${query}`;
}

/**
 * The selected run and turn live in the URL, not in component state.
 * That is the difference between being able to send someone a link to the
 * exact turn where a tap missed, and telling them which one to scroll to.
 */
export function useRoute(): [Route, (next: Route, replace?: boolean) => void] {
  const [route, setRoute] = useState<Route>(read);

  useEffect(() => {
    const onPop = () => setRoute(read());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback((next: Route, replace = false) => {
    const url = href(next);
    if (url === window.location.pathname + window.location.search) return;
    window.history[replace ? "replaceState" : "pushState"]({}, "", url);
    setRoute(next);
  }, []);

  return [route, navigate];
}
