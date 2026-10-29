import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./tokens.css";
import "./styles.css";
import Entry from "./Entry";
import BrandPage from "./Brand";
import MarketsPage from "./Markets";
import OpportunitiesPage from "./Opportunities";
import InvestigationPage from "./Investigation";
import DeliverPage from "./Deliver";
import PlanPage from "./Plan";
import LaunchPage from "./Act";

function App() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const on = () => setPath(window.location.pathname);
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  const m = path.match(/^\/p\/([\w-]+)(?:\/markets(?:\/(all|scan|compare))?)?/);
  if (!m) return <Entry />;
  const pl = path.match(/\/plan\/([a-z-]+)(?:\/(retail|popup|creator|community|digital))?\/?$/);
  if (pl && pl[2]) return <InvestigationPage key={path} id={m[1]} city={pl[1]} method={pl[2]} />;
  if (pl) return <PlanPage key={path} id={m[1]} city={pl[1]} />;
  // Launch (owner, 28 Oct 2026): v4's Prepare and Act & adapt are one checklist; their old addresses lead there
  const ln = path.match(/\/(launch|prepare|act)\/([a-z-]+)\/?$/);
  if (ln && ln[1] !== "launch") window.history.replaceState(null, "", `/p/${m[1]}/launch/${ln[2]}`);
  if (ln) return <LaunchPage key={ln[2]} id={m[1]} city={ln[2]} />;
  const dl = path.match(/\/deliver\/([\w~-]+)\/?$/);  // recipient-keyed ids carry "~" (P65-R2)
  if (dl) return <DeliverPage key={path} id={m[1]} pkg={dl[1]} />;
  const inv = path.match(/\/opportunities\/([a-z-]+)\/(retail|popup|creator|community|digital)\/?$/);
  if (inv) return <InvestigationPage key={path} id={m[1]} city={inv[1]} method={inv[2]} />;
  const o = path.match(/\/opportunities(?:\/(research))?/);
  if (o) return <OpportunitiesPage key={m[1]} id={m[1]} view={o[1] ? "research" : "auto"} />;
  if (path.includes("/markets")) return <MarketsPage key={m[1]} id={m[1]} view={(m[2] as "all" | "scan" | "compare") || "auto"} />;
  return <BrandPage key={m[1]} id={m[1]} />;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
