// The Scan button, its options and progress. Scan state lives on the server and is
// polled, so a reload rejoins a running scan; Stop takes effect before the next AWS step.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { DefaultRegions, ScanStatus } from "../api/types";

const POLL_MS = 1500;

interface Props {
  /** Called when a scan stops running, successfully or not. */
  onFinished: () => void;
  scannedAt: string | null;
  account: string;
  rowCount: number;
}

export function ScanBar({ onFinished, scannedAt, account, rowCount }: Props) {
  const [status, setStatus] = useState<ScanStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [starting, setStarting] = useState(false);

  const [regions, setRegions] = useState("");
  // The saved list for the last scan's account. It fills the box until the
  // user types in it; after that the box is theirs.
  const [defaults, setDefaults] = useState<DefaultRegions | null>(null);
  const regionsEdited = useRef(false);
  const [allRegions, setAllRegions] = useState(false);
  const [profile, setProfile] = useState("");
  const [profiles, setProfiles] = useState<string[]>([]);
  const [noCost, setNoCost] = useState(false);
  const [debug, setDebug] = useState(false);

  // Held in a ref so the polling effect can compare against the previous state
  // without listing it as a dependency and restarting the interval every tick.
  const wasRunning = useRef(false);
  const running = status?.state === "running";

  const loadDefaults = useCallback(() => {
    void api
      .getDefaultRegions()
      .then((d) => {
        setDefaults(d);
        if (!regionsEdited.current && d.regions.length) setRegions(d.regions.join(" "));
      })
      .catch(() => setDefaults(null));
  }, []);

  useEffect(() => loadDefaults(), [loadDefaults]);

  const poll = useCallback(async () => {
    try {
      const next = await api.getScanStatus();
      setStatus(next);
      if (wasRunning.current && next.state !== "running") {
        onFinished();
        // The scan that just ended may have changed the saved list.
        loadDefaults();
      }
      wasRunning.current = next.state === "running";
    } catch {
      // A failed poll is ignored: the next one is 1.5s away.
    }
  }, [onFinished, loadDefaults]);

  // On mount, once: adopt whatever the server says is happening. This is what
  // makes a reload mid-scan rejoin rather than show an idle button.
  useEffect(() => {
    void poll();
  }, [poll]);

  // Suggestions only, fetched once: the box stays free text, so a profile
  // this list misses is still typeable, and a failed fetch costs nothing.
  useEffect(() => {
    void api
      .getProfiles()
      .then((r) => setProfiles(r.profiles))
      .catch(() => setProfiles([]));
  }, []);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, poll]);

  async function startScan() {
    setError(null);
    setStarting(true);
    try {
      const next = await api.startScan({
        regions: allRegions || !regions.trim()
          ? null
          : regions.split(/[,\s]+/).filter(Boolean),
        all_regions: allRegions,
        profile: profile.trim() || null,
        no_cost: noCost,
        debug,
      });
      setStatus(next);
      wasRunning.current = true;
      setShowLog(true);
    } catch (e) {
      const message = e instanceof ApiError ? e.message : String(e);
      setError(message);
      // 409: a scan is already running elsewhere, so adopt its state.
      if (e instanceof ApiError && e.isScanRunning) void poll();
    } finally {
      setStarting(false);
    }
  }

  async function stopScan() {
    setError(null);
    try {
      setStatus(await api.cancelScan());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  const stopping = running && !!status?.stopping;

  const pct =
    status && status.steps_total > 0
      ? Math.round((status.steps_done / status.steps_total) * 100)
      : 0;

  // A failure message's first line goes on the progress row; the rest keeps its line
  // breaks below it (e.g. where to put credentials).
  const [failHeadline, ...failDetail] = (status?.error ?? "").split("\n");
  const failBody = failDetail.join("\n").trim();

  return (
    <section className="scanbar">
      <div className="scanbar-top">
        <button
          className="primary"
          onClick={() => void startScan()}
          disabled={running || starting}
          title={running ? "A scan is already running" : "Scan AWS now"}
        >
          {running ? "Scanning…" : starting ? "Starting…" : "Scan"}
        </button>

        {running && (
          <button
            className="secondary"
            onClick={() => void stopScan()}
            disabled={stopping}
            title="Stops before the next AWS call. Nothing is saved; the previous results stay."
          >
            {stopping ? "Stopping…" : "Stop scan"}
          </button>
        )}

        <div className="scan-meta">
          {scannedAt ? (
            <>
              <strong>{rowCount}</strong> resources
              {account && <> · account {account}</>} · scanned{" "}
              {new Date(scannedAt).toLocaleString()}
            </>
          ) : (
            <>No scan yet — press Scan to make one.</>
          )}
        </div>
      </div>

      <details className="scan-options">
        <summary>Scan options</summary>
        <div className="scan-options-body">
          <label className="check">
            <input
              type="checkbox"
              checked={allRegions}
              onChange={(e) => setAllRegions(e.target.checked)}
            />
            <span className="check-text">
              All regions
              <span className="hint">
                Every enabled region. Slow — 27 collectors per region.
              </span>
            </span>
          </label>
          <label>
            Regions
            <input
              type="text"
              placeholder="us-east-1 eu-west-1"
              value={regions}
              disabled={allRegions}
              onChange={(e) => {
                regionsEdited.current = true;
                setRegions(e.target.value);
              }}
            />
            <span className="hint">
              {defaults?.regions.length ? (
                <>
                  Filled in with the regions where account {defaults.account} has
                  resources, saved by the scan of {defaults.updated_at.slice(0, 10)}.
                  Blank scans the saved list for whichever account the profile
                  logs in to, or us-east-1 if it has none.
                </>
              ) : (
                <>
                  Blank scans us-east-1: this Docker setup gives the profile no
                  default region. After a scan, the regions it found resources
                  in are filled in here.
                </>
              )}
            </span>
          </label>
          <label>
            Profile
            <input
              type="text"
              placeholder="default"
              list="scan-profiles"
              value={profile}
              onChange={(e) => setProfile(e.target.value)}
            />
            <datalist id="scan-profiles">
              {profiles.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
            <span className="hint">
              An AWS named profile. Blank uses <code>[default]</code>.
              {profiles.length > 0
                ? ` Defined: ${profiles.join(", ")}.`
                : " No profiles found in the credentials file."}
            </span>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={noCost}
              onChange={(e) => setNoCost(e.target.checked)}
            />
            <span className="check-text">
              Skip cost lookup
              <span className="hint">
                Cost runs by default. This opts out of the chargeable
                ce:GetCostAndUsage call (~$0.01).
              </span>
            </span>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={debug}
              onChange={(e) => setDebug(e.target.checked)}
            />
            <span className="check-text">
              Debug output
              <span className="hint">
                Connection-type table and Lambda env-var dump, into the log below.
              </span>
            </span>
          </label>
        </div>
      </details>

      {status && status.state !== "idle" && (
        <div className="scan-progress">
          <div className="progress-track">
            <div
              className={`progress-fill ${status.state}`}
              style={{ width: `${running ? pct : 100}%` }}
            />
          </div>
          <div className="progress-label">
            {running ? (
              <>
                {stopping && <>Stopping after: </>}
                {status.phase === "collect" ? (
                  <>
                    {status.region} · {status.collector}
                  </>
                ) : (
                  <>{status.stage ?? status.phase}</>
                )}
                {status.steps_total > 0 && (
                  <>
                    {" "}
                    — {status.steps_done}/{status.steps_total} ({pct}%)
                  </>
                )}
              </>
            ) : status.state === "failed" ? (
              <span className="failed">Scan failed: {failHeadline}</span>
            ) : status.state === "cancelled" ? (
              <span>Scan stopped — nothing was saved, the previous results are unchanged.</span>
            ) : (
              <span className="done">
                Scan complete
                {status.result?.rows != null && <> — {status.result.rows} resources</>}
                {status.result?.stale != null && <>, {status.result.stale} stale</>}
                {/* Failed lookups are not rows, so without
                    this an all-denied scan reads as an empty account. */}
                {!!status.result?.errors && (
                  <> · {status.result.errors} lookup(s) failed — see the log</>
                )}
              </span>
            )}
            <button className="linkish" onClick={() => setShowLog((s) => !s)}>
              {showLog ? "hide log" : "show log"}
            </button>
            {/* The log prints facts only; what to do about them is here. */}
            <a className="linkish" href="/scan-messages.html" target="_blank" rel="noopener">
              what these messages mean
            </a>
          </div>
          {status.state === "failed" && failBody && (
            <pre className="scan-error">{failBody}</pre>
          )}
          {showLog && (
            <pre className="scan-log">
              {status.log.length ? status.log.join("\n") : "(no output yet)"}
            </pre>
          )}
        </div>
      )}

      {error && <div className="banner error">{error}</div>}
    </section>
  );
}
