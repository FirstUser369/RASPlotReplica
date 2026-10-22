// Pass the directory containing tsc's CommonJS output as the first argument.
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(process.argv[2]);
const model = require(path.join(root, 'modelWorkspace.js'));
const identity = require(path.join(root, 'xsIdentity.js'));
const workflow = require(path.join(root, 'workflowState.js'));

const reach = (id, river, name, xs = 2) => ({id, river_id:`river-${river}`, river, reach:name, xs_count:xs, structure_count:0, station_extent:['1','9'], upstream_connection:null, downstream_connection:null, topology_status:'SUPPORTED'});
const inventory = {
  model_context:{model_id:'model-a', generation:1, session_id:'session-a'},
  model:{profiles:[{profile_id:'p1'}]},
  reaches:[reach('down','River','Down'), reach('up-a','River','Up A',3), reach('up-b','River','Up B',4)],
  junctions:[{id:'j1',name:'Split',upstream_reach_ids:['up-a','up-b'],downstream_reach_ids:['down']}],
  connections:[
    {id:'c1',junction_id:'j1',upstream_reach_id:'up-a',downstream_reach_id:'down',upstream_component:2,downstream_component:3,transition_distance:5,status:'SUPPORTED',provenance:{}},
    {id:'c2',junction_id:'j1',upstream_reach_id:'up-b',downstream_reach_id:'down',upstream_component:null,downstream_component:3,transition_distance:null,status:'MISSING',provenance:{}},
  ],
};

assert.deepEqual(model.overviewCounts(inventory), {rivers:1,reaches:3,crossSections:9,junctions:1,structures:0,profiles:1});
assert.equal(model.groupReaches(inventory.reaches).length, 1);
assert.equal(model.upstreamChoices(inventory, 'down', ['down']).length, 2);
assert.equal(model.localPathStatus(inventory, ['down','up-a']).ready, true);
assert.equal(model.localPathStatus(inventory, ['up-a','down']).ready, false);
assert.equal(model.localPathStatus(inventory, ['down','up-b']).connections[0].status, 'UNKNOWN');
assert.deepEqual(model.moveReach(['down','up-a'], 1, -1), ['up-a','down']);
assert.deepEqual(model.moveReach(['down'], 0, -1), ['down']);
assert.deepEqual(['down','up-a'].filter(id => id !== 'up-a'), ['down']);
assert.equal(model.connectionBetween(inventory, 'down', 'up-a')[0].transition_distance, 5);
const single = {...inventory, reaches:[reach('only','Solo','Only')], junctions:[], connections:[]};
assert.equal(model.localPathStatus(single, ['only']).ready, true);
assert.equal(model.overviewCounts(single).reaches, 1);

const link = (id, upstream, downstream) => ({id,junction_id:`j-${id}`,upstream_reach_id:upstream,downstream_reach_id:downstream,upstream_component:1,downstream_component:1,transition_distance:2,status:'SUPPORTED',provenance:{}});
const linear = {
  ...inventory,
  reaches:[reach('r4','South_Soln','Reach 4'),reach('r3a','South_Soln','Reach 3A'),reach('r3','South_Soln','Reach 3')],
  connections:[link('34','r3','r3a'),link('4a','r3a','r4')], junctions:[],
};
assert.deepEqual(model.topologyReachOrder(linear), ['r4','r3a','r3']);
const shuffled = {...linear,reaches:[linear.reaches[2],linear.reaches[0],linear.reaches[1]]};
assert.deepEqual(model.topologyReachOrder(shuffled), ['r4','r3a','r3']);
let incremental = [];
incremental = model.addReachInAssemblyOrder(shuffled, incremental, 'r3');
incremental = model.addReachInAssemblyOrder(shuffled, incremental, 'r4');
incremental = model.addReachInAssemblyOrder(shuffled, incremental, 'r3a');
assert.deepEqual(incremental, ['r4','r3a','r3']);
assert.equal(model.localPathStatus(linear, incremental).ready, true);

