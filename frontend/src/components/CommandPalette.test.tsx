import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CommandPalette } from "./CommandPalette";

describe("CommandPalette", () => {
  it("filters commands and navigates with one action", async () => {
    const user = userEvent.setup();
    const close = vi.fn();
    const navigate = vi.fn();
    render(<CommandPalette close={close} navigate={navigate} />);

    const input = screen.getByPlaceholderText(/Pesquisar páginas/i);
    expect(input).toHaveFocus();
    await user.type(input, "telemetria");
    expect(screen.getByRole("button", { name: /Painel/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: /Agentes/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Painel/ }));
    expect(navigate).toHaveBeenCalledWith("dashboard");
    expect(close).toHaveBeenCalledOnce();
  });

  it("closes on Escape", async () => {
    const close = vi.fn();
    render(<CommandPalette close={close} navigate={vi.fn()} />);
    await userEvent.keyboard("{Escape}");
    expect(close).toHaveBeenCalledOnce();
  });
});
