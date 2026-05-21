import { afterEach, describe, expect, it, vi } from "vitest";
import { streamInvestigate } from "../src/api";
import type { InvestigateEvent } from "../src/types";

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i++]));
      } else {
        controller.close();
      }
    },
  });
}

function mockSseFetch(chunks: string[]) {
  const fetchMock = vi.fn(async () => {
    return new Response(makeStream(chunks), {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function collect(
  gen: AsyncGenerator<InvestigateEvent>,
): Promise<InvestigateEvent[]> {
  const out: InvestigateEvent[] = [];
  for await (const ev of gen) out.push(ev);
  return out;
}

describe("streamInvestigate", () => {
  it("parses status and done events split across chunks", async () => {
    mockSseFetch([
      'event: status\ndata: {"phase":"tracing"}\n\n',
      'event: status\ndata: {"phase":"llm"}\n\n',
      'event: done\ndata: {"summary":"hi","find',
      'ings":[]}\n\n',
    ]);
    const events = await collect(streamInvestigate("m", "c", {}));
    expect(events).toHaveLength(3);
    expect(events[0]).toEqual({ event: "status", data: { phase: "tracing" } });
    expect(events[1]).toEqual({ event: "status", data: { phase: "llm" } });
    expect(events[2]).toEqual({
      event: "done",
      data: { summary: "hi", findings: [] },
    });
  });

  it("yields error events", async () => {
    mockSseFetch(['event: error\ndata: {"error":"boom"}\n\n']);
    const events = await collect(streamInvestigate("m", "c", {}));
    expect(events).toEqual([{ event: "error", data: { error: "boom" } }]);
  });

  it("ignores blocks with empty data", async () => {
    mockSseFetch([
      'event: status\ndata: {"phase":"tracing"}\n\n',
      "\n\n",
      'event: done\ndata: {"summary":"ok","findings":[]}\n\n',
    ]);
    const events = await collect(streamInvestigate("m", "c", {}));
    expect(events.map((e) => e.event)).toEqual(["status", "done"]);
  });
});
