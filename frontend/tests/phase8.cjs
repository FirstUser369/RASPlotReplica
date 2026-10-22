// Pass the directory containing tsc's CommonJS output as the first argument.
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(process.argv[2]);
const persistence = require(path.join(root, 'projectPersistence.js'));

let checks = 0;
const check = fn => { fn(); checks += 1; };

check(() => assert.equal(persistence.PROJECT_EXTENSION, '.rasplot.json'));
check(() => assert.equal(persistence.confirmProjectSwitch(false, () => false), true));
check(() => assert.equal(persistence.confirmProjectSwitch(true, () => true), true));
check(() => assert.equal(persistence.confirmProjectSwitch(true, () => false), false));
check(() => assert.match(persistence.projectStatusLabel(null, true), /Unsaved/));
check(() => assert.match(persistence.projectStatusLabel('C:\\Work\\A.rasplot.json', false), /Saved/));
check(() => assert.match(persistence.projectStatusLabel('C:\\Work\\A.rasplot.json', true), /Unsaved/));

console.log(`PASS: ${checks} Phase 8 project persistence contracts`);
