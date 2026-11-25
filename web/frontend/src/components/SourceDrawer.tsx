import { useState } from "react";
import { getSource } from "../api";
import type { SourceStatus } from "../types";

interface Props {
  sessionKey: string;
  sources: SourceStatus[];
}

export default function SourceDrawer({ sessionKey, sources }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const [body, setBody] = useState<string>("");
  const [error, setError] = useState<string>("");

  async function show(key: string) {
    if (open === key) {
      setOpen(null);
      return;
    }
    setOpen(key);
    setBody("");
    setError("");
    try {
      setBody(await getSource(sessionKey, key));
    } catch (exc) {
      setError(String(exc));
    }
  }

  return (
    <div className="card">
      <h3>Sources</h3>
      <dl className="fact">
        {sources.map((source) => (
          <div key={source.key} style={{ display: "contents" }}>
            <dt>
              <button className="link" onClick={() => void show(source.key)}>
                {source.label}
              </button>
            </dt>
            <dd className={source.state === "ok" ? "" : "stale"}>
              {source.state}
              {source.detail ? ` — ${source.detail}` : ""}
              <span className="provenance">
                {" "}
                · observed {source.observed_at ?? "never"}
                {source.source_at ? ` · source stamp ${source.source_at}` : ""}
                {source.read_limit_hit ? " · read limit hit" : ""}
              </span>
            </dd>
          </div>
        ))}
      </dl>
      {open && error && <p className="bad">{error}</p>}
      {/* React renders a string child as a TEXT NODE. Markdown and YAML from .dev/ are shown
          escaped by construction — there is no dangerouslySetInnerHTML anywhere in this app. */}
      {open && !error && <pre>{body}</pre>}
    </div>
  );
}