import { formatScalar } from "../format";
import { highlightSql } from "../sql";
import type { CellValue, TraceNode } from "../types";

interface Props {
  node: TraceNode;
}

export function TraceTree({ node }: Props) {
  const valueDisplay = renderValueList(node.values, node.value_count);
  const head = (
    <>
      <div className="trace-head">
        <strong>
          {node.model}.{node.column}
        </strong>
        <span className="trace-eq">=</span>
        <span className="trace-value">{valueDisplay}</span>
      </div>
      <div className="trace-formula">
        <span className="trace-label">formula</span>
        <code
          className="hljs language-sql"
          dangerouslySetInnerHTML={{ __html: highlightSql(node.expression) }}
        />
      </div>
      <div className="trace-formula">
        <span className="trace-label">substituted</span>
        <code
          className="hljs language-sql"
          dangerouslySetInnerHTML={{ __html: highlightSql(node.substituted) }}
        />
      </div>
    </>
  );

  if (!node.upstream || node.upstream.length === 0) {
    return <div className="trace-node">{head}</div>;
  }
  return (
    <details open className="trace-node">
      <summary>{head}</summary>
      {node.upstream.map((child, i) => (
        <TraceTree key={i} node={child} />
      ))}
    </details>
  );
}

function renderValueList(values: CellValue[], totalCount: number) {
  if (!values || values.length === 0) return <em>(no value)</em>;
  const display = values.map(formatScalar).join(", ");
  if (totalCount > values.length) {
    return (
      <>
        {display}{" "}
        <em className="trace-truncated">
          (showing {values.length} of {totalCount.toLocaleString()})
        </em>
      </>
    );
  }
  return display;
}
