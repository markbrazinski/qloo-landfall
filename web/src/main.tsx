import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./tokens.css";
import "./styles.css";
import Entry from "./Entry";
import BrandPage from "./Brand";

const match = window.location.pathname.match(/^\/p\/([\w-]+)/);

createRoot(document.getElementById("root")!).render(
  <StrictMode>{match ? <BrandPage id={match[1]} /> : <Entry />}</StrictMode>,
);
