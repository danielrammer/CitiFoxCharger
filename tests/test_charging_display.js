// Rendering only: no startup, timers, network requests or charging commands.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const now=Date.parse('2026-10-27T12:00:00Z');
class FixedDate extends Date {constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
const nodes=new Map();
function node(id){
 if(!nodes.has(id))nodes.set(id,{hidden:false,value:'85',dataset:{},textContent:'',attributes:{},
  style:{setProperty(key,value){this[key]=value;}},classList:{toggle(){}},
  setAttribute(key,value){this.attributes[key]=value;},removeAttribute(){},addEventListener(){}});
 return nodes.get(id);
}
const context=vm.createContext({document:{getElementById:node,querySelectorAll(){return[];},addEventListener(){},documentElement:node('root')},
 window:{matchMedia(){return{matches:false,addEventListener(){}};},addEventListener(){}},
 localStorage:{getItem(){return null;}},CitiFoxI18n:{locale(){return'en-GB';}},setTimeout(){},setInterval(){},Date:FixedDate,console});
vm.runInContext(fs.readFileSync('citifoxcharger/web/app.js','utf8').replace('\ninit();','\n'),context);
const run=source=>vm.runInContext(source,context);
run(`latest={skoda:{soc:80,stale:true},charging:{status:'monitoring',energy_estimate:{valid:true,estimated_soc:81.4}},
 smartfox:{charger:{available:true,telemetry_available:true,power_kw:7.2,read_at:new Date().toISOString()}}};updateEta();updateBattery();`);
assert.match(node('charge-eta').textContent,/^Estimated: 00:15 to target · Reached around /);
assert.ok(node('charge-eta').textContent.endsWith(new Date(now+15*60000).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',hour12:false})));
assert.equal(node('soc-target-label').textContent,'85 %');
assert.equal(node('soc-target').style['--target'],'85%');
assert.equal(node('soc-progress').attributes['aria-valuenow'],'81.4');

// A recorded vehicle SOC remains distinct from the current energy estimate.
run(`renderVehicleReport({soc:80,captured_at:'2026-10-27T11:00:00Z'},true)`);
assert.match(node('car-time').textContent,/Last vehicle report: 80 % · /);
assert.ok(node('car-time').textContent.includes(new Date('2026-10-27T11:00:00Z').toLocaleString('en-GB',{dateStyle:'short',timeStyle:'medium'})));
assert.match(node('car-read').textContent,/not a live vehicle measurement/);
assert.equal(node('car-read').hidden,false);
run(`renderVehicleReport({soc:82,captured_at:'2026-10-27T11:55:00Z'},false)`);
assert.equal(node('car-read').hidden,true);

// Do not invent an ETA when power is zero, old, or interrupted for switching.
run('latest.smartfox.charger.power_kw=0;updateEta()');
assert.equal(node('charge-eta').textContent,'Remaining time: available while charging');
run('latest.smartfox.charger.power_kw=7.2;latest.smartfox.charger.read_at=new Date(Date.now()-31000).toISOString();updateEta()');
assert.equal(node('charge-eta').textContent,'Remaining time: available while charging');
run('latest.smartfox.charger.read_at=new Date().toISOString();latest.smartfox.charger.phase_switching=true;updateEta()');
assert.equal(node('charge-eta').textContent,'Remaining time: available after phase switching');
run('latest.charging.energy_estimate.valid=false;updateEta()');
assert.equal(node('charge-eta').textContent,'Remaining time: waiting for current vehicle data');

node('target').value='100';run('updateBattery()');
assert.equal(node('soc-target').style['--target'],'calc(100% - 1px)');
node('target').value='1';run('updateBattery()');
assert.equal(node('soc-target').style['--target'],'1%');
console.log('PASS: target marker, duration/arrival estimate, power/data guards and last measured vehicle SOC remain distinct.');
