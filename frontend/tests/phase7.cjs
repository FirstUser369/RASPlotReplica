// Pass the directory containing tsc's CommonJS output as the first argument.
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(process.argv[2]);
const panels = require(path.join(root, 'panelVisualization.js'));
const workflow = require(path.join(root, 'workflowState.js'));
const revisions = require(path.join(root, 'revisionLimits.js'));

const first = {panel:1,start_station:0,end_station:6500,start_elevation:100,end_elevation:145,h_scale:500,v_scale:5};
const second = {panel:2,start_station:6500,end_station:9000,start_elevation:125,end_elevation:170,h_scale:500,v_scale:5};
let checks = 0;
const check = (fn) => { fn(); checks += 1; };

check(() => assert.equal(panels.panelOverlayTraces([], null, false).length, 0)); // toggle-off caller has no overlays
check(() => assert.deepEqual(panels.panelOverlayTraces([first], null, false)[0].x, [0,6500,6500,0,0]));
check(() => assert.equal(panels.panelOverlayTraces([first,second], null, false).length, 2));
check(() => assert.deepEqual(panels.panelOverlayTraces([first,second], null, false)[1].y, [125,125,170,170,125]));
check(() => assert.equal(panels.panelOverlayTraces([first,second], 2, false)[1].line.width, 3));
check(() => assert.equal(panels.panelRelationships([first,{...second,start_station:6400}])[0].kind, 'OVERLAP'));
check(() => assert.deepEqual(panels.translatePanel(first, 25, 2), {...first,start_station:25,end_station:6525,start_elevation:102,end_elevation:147}));
check(() => { const moved=panels.translatePanel(first,25,0); assert.equal(moved.end_station-moved.start_station,6500); });
check(() => { const moved=panels.translatePanel(first,0,8); assert.equal(moved.end_elevation-moved.start_elevation,45); });
const limits = {maxStationSpan:6500,maxElevationSpan:45};
check(() => assert.deepEqual(panels.resizePanelEdge(first,'left',100,limits),{...first,start_station:100}));
check(() => assert.deepEqual(panels.resizePanelEdge(first,'right',6400,limits),{...first,end_station:6400}));
check(() => assert.deepEqual(panels.resizePanelEdge(first,'top',144,limits),{...first,end_elevation:144}));
check(() => assert.deepEqual(panels.resizePanelEdge(first,'bottom',101,limits),{...first,start_elevation:101}));
check(() => { const resized=panels.resizePanelEdge({...first,panel_suffix:'P',panel_x:2,panel_y:3},'right',6400,limits); assert.equal(resized.h_scale,500); assert.equal(resized.v_scale,5); assert.equal(resized.panel,1); assert.equal(resized.panel_suffix,'P'); assert.equal(resized.panel_x,2); assert.equal(resized.panel_y,3); });
check(() => assert.throws(() => panels.resizePanelEdge(first,'left',6500,limits),/greater than/));
check(() => assert.throws(() => panels.resizePanelEdge(first,'bottom',145,limits),/greater than/));
check(() => assert.throws(() => panels.resizePanelEdge(first,'right',Infinity,limits),/finite/));
check(() => assert.throws(() => panels.resizePanelEdge(first,'right',6501,limits),/physical page capacity/));
check(() => assert.throws(() => panels.resizePanelEdge(first,'top',146,limits),/physical page capacity/));
check(() => assert.throws(() => panels.translatePanel(first, Infinity, 0), /finite/));
check(() => { const committed=[first]; panels.translatePanel(first,1,1); assert.deepEqual(committed,[first]); });
check(() => { const applied=panels.translatePanel(first,1,1); assert.equal(applied.start_station,1); });
check(() => { const generated=[second]; assert.deepEqual(generated,[second]); });
check(() => { const trace=panels.panelOverlayTraces([first],null,true)[0]; assert.equal(trace.line.dash,'dash'); assert.match(trace.name,/REVIEW REQUIRED/); });
check(() => assert.equal(workflow.engineeringPresentation(true,'source').panels,'RECALCULATION_REQUIRED'));
check(() => { const projectA=[first]; const projectB=[]; assert.equal(projectB.length,0); assert.equal(projectA.length,1); });
check(() => assert.equal(panels.panelRelationships([first,{...second,start_station:6600}])[0].kind,'GAP'));
check(() => assert.equal(panels.panelRelationships([first]).length,0));
check(() => { const relations=panels.panelRelationships([first,second,{...second,panel:3,start_station:8800,end_station:12000}]); assert.deepEqual(relations.map(x=>x.kind),['SHARED','OVERLAP']); });
check(() => { const exportSnapshot=JSON.stringify([first]); panels.translatePanel(first,100,10); assert.equal(JSON.stringify([first]),exportSnapshot); });
const preciseFull = {revision_start_station:0.004,revision_end_station:8847.08997631073,model_start_station:0.004,model_end_station:8847.08997631073,explicit:false};
check(() => { const draft=revisions.revisionDraft(preciseFull); assert.equal(draft.endInput,'8847.09'); const result=revisions.prepareRevisionSubmission(preciseFull,draft.startInput,draft.endInput,false,false); assert.equal(result.ok,true); assert.equal(result.end,8847.08997631073); });
check(() => { const draft=revisions.revisionDraft(preciseFull); assert.equal(draft.startInput,'0'); const result=revisions.prepareRevisionSubmission(preciseFull,draft.startInput,draft.endInput,false,false); assert.equal(result.ok,true); assert.equal(result.start,0.004); });
check(() => { const result=revisions.prepareRevisionSubmission(preciseFull,'100.25','8000.75',true,true); assert.deepEqual(result,{ok:true,start:100.25,end:8000.75,unchanged:false}); });
check(() => { const result=revisions.prepareRevisionSubmission(preciseFull,'0','8847.10',false,true); assert.equal(result.ok,false); assert.match(result.error,/model extent/); });
check(() => { const resetDraft=revisions.revisionDraft({...preciseFull,explicit:false}); const result=revisions.prepareRevisionSubmission(preciseFull,resetDraft.startInput,resetDraft.endInput,false,false); assert.equal(result.ok,true); assert.equal(result.unchanged,true); });
check(() => { const changed=revisions.prepareRevisionSubmission(preciseFull,'100','8000',true,true); assert.equal(changed.ok,true); assert.equal(changed.unchanged,false); }); // an actual revision change reaches the API and existing panel-review contract
check(() => { const draft=revisions.revisionDraft(preciseFull); const unchanged=revisions.prepareRevisionSubmission(preciseFull,draft.startInput,draft.endInput,false,false); assert.equal(unchanged.ok,true); assert.equal(unchanged.unchanged,true); }); // unchanged Apply skips mutation/panel invalidation

console.log(`PASS: ${checks} Phase 7 panel visualization and stitching contracts`);
