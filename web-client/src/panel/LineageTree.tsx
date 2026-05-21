import type { ColumnLineage } from "../types";
import { highlightSql } from "../sql";

interface Props {
  node: ColumnLineage;
}

export function LineageTree({ node }: Props) {
  const expr = node.expression ? (
    <code
      className="hljs language-sql inline-code"
      dangerouslySetInnerHTML={{ __html: highlightSql(node.expression) }}
    />
  ) : null;
  const head = (
    <span>
      <strong>{node.name}</strong>
      {expr ? <> {expr}</> : null}
    </span>
  );
  if (!node.upstream || node.upstream.length === 0) {
    return <div>{head}</div>;
  }
  return (
    <details open>
      <summary>{head}</summary>
      {node.upstream.map((child, i) => (
        <LineageTree key={i} node={child} />
      ))}
    </details>
  );
}
