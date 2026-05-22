import { useEffect, useState } from "react";
import { fetchModelDoc } from "../api";
import type { ModelDoc } from "../types";

interface Props {
  modelName: string;
}

/**
 * "Doc" tab: structured documentation view for one model.
 *
 * The data comes from `/api/docs/{name}`, which carries every field the LLM
 * manifest exposes (description, columns with `inherited_from`, annotations,
 * upstream/downstream). The same payload is one entry of the full
 * `Documentation.to_json()` — the "Copy for LLM" button copies just that
 * entry, which is usually enough to ground an LLM question about one model.
 */
export function DocView({ modelName }: Props) {
  const [doc, setDoc] = useState<ModelDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setDoc(null);
    setError(null);
    setCopied(false);
    let cancelled = false;
    fetchModelDoc(modelName)
      .then((d) => {
        if (!cancelled) setDoc(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [modelName]);

  async function copyForLlm() {
    if (!doc) return;
    const payload = JSON.stringify(doc, null, 2);
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Clipboard access denied");
    }
  }

  if (error) return <p className="empty">{error}</p>;
  if (!doc) return <p className="empty">loading…</p>;

  return (
    <div className="doc-view">
      <div className="doc-actions">
        <button type="button" className="doc-copy" onClick={copyForLlm}>
          {copied ? "✓ Copied" : "📋 Copy for LLM"}
        </button>
      </div>

      {doc.description ? (
        <p className="doc-description">{doc.description}</p>
      ) : (
        <p className="empty">No description yet — add a `--` comment header in the SQL file.</p>
      )}

      <dl className="doc-meta">
        {doc.group ? (
          <>
            <dt>Group</dt>
            <dd>{doc.group}</dd>
          </>
        ) : null}
        <dt>Materialized</dt>
        <dd>{doc.materialized}</dd>
        <dt>Kind</dt>
        <dd>{doc.kind}</dd>
        {doc.tags.length > 0 ? (
          <>
            <dt>Tags</dt>
            <dd>
              {doc.tags.map((t) => (
                <span key={t} className="doc-tag">
                  {t}
                </span>
              ))}
            </dd>
          </>
        ) : null}
        {doc.upstreams.length > 0 ? (
          <>
            <dt>Upstreams</dt>
            <dd className="doc-refs">{doc.upstreams.join(", ")}</dd>
          </>
        ) : null}
        {doc.downstreams.length > 0 ? (
          <>
            <dt>Downstreams</dt>
            <dd className="doc-refs">{doc.downstreams.join(", ")}</dd>
          </>
        ) : null}
      </dl>

      {doc.columns.length > 0 ? (
        <section className="doc-section">
          <h3>Columns</h3>
          <table className="doc-cols">
            <thead>
              <tr>
                <th>Column</th>
                <th>Type</th>
                <th>Description</th>
                <th>Inherited from</th>
              </tr>
            </thead>
            <tbody>
              {doc.columns.map((c) => (
                <tr key={c.name}>
                  <td className="doc-col-name">{c.name}</td>
                  <td className="ty">{c.type ?? "—"}</td>
                  <td>{c.description ?? "—"}</td>
                  <td className="doc-inherited">{c.inherited_from ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {doc.annotations.length > 0 ? (
        <section className="doc-section">
          <h3>Annotations</h3>
          <ul className="doc-annotations">
            {doc.annotations.map((a, i) => (
              <li key={`${a.line}-${i}`}>
                <span className="doc-anno-line">L{a.line}</span>
                <span>{a.text}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
