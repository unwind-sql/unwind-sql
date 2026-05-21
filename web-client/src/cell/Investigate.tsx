import { useEffect, useRef, useState } from "react";
import { streamInvestigate } from "../api";
import { formatScalar } from "../format";
import type { CellValue, Explanation, Finding } from "../types";

interface Props {
  model: string;
  column: string;
  where: Record<string, CellValue>;
}

type Phase = "idle" | "tracing" | "llm" | "cached" | "done" | "error";

export function Investigate({ model, column, where }: Props) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const ctrlRef = useRef<AbortController | null>(null);

  // Cancel any in-flight stream when the request changes (e.g. modal swap).
  useEffect(() => {
    return () => {
      ctrlRef.current?.abort();
    };
  }, []);

  async function run() {
    ctrlRef.current?.abort();
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    setPhase("tracing");
    setExplanation(null);
    setErrorMsg(null);
    try {
      for await (const evt of streamInvestigate(model, column, where, ctrl.signal)) {
        if (evt.event === "status") {
          setPhase(evt.data.phase);
        } else if (evt.event === "done") {
          setExplanation(evt.data);
          setPhase("done");
        } else if (evt.event === "error") {
          setErrorMsg(evt.data.error);
          setPhase("error");
        }
      }
    } catch (e: unknown) {
      if ((e as Error).name === "AbortError") return;
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  }

  const busy = phase === "tracing" || phase === "llm";
  const phaseLabel: Record<Phase, string> = {
    idle: "",
    tracing: "⏳ tracing values…",
    llm: "🤖 asking LLM…",
    cached: "⚡ from cache",
    done: "✓",
    error: `✗ ${errorMsg ?? "error"}`,
  };

  return (
    <div className="investigate">
      <button className="investigate-btn" onClick={run} disabled={busy}>
        🤖 Explain
      </button>
      <span className="investigate-status">{phaseLabel[phase]}</span>
      {(explanation || errorMsg) && (
        <div className="investigate-output">
          {explanation ? (
            <>
              <div className="investigate-summary">{explanation.summary}</div>
              <FindingList findings={explanation.findings} />
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}

function FindingList({ findings }: { findings: Finding[] }) {
  if (!findings.length) {
    return (
      <ul className="investigate-findings">
        <li className="empty">no notable findings</li>
      </ul>
    );
  }
  return (
    <ul className="investigate-findings">
      {findings.map((f, i) => (
        <li key={i} className="finding">
          <code>
            {f.model}.{f.column}
          </code>
          <span className="finding-value"> = {formatScalar(f.value)}</span>
          <div className="finding-reason">{f.reason}</div>
        </li>
      ))}
    </ul>
  );
}
