import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { JarvisProvider } from "./store/JarvisContext";
import { LanguageProvider } from "./i18n/LanguageContext";
import "@fontsource-variable/geist";
import "katex/dist/katex.min.css";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><LanguageProvider><JarvisProvider><App /></JarvisProvider></LanguageProvider></React.StrictMode>,
);
