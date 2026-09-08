import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "../types";

const actions = vi.hoisted(() => ({ confirm: vi.fn(), send: vi.fn() }));
vi.mock("../store/JarvisContext", () => ({ useJarvis: () => actions }));

import { MessageBubble } from "./MessageBubble";

describe("MessageBubble", () => {
  it("renders a human confirmation preview and authorizes explicitly", async () => {
    const message: ChatMessage = {
      id: "m1",
      role: "assistant",
      content: "Preciso da sua autorização, senhor.",
      confirmationId: "confirm-1",
      confirmationPreview: {
        title: "Enviar mensagem no WhatsApp",
        summary: "A mensagem será enviada somente após confirmação.",
        details: [
          { label: "Contato", value: "Ana" },
          { label: "Mensagem", value: "Chego às oito." },
        ],
      },
    };
    render(<MessageBubble message={message} />);
    expect(screen.getByText("Ana")).toBeVisible();
    expect(screen.getByText("Chego às oito.")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /Autorizar/ }));
    expect(actions.confirm).toHaveBeenCalledWith(message, true);
  });

  it("renders markdown code while keeping operational JSON out of the activity row", () => {
    const { container } = render(<MessageBubble message={{
      id: "m2", role: "assistant", content: "```ts\nconst answer = 42;\n```",
      actions: [{ tool: "open_app", status: "completed", detail: "Discord", label: "Abrindo Discord" }],
    }} />);
    expect(container.querySelector("code")?.textContent).toContain("const answer = 42;");
    expect(screen.getByText("Abrindo Discord")).toBeVisible();
    expect(screen.queryByText(/\{"tool"/)).not.toBeInTheDocument();
  });
});
