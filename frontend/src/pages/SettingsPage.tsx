// The audit_config.json editor (credentials only). Other settings are edited in the file;
// all are written back on save, since PUT /api/config replaces the whole file.

import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { ConfigResponse, ConnectionState } from "../api/types";

interface Props {
  config: ConfigResponse;
  onSaved: () => void;
}

export function SettingsPage({ config, onSaved }: Props) {
  const [assumeRole, setAssumeRole] = useState(config.assume_role);
  const [roleName, setRoleName] = useState(config.role_name);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setAssumeRole(config.assume_role);
    setRoleName(config.role_name);
  }, [config]);

  async function save() {
    setError(null);
    setSaved(false);
    try {
      await api.putConfig({
        // Not edited here, but round-tripped: unsent is reset, not unchanged.
        connection_types:
          (config.raw.connection_types as Record<string, ConnectionState>) ?? {},
        authoritative_only: config.authoritative_only,
        graph_in_html: config.graph_in_html,
        graph_all_nodes: config.graph_all_nodes,
        bubbles_in_html: config.bubbles_in_html,
        bubbles_default_metric: config.bubbles_default_metric,
        snapshot_file: config.snapshot_file,
        assume_role: assumeRole,
        role_name: roleName,
      });
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <section className="page">
      <h2>Settings</h2>
      <p className="lede">
        Written to <code className="mono">{config.path}</code>. These are
        standing decisions about this account, not per-scan choices.
      </p>

      <div className="card">
        <h3>Credentials</h3>
        <label className="inline">
          <input type="checkbox" checked={assumeRole}
                 onChange={(e) => { setAssumeRole(e.target.checked); setSaved(false); }} />
          Assume an IAM role before scanning
          <span className="hint">
            Trades the profile's own credentials in for a dedicated,
            least-privilege role in the same account. See iam/README.md for a
            CloudFormation template granting exactly what a scan needs.
          </span>
        </label>
        <label>
          Role name
          <input
            type="text"
            value={roleName}
            disabled={!assumeRole}
            onChange={(e) => { setRoleName(e.target.value); setSaved(false); }}
          />
          <span className="hint">
            Overridable per-deployment with the AWS_AUDIT_ROLE_NAME env var.
          </span>
        </label>
      </div>

      {error && <div className="banner error">{error}</div>}
      {saved && <div className="banner ok">Saved to {config.path}</div>}

      <div className="form-row">
        <button className="primary" onClick={() => void save()}>
          Save settings
        </button>
        <span className="hint">
          Rejected as a whole if anything is invalid — the old file stays in
          place rather than being half-applied.
        </span>
      </div>
    </section>
  );
}
