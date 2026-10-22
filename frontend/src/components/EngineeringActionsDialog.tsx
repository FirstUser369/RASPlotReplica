import { useEffect, useRef, useState } from "react";
import { api, ApiError, type AutoLetterResponse, type FixDrawdownsResponse } from "../api/client";

export type EngineeringAction = "auto-letters" | "fix-drawdowns";

type Props = {
  action: EngineeringAction | null;
  sessionId: string;
  onClose: () => void;
  onSuccess: () => Promise<void>;
};

export default function EngineeringActionsDialog({ action, sessionId, onClose, onSuccess }: Props) {
  const [interval, setInterval] = useState("5");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [letterResult, setLetterResult] = useState<AutoLetterResponse | null>(null);
  const [drawdownResult, setDrawdownResult] = useState<FixDrawdownsResponse | null>(null);
  const requestId = useRef(0);

  useEffect(() => {
    requestId.current += 1;
    setInterval("5");
    setSubmitting(false);
    setError(null);
    setRefreshError(null);
    setLetterResult(null);
    setDrawdownResult(null);
    return () => { requestId.current += 1; };
  }, [action, sessionId]);

  useEffect(() => {
    if (!action) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [action, submitting, onClose]);

  if (!action) return null;

  const parsedInterval = Number(interval);
  const intervalError = action === "auto-letters" &&
    (!interval.trim() || !Number.isInteger(parsedInterval) || parsedInterval <= 0)
    ? "Lettering interval must be a positive whole number."
    : null;
  const completed = letterResult != null || drawdownResult != null;

  const countText = (value: number | null) => value == null ? "Not available" : String(value);

  async function refreshCurrentState() {
    if (submitting || !completed) return;
    const operation = ++requestId.current;
    setSubmitting(true);
    setRefreshError(null);
    try {
      await onSuccess();
      if (operation !== requestId.current) return;
    } catch (reason: any) {
      if (operation === requestId.current) {
        setRefreshError(`The operation was already completed, but the screen refresh failed: ${String(reason?.message ?? reason)} Do not run the operation again. Reload the current project state instead.`);
      }
    } finally {
      if (operation === requestId.current) setSubmitting(false);
    }
  }

  async function apply() {
    if (submitting || intervalError) return;
    const operation = ++requestId.current;
    setSubmitting(true);
    setError(null);
    setRefreshError(null);
    try {
      if (action === "auto-letters") {
        const result = await api.autoLetter(sessionId, parsedInterval);
        if (operation !== requestId.current) return;
        setLetterResult(result);
        try {
          await onSuccess();
        } catch (reason: any) {
          if (operation === requestId.current) {
            setRefreshError(`Auto Letters completed and may have changed working labels, but the screen refresh failed: ${String(reason?.message ?? reason)} Do not run Auto Letters again. Reload the current project state instead.`);
          }
        }
      } else {
        const result = await api.fixDrawdowns(sessionId);
        if (operation !== requestId.current) return;
        setDrawdownResult(result);
        try {
          await onSuccess();
        } catch (reason: any) {
          if (operation === requestId.current) {
            setRefreshError(`Fix Drawdowns completed and may have changed working WSEL values, but the screen refresh failed: ${String(reason?.message ?? reason)} Do not run Fix Drawdowns again. Reload the current project state instead.`);
          }
        }
      }
    } catch (reason: any) {
      if (operation === requestId.current) {
        const detail = String(reason?.message ?? reason);
        setError(reason instanceof ApiError && reason.status < 500
          ? `The server rejected the operation: ${detail}`
          : `The operation was not confirmed. ${detail} No automatic retry was attempted. Verify the current working values before trying again.`);
      }
    } finally {
      if (operation === requestId.current) setSubmitting(false);
    }
  }

  const title = action === "auto-letters" ? "Auto Letter Cross Sections" : "Fix WSEL Drawdowns";
  return (
    <div className="modal-backdrop engineering-action-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !submitting) onClose();
    }}>
      <section className="engineering-action-modal" role="dialog" aria-modal="true" aria-labelledby="engineering-action-title">
        <header className="engineering-action-header">
          <div><span className="eyebrow">Engineering action</span><h2 id="engineering-action-title">{title}</h2></div>
          <button type="button" className="close-button" onClick={onClose} disabled={submitting} aria-label={`Close ${title}`}>×</button>
        </header>
        <div className="engineering-action-body">
          {action === "auto-letters" ? (
            <>
              <p>Automatically assign letters to cross sections. Existing letters may be replaced.</p>
              <p className="engineering-action-warning">Manually edited letters may be overwritten. Label text is preserved.</p>
              <label className="engineering-action-field">
                <span>Lettering interval</span>
                <input type="number" min="1" step="1" inputMode="numeric" value={interval}
                  onChange={(event) => { setInterval(event.target.value); setError(null); }}
                  disabled={submitting || completed} aria-describedby="lettering-interval-help" />
                <small id="lettering-interval-help">Assign a letter at every specified number of cross sections.</small>
              </label>
              {intervalError && !completed && <div className="engineering-action-error" role="alert">{intervalError}</div>}
              {letterResult && <div className="engineering-action-result" role="status">
                <strong>{letterResult.no_op === true ? "No letter changes were needed."
                  : letterResult.no_op === false ? "Auto lettering completed."
                  : "Auto Letters completed. Detailed results are not available from this server."}</strong>
                <dl>
                  <div><dt>Cross sections evaluated</dt><dd>{countText(letterResult.cross_sections_evaluated)}</dd></div>
                  <div><dt>Letters assigned</dt><dd>{countText(letterResult.letters_assigned)}</dd></div>
                  <div><dt>Letters changed</dt><dd>{countText(letterResult.letters_changed)}</dd></div>
                </dl>
              </div>}
            </>
          ) : (
            <>
              <p>Check all working profiles for drawdowns and adjust qualifying WSEL values. Original source values remain preserved.</p>
              <p className="engineering-action-warning">This operation applies to all working profiles, not only the currently selected profile.</p>
              {drawdownResult && <div className="engineering-action-result" role="status">
                <strong>{drawdownResult.no_op === true
                  ? "No qualifying drawdowns were found. No WSEL values were changed."
                  : drawdownResult.no_op === false
                    ? "Drawdown adjustments completed."
                    : "Fix Drawdowns completed. Detailed results are not available from this server."}</strong>
                <dl>
                  <div><dt>Profiles evaluated</dt><dd>{countText(drawdownResult.profiles_evaluated)}</dd></div>
                  <div><dt>Cross sections evaluated</dt><dd>{countText(drawdownResult.cross_sections_evaluated)}</dd></div>
                  <div><dt>WSEL adjustments made</dt><dd>{countText(drawdownResult.wsel_adjustments_made)}</dd></div>
                  <div><dt>Affected profiles</dt><dd>{drawdownResult.affected_profiles == null
                    ? "Not available"
                    : drawdownResult.affected_profiles.length ? drawdownResult.affected_profiles.join(", ") : "None"}</dd></div>
                </dl>
              </div>}
            </>
          )}
          {error && <div className="engineering-action-error" role="alert">{error}</div>}
          {refreshError && <div className="engineering-action-error" role="alert">
            {refreshError}
            <div><button type="button" onClick={() => void refreshCurrentState()} disabled={submitting}>Reload current project state</button></div>
          </div>}
        </div>
        <footer className="engineering-action-footer">
          <button type="button" onClick={onClose} disabled={submitting}>{completed ? "Close" : "Cancel"}</button>
          {!completed && <button type="button" className="primary" onClick={() => void apply()}
            disabled={submitting || Boolean(intervalError)}>{submitting ? "Applying…" : "Apply"}</button>}
        </footer>
      </section>
    </div>
  );
}
