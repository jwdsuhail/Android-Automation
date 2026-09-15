import { useCallback, useEffect, useState } from "react";

/**
 * Where the app is, as data.
 *
 * `view` is the section; the rest is what that section has selected. Runs keep
 * the addresses they already had, so every link written before cases existed
 * still resolves.
 */
export interface Route {
  view: "runs" | "cases";
  runId: string | null;
  turn: number | null;
  caseId: string | null;
  /** `new` for an unsaved case, `edit` for an existing one being changed. */
  mode: "new" | "edit" | null;
}

export const HOME: Route = {
  view: "runs",
  runId: null,
  turn: null,
  caseId: null,
  mode: null,
};

function read(): Route {
  const path = window.location.pathname;
  const turnParam = new URLSearchParams(window.location.search).get("turn");
  const turn = turnParam === null ? null : Number(turnParam);

  if (path === "/cases/new") {
    return { ...HOME, view: "cases", mode: "new" };
  }

  const caseMatch = /^\/cases\/([^/]+?)(?:\/(edit))?\/?$/.exec(path);
  if (caseMatch) {
    return {
      ...HOME,
      view: "cases",
      caseId: decodeURIComponent(caseMatch[1]),
      mode: caseMatch[2] === "edit" ? "edit" : null,
    };
  }
  if (path === "/cases" || path === "/cases/") {
    return { ...HOME, view: "cases" };
  }

  const runMatch = /^\/runs\/([^/]+)/.exec(path);
  return {
    ...HOME,
    runId: runMatch ? decodeURIComponent(runMatch[1]) : null,
    turn,
  };
}

function href(route: Route): string {
  if (route.view === "cases") {
    if (route.mode === "new") return "/cases/new";
    if (route.caseId === null) return "/cases";
    const base = `/cases/${encodeURIComponent(route.caseId)}`;
    return route.mode === "edit" ? `${base}/edit` : base;
  }
  if (route.runId === null) return "/";
  const query = route.turn === null ? "" : `?turn=${route.turn}`;
  return `/runs/${encodeURIComponent(route.runId)}${query}`;
}

/**
 * The selected run and turn live in the URL, not in component state.
 * That is the difference between being able to send someone a link to the
 * exact turn where a tap missed, and telling them which one to scroll to.
 * A case is worth addressing for the same reason.
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
