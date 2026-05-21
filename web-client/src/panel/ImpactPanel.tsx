import type { ColumnImpact, ImpactedColumn, ImpactEdge } from "../types";

interface Props {
  impact: ColumnImpact;
  onClose: () => void;
  onSelectModel: (name: string) => void;
}

const USAGE_LABEL: Record<ImpactEdge["usage"], string> = {
  projection: "projection",
  join: "JOIN key",
  filter: "WHERE / HAVING",
  group: "GROUP BY",
  order: "ORDER BY",
};

export function ImpactPanel({ impact, onClose, onSelectModel }: Props) {
  const { source } = impact;
  const affectedByModel = groupAffected(impact.affected);
  const structuralByModel = groupStructural(impact.edges);

  return (
    <div className="panel-body impact-panel">
      <div className="impact-header">
        <h2>
          Impact of <span className="impact-source">{source.model}.{source.column}</span>
        </h2>
        <button className="impact-close" onClick={onClose} title="Close impact view">
          ×
        </button>
      </div>
      <p className="meta">
        source type: <code>{source.type}</code> · {impact.affected.length}{" "}
        downstream column{plural(impact.affected.length)} affected ·{" "}
        {structuralByModel.size} structural usage{plural(structuralByModel.size)} ·{" "}
        {impact.opaque_consumers.length} opaque consumer{plural(impact.opaque_consumers.length)}
      </p>
      <p className="impact-hint">
        Use this view before renaming or retyping the source column. Red models
        rebuild values from it (rename → breaks code; type change → re-validate
        casts). Amber models reference it structurally (JOIN / WHERE / GROUP /
        ORDER); a rename will break those too. Purple Python sinks can't be
        introspected — open them by hand.
      </p>

      {affectedByModel.size > 0 && (
        <section className="impact-section">
          <h3 className="impact-section-title impact-section-title--projection">
            value flow ({affectedByModel.size} model{plural(affectedByModel.size)})
          </h3>
          {[...affectedByModel.entries()].map(([model, cols]) => (
            <details key={`p-${model}`} open className="impact-model">
              <summary
                onClick={(e) => {
                  e.preventDefault();
                  onSelectModel(model);
                }}
                title={`Show ${model} in the panel`}
              >
                <span className="impact-dot impact-dot--projection" />
                {model}
                <span className="impact-count">{cols.length}</span>
              </summary>
              <ul className="impact-cols">
                {cols.map((c) => (
                  <li key={c.column}>
                    <span className="impact-col-name">{c.column}</span>
                    <span className="ty">{c.type}</span>
                    {c.expression ? (
                      <code className="impact-expr">{c.expression}</code>
                    ) : null}
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </section>
      )}

      {structuralByModel.size > 0 && (
        <section className="impact-section">
          <h3 className="impact-section-title impact-section-title--structural">
            structural usages
          </h3>
          <ul className="impact-structural">
            {[...structuralByModel.entries()].map(([model, kinds]) => (
              <li key={`s-${model}`}>
                <button
                  className="impact-pivot"
                  onClick={() => onSelectModel(model)}
                  title={`Show ${model} in the panel`}
                >
                  <span className="impact-dot impact-dot--structural" />
                  {model}
                </button>
                <span className="impact-kinds">
                  {[...kinds].map((k) => USAGE_LABEL[k]).join(", ")}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {impact.opaque_consumers.length > 0 && (
        <section className="impact-section">
          <h3 className="impact-section-title impact-section-title--opaque">
            Python sinks (audit by hand)
          </h3>
          <ul className="impact-structural">
            {impact.opaque_consumers.map((model) => (
              <li key={`o-${model}`}>
                <button
                  className="impact-pivot"
                  onClick={() => onSelectModel(model)}
                  title={`Show ${model} in the panel`}
                >
                  <span className="impact-dot impact-dot--opaque" />
                  {model}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {affectedByModel.size === 0 &&
        structuralByModel.size === 0 &&
        impact.opaque_consumers.length === 0 && (
          <p className="empty">
            Nothing downstream uses this column — safe to rename or retype.
          </p>
        )}
    </div>
  );
}

function groupAffected(
  affected: ImpactedColumn[],
): Map<string, ImpactedColumn[]> {
  const out = new Map<string, ImpactedColumn[]>();
  for (const c of affected) {
    const arr = out.get(c.model) ?? [];
    arr.push(c);
    out.set(c.model, arr);
  }
  return out;
}

function groupStructural(
  edges: ImpactEdge[],
): Map<string, Set<ImpactEdge["usage"]>> {
  const out = new Map<string, Set<ImpactEdge["usage"]>>();
  for (const e of edges) {
    if (e.usage === "projection") continue;
    const set = out.get(e.child_model) ?? new Set();
    set.add(e.usage);
    out.set(e.child_model, set);
  }
  return out;
}

function plural(n: number): string {
  return n === 1 ? "" : "s";
}
