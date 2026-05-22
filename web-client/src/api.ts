import type {
  CellValue,
  ColumnImpact,
  ColumnLineage,
  DagPayload,
  Documentation,
  InvestigateEvent,
  ModelData,
  ModelDetail,
  ModelDoc,
  TraceResult,
} from "./types";

async function jsonGet<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error ?? body.detail ?? `GET ${url} failed: ${r.status}`);
  }
  return (await r.json()) as T;
}

export function fetchDag(): Promise<DagPayload> {
  return jsonGet("/api/dag");
}

export function fetchModel(name: string): Promise<ModelDetail> {
  return jsonGet(`/api/model/${encodeURIComponent(name)}`);
}

export function fetchModelData(
  name: string,
  offset: number,
  limit = 100,
): Promise<ModelData> {
  const url = `/api/model/${encodeURIComponent(name)}/data?limit=${limit}&offset=${offset}`;
  return jsonGet(url);
}

export function fetchColumnLineage(
  model: string,
  column: string,
): Promise<ColumnLineage> {
  return jsonGet(
    `/api/column/${encodeURIComponent(model)}/${encodeURIComponent(column)}`,
  );
}

export function fetchDocs(): Promise<Documentation> {
  return jsonGet("/api/docs");
}

export function fetchModelDoc(name: string): Promise<ModelDoc> {
  return jsonGet(`/api/docs/${encodeURIComponent(name)}`);
}

/** Public URL for downloading the full docs in the requested format. */
export function docsExportUrl(format: "markdown" | "json"): string {
  return `/api/docs/export?format=${format}`;
}

export function fetchColumnImpact(
  model: string,
  column: string,
): Promise<ColumnImpact> {
  return jsonGet(
    `/api/column/${encodeURIComponent(model)}/${encodeURIComponent(column)}/impact`,
  );
}

export async function postCell(
  model: string,
  column: string,
  where: Record<string, CellValue>,
): Promise<TraceResult> {
  const r = await fetch("/api/cell", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, column, where }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error ?? body.detail ?? `POST /api/cell: ${r.status}`);
  }
  return (await r.json()) as TraceResult;
}

/**
 * SSE generator for /api/investigate. Each yielded value is one parsed event.
 * The caller is responsible for unsubscribing — pass an `AbortSignal` if you
 * need to cancel mid-stream (e.g. closing the modal during an LLM call).
 */
export async function* streamInvestigate(
  model: string,
  column: string,
  where: Record<string, CellValue>,
  signal?: AbortSignal,
): AsyncGenerator<InvestigateEvent> {
  const r = await fetch("/api/investigate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, column, where }),
    signal,
  });
  if (!r.ok || !r.body) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error ?? body.detail ?? `POST /api/investigate: ${r.status}`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const evt = parseSseBlock(block);
      if (evt) yield evt;
    }
  }
}

function parseSseBlock(block: string): InvestigateEvent | null {
  let event = "message";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7).trim();
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!data) return null;
  try {
    const parsed = JSON.parse(data) as unknown;
    return { event, data: parsed } as InvestigateEvent;
  } catch {
    return null;
  }
}
