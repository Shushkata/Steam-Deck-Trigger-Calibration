import test from 'node:test';
import assert from 'node:assert/strict';
import { markerPosition, cautionMessage, difference, validProposal, triggerControls } from '../.test-build/calibration.js';

const current = { lmax:1814, lmin:518, rmax:1730, rmin:506, lneg:12, rneg:13 };

test('Default marker stays fixed as the selected value moves', () => {
  const markers = [1814, 1820, 1834].map(() => markerPosition(1814,1794,1854));
  assert.ok(markers.every(value => Math.abs(value-100/3) < 1e-9));
  assert.equal(new Set(markers).size,1);
});
test('Caution position and visible endpoints', () => {
  assert.equal(markerPosition(1820,1800,1840),50);
  assert.equal(markerPosition(1800,1800,1880),0);
  assert.equal(markerPosition(1880,1800,1880),100);
});
test('Out of viewport and degenerate ranges have no fake markers', () => {
  assert.equal(markerPosition(1820,1700,1800),null);
  assert.equal(markerPosition(1820,1820,1820),null);
});
test('A default above 1820 is not labelled dangerous', () => {
  assert.equal(cautionMessage(1900,1900),'');
  assert.equal(cautionMessage(1880,1900),'');
  assert.equal(cautionMessage(1920,1900),'Caution: Test trigger response carefully.');
});
test('Caution requires strictly above the reference', () => {
  assert.equal(cautionMessage(1820,1814),'');
  assert.notEqual(cautionMessage(1821,1814),'');
});
test('Independent LT/RT and numerical differences', () => {
  assert.equal(validProposal(current,1834,1730),true);
  assert.equal(validProposal(current,1814,1710),true);
  assert.equal(difference(1834,1814),'+20');
  assert.equal(difference(1710,1730),'-20');
  assert.equal(difference(1814,1814),'0');
});
test('Reject invalid wire values', () => {
  for (const [l,r] of [[65536,1730],[518,1730],[1814.5,1730],[NaN,1730],[Infinity,1730]])
    assert.equal(validProposal(current,l,r),false);
  assert.equal(validProposal(null,1814,1730),false);
});

test('Rendering derives valid independent ranges without optional backend windows',()=>{
  const original={...current,lmax:1611,rmax:1647};
  const left=triggerControls(original,original,1650,'l');
  const right=triggerControls(original,original,1647,'r');
  assert.equal(left.selected,1650);assert.equal(right.selected,1647);
  assert.equal(left.original,1611);assert.equal(right.original,1647);
  assert.ok(left.range.min<=1611&&left.range.max>=1820);
  assert.notEqual(markerPosition(1611,left.range.min,left.range.max),markerPosition(1820,left.range.min,left.range.max));
});

test('Dragging within the viewport does not move either reference position',()=>{
  const before=triggerControls(current,current,1814,'l');
  const after=triggerControls(current,current,1834,'l');
  assert.deepEqual(after.range,before.range);
  assert.equal(markerPosition(1814,before.range.min,before.range.max),markerPosition(1814,after.range.min,after.range.max));
  assert.equal(markerPosition(1820,before.range.min,before.range.max),markerPosition(1820,after.range.min,after.range.max));
});

test('Invalid values never reach a native slider and missing defaults are not invented',()=>{
  const incomplete=triggerControls(null,current,1611,'l');
  assert.equal(incomplete.range,null);assert.ok(incomplete.error);
  const missingDefault=triggerControls(current,null,1814,'l');
  assert.equal(missingDefault.original,null);assert.ok(missingDefault.range);
  const badSelection=triggerControls(current,current,NaN,'l');
  assert.equal(badSelection.selected,1814);assert.ok(badSelection.error);
  assert.equal(triggerControls({...current,lmax:65535,lmin:65534},current,65535,'l').range,null);
});

test('Compiled source map contains the current frontend sources byte-for-byte',async()=>{
  const {readFileSync,readdirSync}=await import('node:fs');
  const map=JSON.parse(readFileSync(new URL('../dist/index.js.map',import.meta.url),'utf8'));
  for(const file of readdirSync(new URL('../src/',import.meta.url)).filter(name=>/\.tsx?$/.test(name))) {
    const index=map.sources.findIndex(source=>source.endsWith(`/src/${file}`));
    assert.notEqual(index,-1,`Source map is missing ${file}`);
    assert.equal(map.sourcesContent[index],readFileSync(new URL(`../src/${file}`,import.meta.url),'utf8'),`Stale build: ${file}`);
  }
});

test('Read with a higher MIN retains stored Selected values and a shared range',()=>{
  const changed={...current,lmin:600};
  const left=triggerControls(changed,current,550,'l',1730);
  const right=triggerControls(changed,current,1730,'r',550);
  assert.equal(left.selected,550);assert.match(left.error,/saved selection was kept/);
  assert.deepEqual(left.range,right.range);
  assert.equal(validProposal(changed,550,1730),false);
});
