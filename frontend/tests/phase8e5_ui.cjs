// Pass the directory containing tsc's CommonJS output as the first argument.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(process.argv[2]);
const drafts = require(path.join(root, "panelDraft.js"));
const panels = require(path.join(root, "panelVisualization.js"));
const apiContract = require(path.join(root, "api", "client.js"));
const css = fs.readFileSync(path.resolve(__dirname, "..", "src", "styles.css"), "utf8");
const panelSetupSource = fs.readFileSync(path.resolve(__dirname, "..", "src", "components", "PanelSetupDialog.tsx"), "utf8");
const appSource = fs.readFileSync(path.resolve(__dirname, "..", "src", "App.tsx"), "utf8");
const controlsSource = fs.readFileSync(path.resolve(__dirname, "..", "src", "components", "ControlsRow.tsx"), "utf8");
const engineeringActionsSource = fs.readFileSync(path.resolve(__dirname, "..", "src", "components", "EngineeringActionsDialog.tsx"), "utf8");
const apiSource = fs.readFileSync(path.resolve(__dirname, "..", "src", "api", "client.ts"), "utf8");

const original = [{
  panel: 7,
  panel_suffix: "P",
  start_station: 10,
  end_station: 110.5,
  start_elevation: 500,
  end_elevation: 545.25,
  panel_x: 1.25,
  panel_y: 2.5,
  h_scale: 500,
  v_scale: 5,
}];

let checks = 0;
const check = (fn) => { fn(); checks += 1; };

check(() => assert.equal(drafts.parseFinitePanelInput("", "Value").ok, false));
check(() => assert.equal(drafts.parseFinitePanelInput("-", "Value").ok, false));
check(() => assert.equal(drafts.parseFinitePanelInput(".", "Value").ok, false));
check(() => assert.deepEqual(drafts.parseFinitePanelInput("42", "Value"), { ok: true, value: 42 }));
check(() => assert.deepEqual(drafts.parseFinitePanelInput("42.75", "Value"), { ok: true, value: 42.75 }));
check(() => assert.equal(drafts.parseFinitePanelInput("NaN", "Value").ok, false));
check(() => assert.equal(drafts.parseFinitePanelInput("Infinity", "Value").ok, false));
check(() => assert.match(drafts.validatePanelGeometry({ ...original[0], end_station: 5 }), /Ending Station must be greater/));
check(() => assert.match(drafts.validatePanelGeometry({ ...original[0], end_elevation: 353 }), /Ending Elevation must be greater/));
check(() => assert.match(drafts.validatePanelGeometry({ ...original[0], start_station: Infinity }), /finite number/));
check(() => assert.equal(drafts.validatePanelGeometry(original[0]), null));

