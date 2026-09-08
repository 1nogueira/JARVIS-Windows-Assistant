import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const jarvis = vi.hoisted(() => ({
  stop: vi.fn(),
  toggleRecording: vi.fn(),
  status: {
    ollama: { online: true, models: [{ name: "qwen-test" }] },
    voice: { wakeword: { running: true } },
  },
  state: "IDLE",
  messages: [],
  audioLevel: 0,
  recording: false,
  handsFree: false,
  microphoneMuted: false,
}));

vi.mock("../store/JarvisContext", () => ({ useJarvis: () => jarvis }));

import { Shell } from "./Shell";

describe("Shell navigation", () => {
  beforeEach(() => vi.clearAllMocks());

  it("exposes every product area and navigates without reloading", async () => {
    const setPage = vi.fn();
    render(<Shell page="chat" setPage={setPage} openPalette={vi.fn()}><div>Content</div></Shell>);
    expect(screen.getByRole("button", { name: "Agentes" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Fontes de dados" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Registros e rastreamento" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Configurações" }));
    expect(setPage).toHaveBeenCalledWith("settings");
  });

  it("collapses the responsive sidebar while keeping navigation available", async () => {
    const { container } = render(<Shell page="chat" setPage={vi.fn()} openPalette={vi.fn()}><div /></Shell>);
    await userEvent.click(screen.getByTitle("Recolher ou expandir barra lateral"));
    expect(container.querySelector(".sidebar")).toHaveClass("collapsed");
    expect(screen.getByRole("button", { name: "Painel" })).toBeVisible();
  });
});
