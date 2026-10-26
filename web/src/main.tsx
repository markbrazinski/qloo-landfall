import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./tokens.css";
import "./styles.css";
import Entry from "./Entry";
import BrandPage from "./Brand";
import MarketsPage from "./Markets";

function App() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const on = () => setPath(window.location.pathname);
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  const m = path.match(/^\/p\/([\w-]+)(?:\/markets(?:\/(all|scan))?)?/);
  if (!m) return <Entry />;
  if (path.includes("/markets")) return <MarketsPage key={m[1]} id={m[1]} view={(m[2] as "all" | "scan") || "auto"} />;
  return <BrandPage key={m[1]} id={m[1]} />;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
