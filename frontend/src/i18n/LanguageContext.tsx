import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../services/api";
import { normalizeLanguage, setCurrentLanguage, translate, type Language, type TranslationValues } from "./messages";

const defaultValue = {
  language: "pt-BR" as Language,
  t: (message: string, values?: TranslationValues) => translate("pt-BR", message, values),
};
const LanguageContext = createContext(defaultValue);

export function LanguageProvider({ children, initialLanguage = "pt-BR", syncSettings = true }: {
  children: ReactNode;
  initialLanguage?: Language;
  syncSettings?: boolean;
}) {
  const [language, setLanguage] = useState<Language>(initialLanguage);
  const revision = useRef(0);
  useEffect(() => {
    let disposed = false;
    const apply = (settings: Record<string, any>) => {
      if (!disposed) setLanguage(normalizeLanguage(settings.user?.language));
    };
    const update = (event: Event) => {
      revision.current += 1;
      apply((event as CustomEvent<Record<string, any>>).detail);
    };
    window.addEventListener("jarvis-settings-updated", update);
    const requestedRevision = revision.current;
    if (syncSettings) void api.settings().then((settings) => {
      if (revision.current === requestedRevision) apply(settings);
    }).catch(() => undefined);
    return () => { disposed = true; window.removeEventListener("jarvis-settings-updated", update); };
  }, [syncSettings]);
  useEffect(() => {
    document.documentElement.lang = language;
    setCurrentLanguage(language);
    if ("__TAURI_INTERNALS__" in window) {
      void import("@tauri-apps/api/core").then(({ invoke }) =>
        invoke("set_interface_language", { language }),
      ).catch(() => undefined);
    }
  }, [language]);
  const value = useMemo(() => ({
    language,
    t: (message: string, values?: TranslationValues) => translate(language, message, values),
  }), [language]);
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage() { return useContext(LanguageContext); }
