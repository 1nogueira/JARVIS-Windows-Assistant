import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  settings: vi.fn(), onboarding: vi.fn(), saveSettings: vi.fn(), speakLocal: vi.fn(),
}));
vi.mock("../services/api", () => ({ api: mocks }));

import { SettingsPage } from "./SettingsPage";

const config = {
  ollama: { url: "http://127.0.0.1:11434", chat_model: "qwen", vision_model: "vision", temperature: 0.2, context_size: 4096 },
  voice: { enabled: true, speech_rate: 1, wake_word: "jarvis", sensitivity: 0.5, wake_word_enabled: true, hands_free: true },
  user: { default_location: "São Paulo" },
  interface: { always_on_top: false, minimize_to_tray: true, start_with_windows: false, animations: true },
  proactive: { enabled: false }, storage: { artifact_directory: "D:\\JARVIS" },
  privacy: { memory_enabled: true, history_enabled: true, structured_logs: true, keep_screenshots: false },
};

describe("SettingsPage", () => {
  it("loads local diagnostics, changes runtime settings and saves", async () => {
    mocks.settings.mockResolvedValue(structuredClone(config));
    mocks.onboarding.mockResolvedValue({ backend: { ok: true, detail: "Pronto" } });
    mocks.saveSettings.mockImplementation(async (value) => value);
    render(<SettingsPage />);
    const url = await screen.findByLabelText("URL do Ollama");
    expect(url).toHaveValue("http://127.0.0.1:11434");
    await userEvent.clear(url);
    await userEvent.type(url, "http://127.0.0.1:22434");
    await userEvent.click(screen.getByRole("button", { name: "Salvar" }));
    await waitFor(() => expect(mocks.saveSettings).toHaveBeenCalled());
    expect(mocks.saveSettings.mock.calls[0][0].ollama.url).toBe("http://127.0.0.1:22434");
  });
});
