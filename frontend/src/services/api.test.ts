import { describe, expect, it } from "vitest";
import { parseSseFrames } from "./api";

describe("parseSseFrames", () => {
  it("emits tokens before a complete response and keeps a partial frame", () => {
    const value = [
      'event: token\ndata: {"type":"token","content":"Can"}',
      'event: token\ndata: {"type":"token","content":"berra"}',
      'event: complete\ndata: {"type":"complete","response":{"message":"Canberra"}}',
      'event: token\ndata: {"type":"token"',
    ].join("\n\n");
    const parsed = parseSseFrames(value);
    expect(parsed.events.map((event) => event.type)).toEqual(["token", "token", "complete"]);
    expect(parsed.events[0].content).toBe("Can");
    expect(parsed.remainder).toContain('event: token');
  });

  it("accepts CRLF frames and ignores comments", () => {
    const parsed = parseSseFrames(': keep-alive\r\nevent: route\r\ndata: {"type":"route","route":{"mode":"simple","agent":"simple","skills":[],"reason":"fast"}}\r\n\r\n');
    expect(parsed.events).toHaveLength(1);
    expect(parsed.events[0].route?.mode).toBe("simple");
  });
});