const inputs = drafts.createPanelNumericInputs(original);
check(() => {
  const replacement = { ...inputs, [drafts.panelNumericInputKey(0, "start_station")]: "25.75" };
  const result = drafts.materializePanelNumericInputs(original, replacement);
  assert.equal(result.ok, true);
  assert.equal(result.panels[0].start_station, 25.75);
});
check(() => {
  const empty = { ...inputs, [drafts.panelNumericInputKey(0, "end_station")]: "" };
  assert.equal(drafts.materializePanelNumericInputs(original, empty).ok, false);
});
check(() => {
  const invalid = {
    ...inputs,
    [drafts.panelNumericInputKey(0, "start_elevation")]: "3542",
    [drafts.panelNumericInputKey(0, "end_elevation")]: "353",
  };
  assert.match(drafts.materializePanelNumericInputs(original, invalid).error, /Ending Elevation must be greater/);
  assert.equal(original[0].start_elevation, 500, "invalid blur must leave preview geometry unchanged");
  assert.equal(original[0].end_elevation, 545.25, "invalid blur must leave preview geometry unchanged");
});
check(() => {
  const corrected = {
    ...inputs,
    [drafts.panelNumericInputKey(0, "start_elevation")]: "3542.25",
    [drafts.panelNumericInputKey(0, "end_elevation")]: "3600.5",
  };
  const result = drafts.materializePanelNumericInputs(original, corrected);
  assert.equal(result.ok, true);
  assert.equal(result.panels[0].start_elevation, 3542.25);
  assert.equal(result.panels[0].end_elevation, 3600.5);
});
check(() => assert.deepEqual(original, [{
  panel: 7, panel_suffix: "P", start_station: 10, end_station: 110.5,
  start_elevation: 500, end_elevation: 545.25, panel_x: 1.25, panel_y: 2.5,
  h_scale: 500, v_scale: 5,
}]));
check(() => {
  const result = drafts.materializePanelNumericInputs(original, inputs);
  assert.equal(result.ok, true);
  assert.deepEqual(result.panels[0], original[0]);
});
check(() => {
  const moved = [{ ...original[0], start_station: 20, end_station: 120.5 }];
  const synced = drafts.synchronizePanelNumericInputs(inputs, moved, [0]);
  assert.equal(synced[drafts.panelNumericInputKey(0, "start_station")], "20");
  assert.equal(synced[drafts.panelNumericInputKey(0, "end_station")], "120.5");
});
check(() => {
  const generated = [{ ...original[0], panel: 9, panel_suffix: "Q", h_scale: 250, v_scale: 2 }];
  const synced = drafts.synchronizePanelNumericInputs({}, generated);
  assert.equal(synced[drafts.panelNumericInputKey(0, "start_elevation")], "500");
  assert.equal(generated[0].panel, 9);
  assert.equal(generated[0].panel_suffix, "Q");
  assert.equal(generated[0].h_scale, 250);
  assert.equal(generated[0].v_scale, 2);
});
check(() => {
  assert.match(panelSetupSource, /function commitNumericInput[\s\S]*materializePanelNumericInputs\(draftPanels, numericInputs\)/);
  assert.match(panelSetupSource, /if \(!materialized\.ok\)[\s\S]*setError\(materialized\.error\)[\s\S]*return false/);
  assert.match(panelSetupSource, /setDraftPanels\(materialized\.panels\)/);
});
check(() => {
  const moved = panels.translatePanel(original[0], 25.5, 0);
  assert.equal(moved.start_station, 35.5);
  assert.equal(moved.end_station, 136);
  assert.equal(moved.end_station - moved.start_station, original[0].end_station - original[0].start_station);
  assert.equal(moved.start_elevation, original[0].start_elevation);
});
check(() => {
  const moved = panels.translatePanel(original[0], 0, -12.25);
  assert.equal(moved.start_elevation, 487.75);
  assert.equal(moved.end_elevation, 533);
  assert.equal(moved.end_elevation - moved.start_elevation, original[0].end_elevation - original[0].start_elevation);
  assert.equal(moved.start_station, original[0].start_station);
});
check(() => {
  const moved = panels.translatePanel(original[0], 10, 2);
  assert.equal(moved.panel, original[0].panel);
  assert.equal(moved.panel_suffix, original[0].panel_suffix);
  assert.equal(moved.panel_x, original[0].panel_x);
  assert.equal(moved.panel_y, original[0].panel_y);
  assert.equal(moved.h_scale, original[0].h_scale);
  assert.equal(moved.v_scale, original[0].v_scale);
});
check(() => {
  const moved = [panels.translatePanel(original[0], 10, 2)];
  const synced = drafts.synchronizePanelNumericInputs(inputs, moved, [0]);
  assert.equal(synced[drafts.panelNumericInputKey(0, "start_station")], "20");
  assert.equal(synced[drafts.panelNumericInputKey(0, "end_station")], "120.5");
  assert.equal(synced[drafts.panelNumericInputKey(0, "start_elevation")], "502");
  assert.equal(synced[drafts.panelNumericInputKey(0, "end_elevation")], "547.25");
});
check(() => {
  assert.match(panelSetupSource, /function materializeForInteraction[\s\S]*committedNumericDraft\(\)/);
  assert.match(panelSetupSource, /Correct the value before moving or resizing the panel/);
  assert.match(panelSetupSource, /function movePanel[\s\S]*materializeForInteraction\(\)[\s\S]*if \(!materialized\) return/);
  assert.match(panelSetupSource, /function handlePanelPointerDown[\s\S]*materializeForInteraction\(\)[\s\S]*if \(!materialized\) return/);
});
check(() => {
  const limits = { maxStationSpan: 6500, maxElevationSpan: 45.25 };
  const resized = panels.resizePanelEdge(original[0], "right", 130, limits);
  const synced = drafts.synchronizePanelNumericInputs(inputs, [resized], [0]);
  assert.equal(synced[drafts.panelNumericInputKey(0, "end_station")], "130");
  assert.equal(resized.h_scale, original[0].h_scale);
  assert.equal(resized.v_scale, original[0].v_scale);
});
check(() => assert.match(panelSetupSource, /kind:\s*"move"\s*\|\s*"resize"[\s\S]*viewDomain/));
check(() => assert.match(panelSetupSource, /dragState\?\.viewDomain\s*\?\?\s*fittedDomain/));
check(() => assert.match(panelSetupSource, /setDragState\(null\)[\s\S]*setPreviewMode\("selected"\)/));
check(() => { assert.match(panelSetupSource, /Full Profile Overview/); assert.match(panelSetupSource, /Selected Panel Preview/); });
check(() => assert.match(panelSetupSource, /aria-pressed=\{previewMode === "overview"\}[\s\S]*aria-pressed=\{previewMode === "selected"\}/));
check(() => assert.match(panelSetupSource, /profile\.profile_segments[\s\S]*\.map\(run => run\.map/));
check(() => assert.equal((panelSetupSource.match(/data-resize-edge=/g) || []).length, 4));
check(() => assert.match(panelSetupSource, /resizePanelEdge\(dragState\.original, edge, coordinate, resizeLimits\(dragState\.original\)\)/));
check(() => assert.match(css, /\.panel-resize-handle--horizontal\s*\{\s*cursor:\s*ew-resize;/));
check(() => assert.match(css, /\.panel-resize-handle--vertical\s*\{\s*cursor:\s*ns-resize;/));
check(() => assert.match(panelSetupSource, /overviewXMinRaw\s*=\s*Math\.min\(availableMinStation, bounds\.xMinRaw/));
check(() => assert.match(panelSetupSource, /overviewXMaxRaw\s*=\s*Math\.max\(availableMaxStation, bounds\.xMaxRaw/));
check(() => assert.match(panelSetupSource, /drawingClip\s*=\s*selectedPanel\s*&&\s*previewMode\s*===\s*"selected"/));
check(() => assert.match(panelSetupSource, /clipPath id="panel-setup-drawing-clip"[\s\S]*preview\.drawingClip/));
check(() => assert.ok((panelSetupSource.match(/clipPath="url\(#panel-setup-drawing-clip\)"/g) || []).length >= 6));
check(() => assert.match(panelSetupSource, /data-profile-segment="wsel"[\s\S]*preview\.invertPoints[\s\S]*preview\.crossSections/));
check(() => assert.match(panelSetupSource, /stationPerSvg:\s*\(preview\.viewDomain\.xMax\s*-\s*preview\.viewDomain\.xMin\)\s*\/\s*preview\.plot\.width/));
check(() => assert.match(panelSetupSource, /elevationPerSvg:\s*\(preview\.viewDomain\.yMax\s*-\s*preview\.viewDomain\.yMin\)\s*\/\s*preview\.plot\.height/));
check(() => assert.match(panelSetupSource, /viewDomain:\s*\{\s*\.\.\.preview\.viewDomain\s*\}/));
check(() => assert.match(panelSetupSource, /extends beyond the available model station range[\s\S]*not automatically clamped[\s\S]*complete Revision Limits coverage is still required/));
check(() => assert.doesNotMatch(panelSetupSource, /Math\.max\(availableMinStation,\s*panel\.start_station\)|Math\.min\(availableMaxStation,\s*panel\.end_station\)/));
check(() => assert.match(panelSetupSource, /await api\.putPanels\(sessionId, payload\)[\s\S]*setError\(String\(e\.message/));
check(() => { const before=JSON.stringify(original); const displayDomain={xMin:-100,xMax:120,yMin:490,yMax:550}; assert.equal(JSON.stringify(original),before); assert.equal(displayDomain.xMin,-100); });
check(() => assert.match(css, /\.panel-extent-warning\s*\{/));
check(() => assert.match(css, /\.panel-outside-model\s*\{/));

check(() => assert.match(css, /\.export-backdrop\s*\{[^}]*box-sizing:\s*border-box;[^}]*overflow:\s*hidden;/s));
check(() => assert.match(css, /\.export-dialog\s*\{[^}]*height:\s*min\(92vh,\s*calc\(100dvh\s*-\s*24px\)\);[^}]*min-height:\s*0;/s));
check(() => assert.match(css, /\.export-dialog \.export-body\s*\{[^}]*overflow-x:\s*hidden;[^}]*overflow-y:\s*scroll;[^}]*scrollbar-gutter:\s*stable;/s));
check(() => assert.match(css, /\.export-dialog \.export-header\s*\{[^}]*flex-shrink:\s*0;/s));
check(() => assert.match(css, /\.export-dialog \.export-footer\s*\{[^}]*flex-shrink:\s*0;/s));
check(() => assert.match(css, /@media \(max-height:\s*700px\)/));

check(() => {
  assert.match(controlsSource, />Auto Letters<\/button>/);
  assert.match(controlsSource, />Fix Drawdowns<\/button>/);
  assert.doesNotMatch(controlsSource, />Letters<\/button>|>Drawdowns<\/button>/);
});
check(() => {
  assert.match(appSource, /onFixDrawdowns=\{\(\) => setEngineeringAction\("fix-drawdowns"\)\}/);
  assert.match(appSource, /onAutoLetter=\{\(\) => setEngineeringAction\("auto-letters"\)\}/);
  assert.doesNotMatch(appSource, /async function onFixDrawdowns|async function onAutoLetter/);
});
check(() => {
  assert.match(engineeringActionsSource, /if \(!action\) return null;[\s\S]*async function apply\(\)/);
  assert.match(engineeringActionsSource, /onClose/);
  assert.doesNotMatch(engineeringActionsSource, /useEffect\([\s\S]{0,300}api\.(autoLetter|fixDrawdowns)/);
});
check(() => {
  assert.match(engineeringActionsSource, /Number\.isInteger\(parsedInterval\)/);
  assert.match(engineeringActionsSource, /parsedInterval <= 0/);
  assert.match(engineeringActionsSource, /Lettering interval must be a positive whole number/);
  for (const invalid of ["", "1.5", "0", "-2", "Infinity", "NaN"]) {
    const value = Number(invalid);
    assert.equal(Boolean(invalid.trim()) && Number.isInteger(value) && value > 0, false);
  }
  for (const valid of ["1", "5", "20"]) {
    const value = Number(valid);
    assert.equal(Boolean(valid.trim()) && Number.isInteger(value) && value > 0, true);
  }
});
check(() => {
  assert.match(engineeringActionsSource, /Cross sections evaluated/);
  assert.match(engineeringActionsSource, /Letters assigned/);
  assert.match(engineeringActionsSource, /Letters changed/);
  assert.match(engineeringActionsSource, /No letter changes were needed\./);
});
check(() => {
  assert.match(engineeringActionsSource, /Profiles evaluated/);
  assert.match(engineeringActionsSource, /WSEL adjustments made/);
  assert.match(engineeringActionsSource, /Affected profiles/);
  assert.match(engineeringActionsSource, /No qualifying drawdowns were found\. No WSEL values were changed\./);
  assert.match(engineeringActionsSource, /applies to all working profiles/);
});
check(() => {
  assert.match(engineeringActionsSource, /operation === requestId\.current/);
  assert.match(engineeringActionsSource, /if \(submitting \|\| intervalError\) return/);
  assert.match(engineeringActionsSource, /disabled=\{submitting/);
  assert.match(engineeringActionsSource, /The server rejected the operation/);
  assert.match(engineeringActionsSource, /The operation was not confirmed/);
});
check(() => {
  assert.match(engineeringActionsSource, /event\.key === "Escape" && !submitting/);
  assert.match(engineeringActionsSource, /role="dialog" aria-modal="true"/);
  assert.match(appSource, /setEngineeringAction\(null\);[\s\S]*setEngineeringInput\(null\);[\s\S]*setConfirmation\(null\);[\s\S]*\}, \[session\?\.session_id\]\)/);
  assert.match(appSource, /key=\{`engineering-action:\$\{session\.session_id\}`\}/);
});
check(() => {
  assert.match(apiSource, /export type AutoLetterResponse/);
  assert.match(apiSource, /export type FixDrawdownsResponse/);
  assert.match(apiSource, /req<unknown>[\s\S]*normalizeFixDrawdownsResponse/);
  assert.match(apiSource, /req<unknown>[\s\S]*normalizeAutoLetterResponse/);
  assert.match(appSource, /onSuccess=\{reloadAfterDb\}/);
});
check(() => {
  assert.match(css, /\.engineering-action-modal\s*\{/);
  assert.match(css, /\.engineering-action-body\s*\{[^}]*overflow-y:\s*auto;/s);
  assert.match(css, /\.engineering-action-footer\s*\{/);
});

check(() => {
  assert.deepEqual(apiContract.normalizeFixDrawdownsResponse({
    ok: true, profiles_evaluated: 2, cross_sections_evaluated: 14,
    wsel_adjustments_made: 3, affected_profiles: ["p1", "p2"], no_op: false,
  }), {
    ok: true, profiles_evaluated: 2, cross_sections_evaluated: 14,
    wsel_adjustments_made: 3, affected_profiles: ["p1", "p2"], no_op: false,
    details_complete: true,
  });
});
check(() => {
  assert.deepEqual(apiContract.normalizeFixDrawdownsResponse({ok: true}), {
    ok: true, profiles_evaluated: null, cross_sections_evaluated: null,
    wsel_adjustments_made: null, affected_profiles: null, no_op: null,
    details_complete: false,
  });
});
check(() => {
  for (const affected_profiles of [undefined, null, "p1", ["p1", 2], {}]) {
    const result = apiContract.normalizeFixDrawdownsResponse({ok: true, affected_profiles});
    assert.equal(result.affected_profiles, null);
    assert.equal(result.details_complete, false);
  }
});
check(() => {
  for (const invalid of [undefined, null, -1, 1.2, NaN, Infinity, "4"]) {
    const result = apiContract.normalizeFixDrawdownsResponse({
      ok: true, profiles_evaluated: invalid, cross_sections_evaluated: invalid,
      wsel_adjustments_made: invalid, affected_profiles: [], no_op: true,
    });
    assert.equal(result.profiles_evaluated, null);
    assert.equal(result.cross_sections_evaluated, null);
    assert.equal(result.wsel_adjustments_made, null);
  }
});
check(() => {
  assert.deepEqual(apiContract.normalizeAutoLetterResponse({ok: true}), {
    ok: true, cross_sections_evaluated: null, letters_assigned: null,
    letters_changed: null, no_op: null, details_complete: false,
  });
  assert.deepEqual(apiContract.normalizeAutoLetterResponse({
    ok: true, cross_sections_evaluated: 8, letters_assigned: 2, letters_changed: 1, no_op: false,
  }), {
    ok: true, cross_sections_evaluated: 8, letters_assigned: 2,
    letters_changed: 1, no_op: false, details_complete: true,
  });
});
check(() => {
  for (const invalid of [null, {}, {ok: false}, []]) {
    assert.throws(() => apiContract.normalizeFixDrawdownsResponse(invalid), /did not confirm the operation/);
  }
});
check(() => {
  assert.match(engineeringActionsSource, /setDrawdownResult\(result\)[\s\S]*await onSuccess\(\)/);
  assert.match(engineeringActionsSource, /setLetterResult\(result\)[\s\S]*await onSuccess\(\)/);
  assert.match(engineeringActionsSource, /operation was already completed[\s\S]*Do not run the operation again/);
  assert.match(engineeringActionsSource, /Reload current project state/);
  assert.match(engineeringActionsSource, /if \(submitting \|\| !completed\) return/);
});
check(() => {
  assert.match(engineeringActionsSource, /No automatic retry was attempted/);
  assert.match(engineeringActionsSource, /The server rejected the operation/);
  assert.match(engineeringActionsSource, /return \(\) => \{ requestId\.current \+= 1; \}/);
  assert.match(engineeringActionsSource, /affected_profiles == null[\s\S]*affected_profiles\.length/);
});

console.log(`PASS: ${checks} Phase 8E-5 UI contracts`);