const splitRejoin = {
  ...inventory,
  reaches:['Reach 4','Reach 3.2','Reach 3C','Reach 3D','Reach 3.1','Reach 3A','Reach 3B','Reach 3'].map((name,index) => reach(`n${index}`, index < 4 ? 'North_Soln' : 'South_Soln', name)),
  connections:[], junctions:[],
};
const ids = Object.fromEntries(splitRejoin.reaches.map(r => [r.reach,r.id]));
splitRejoin.connections = [
  link('c40',ids['Reach 3.2'],ids['Reach 4']),
  link('cc',ids['Reach 3C'],ids['Reach 3.2']), link('cd',ids['Reach 3D'],ids['Reach 3.2']),
  link('ac',ids['Reach 3.1'],ids['Reach 3C']), link('ad',ids['Reach 3.1'],ids['Reach 3D']),
  link('aa',ids['Reach 3A'],ids['Reach 3.1']), link('ab',ids['Reach 3B'],ids['Reach 3.1']),
  link('3a',ids['Reach 3'],ids['Reach 3A']), link('3b',ids['Reach 3'],ids['Reach 3B']),
];
const branchComponents = model.topologyReachComponents(splitRejoin);
assert.equal(branchComponents.length, 1);
assert.equal(branchComponents[0].reaches.find(r => r.reach === 'Reach 3C').branch_ambiguous, true);
assert.equal(branchComponents[0].reaches.find(r => r.reach === 'Reach 3D').branch_ambiguous, true);
assert.equal(model.upstreamChoices(splitRejoin, ids['Reach 3.2'], [ids['Reach 4'],ids['Reach 3.2']]).length, 2);
assert.equal(model.addReachInAssemblyOrder(splitRejoin, [ids['Reach 4'],ids['Reach 3.2']], ids['Reach 3C']).includes(ids['Reach 3D']), false);

const disconnected = {...linear,reaches:[...linear.reaches,reach('island','Other','Disconnected') ]};
assert.equal(model.topologyReachComponents(disconnected).length, 2);
assert.equal(model.localPathStatus(disconnected,['r4','island']).ready,false);
assert.match(model.localPathStatus(disconnected,['r4','island']).messages[0],/not connected/);

const manuallyMoved = model.moveReach(['r4','r3a'],1,-1);
assert.deepEqual(manuallyMoved,['r3a','r4']);
assert.deepEqual(model.addReachInAssemblyOrder(linear,manuallyMoved,'r3',true).filter(id => id !== 'r3'),manuallyMoved);
assert.deepEqual(model.addReachInAssemblyOrder(linear,['r4','r3a'],'r3',true),['r4','r3a','r3']);
assert.equal(model.localPathStatus(linear,['r4','r4']).ready,false);
const duplicateStations = [
  {kind:'xs', id:11, rs:'100', source_identity:'model/river/reach-a/100'},
  {kind:'xs', id:12, rs:'100', source_identity:'model/river/reach-b/100'},
  {kind:'struct', id:13, rs:'100', row_identity:'structure/13'},
];
assert.deepEqual(identity.workingRowsByIdentity(duplicateStations), {
  'model/river/reach-a/100':11, 'model/river/reach-b/100':12,
});
assert.equal(identity.crossSectionByIdentity([
  {xs_id:'model/river/reach-a/100',rs:'100',reach:'A'},
  {xs_id:'model/river/reach-b/100',rs:'100',reach:'B'},
], 'model/river/reach-b/100').reach, 'B');
assert.throws(() => identity.workingRowsByIdentity([
  {kind:'xs',id:1,source_identity:'same'}, {kind:'xs',id:2,source_identity:'same'},
]));
const dirty = workflow.engineeringStateFromDb({stationing_dirty:true,panels_stale:true}, null);
assert.equal(dirty.notice, workflow.CUMDIST_DIRTY_MESSAGE);
assert.equal(dirty.stationingDirty, true);
assert.equal(dirty.panelsStale, true);
assert.equal(workflow.engineeringStateFromDb({stationing_dirty:false}, workflow.CUMDIST_DIRTY_MESSAGE).error, null);
assert.equal(workflow.engineeringStateFromDb({stationing_dirty:false}, 'Unrelated export error').error, 'Unrelated export error');
const tableProfile = {profile:'p1',profiles_selected:['p1'],cross_sections:[{xs_id:'xs-a',length_channel:10,cumdist:10,wsel:2,wsel_by_profile:{p1:2}}]};
const workingProfile = workflow.mergeWorkingRows(tableProfile,{working:[{kind:'xs',source_identity:'xs-a',length_chnl:30,cumdist:10,minchel:1,profiles:{p1:2}}]});
assert.equal(workingProfile.cross_sections[0].length_channel, 30);
assert.equal(workflow.profileTableVisible(true, workingProfile), true);
assert.equal(workflow.engineeringPresentation(true, 'Combined Source').chart, 'RECALCULATION_REQUIRED');
assert.equal(workflow.engineeringPresentation(true, 'Combined Source').panels, 'RECALCULATION_REQUIRED');
assert.equal(workflow.engineeringPresentation(false, 'Combined Source').allowOriginalReachSelection, false);
assert.equal(workflow.activeReachContext('Combined Source', {reaches:[{river:'Wrong',reach:'Reach'}]}, 0).label, 'Combined Source');
assert.equal(workflow.engineeringPresentation(false, null).allowOriginalReachSelection, true);
assert.equal(workflow.activeReachContext(null, {reaches:[{river:'River',reach:'Reach'}]}, 0).label, 'River / Reach');
assert.equal(workflow.profileTableVisible(false, workingProfile), false);
assert.equal(workflow.profileTableVisible(true, null), false);
console.log('PASS: 30 Phase 6 workspace, identity, and workflow-state contracts');
