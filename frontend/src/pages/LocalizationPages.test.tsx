import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  agents: vi.fn(), skills: vi.fn(), tools: vi.fn(), dataSources: vi.fn(), traces: vi.fn(), logs: vi.fn(),
}));
vi.mock("../services/api", () => ({ api: mocks }));

import { LanguageProvider } from "../i18n/LanguageContext";
import { AgentsPage } from "./AgentsPage";
import { DataSourcesPage } from "./DataSourcesPage";
import { ToolsPage } from "./ToolsPage";
import { LogsPage } from "./LogsPage";

beforeEach(() => { for (const mock of Object.values(mocks)) mock.mockResolvedValue([]); });

describe("localized pages", () => {
  it.each([
    [AgentsPage, "Agentes", "Agents"],
    [DataSourcesPage, "Fontes de dados", "Data Sources"],
    [ToolsPage, "Ferramentas e habilidades", "Tools & Skills"],
    [LogsPage, "Registros e rastreamento", "Logs & Traces"],
  ])("switches a mounted page when the saved language changes", async (Page, portuguese, english) => {
    render(<LanguageProvider syncSettings={false}><Page /></LanguageProvider>);
    expect(await screen.findByRole("heading", { name: portuguese })).toBeInTheDocument();
    await act(async () => { window.dispatchEvent(new CustomEvent("jarvis-settings-updated", { detail: { user: { language: "en-US" } } })); });
    expect(await screen.findByRole("heading", { name: english })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("en-US");
    await act(async () => { window.dispatchEvent(new CustomEvent("jarvis-settings-updated", { detail: { user: { language: "pt-BR" } } })); });
    expect(await screen.findByRole("heading", { name: portuguese })).toBeInTheDocument();
  });
});
