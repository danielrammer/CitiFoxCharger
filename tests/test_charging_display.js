// Rendering only: no startup, timers, network requests or charging commands.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const now=Date.parse('2026-10-27T12:00:00Z');
class FixedDate extends Date {constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
const nodes=new Map();
function node(id){
 if(!nodes.has(id))nodes.set(id,{hidden:false,value:'85',dataset:{},textContent:'',attributes:{},
  style:{setProperty(key,value){this[key]=value;}},classList:{toggle(){}},
  setAttribute(key,value){this.attributes[key]=value;},removeAttribute(){},addEventListener(){},
  closest(){return node(id+'-section');},querySelector(selector){return node(id+selector);}});
 return nodes.get(id);
}
const context=vm.createContext({document:{getElementById:node,querySelectorAll(){return[];},addEventListener(){},documentElement:node('root')},
 window:{matchMedia(){return{matches:false,addEventListener(){}};},addEventListener(){}},
 localStorage:{getItem(){return null;}},CitiFoxI18n:{locale(){return'en-GB';},language(){return'en';}},setTimeout(){},setInterval(){},Date:FixedDate,console});
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

// A+ takeover is available, but a manual session must not offer a duplicate Start.
node('target').value='85';
run(`latest={skoda:{soc:80,available:true,stale:true},controls:{start:true,stop:true},
 charging:{status:'idle',can_start_manual:true,can_start_manual_with_stale:true,can_stop:true},
 smartfox:{charger:{mode:'A+',available:true,telemetry_available:true,power_kw:2,
 read_at:new Date().toISOString()}}};updateControls();`);
assert.equal(node('force').disabled,false,'A+ surplus charging can be taken over');
assert.equal(node('force').textContent,'Start charging');
for(const status of ['starting','monitoring','paused','interrupted']){
 run(`latest.charging={status:${JSON.stringify(status)},strategy:'smartfox_energy',
 can_start_manual:true,can_start_manual_with_stale:true,can_adjust_target:true,can_stop:true};
 latest.smartfox.charger.mode='M+';updateControls();`);
 assert.equal(node('force').disabled,true,`no duplicate Start while ${status}`);
 assert.equal(node('target').disabled,false,`target remains adjustable while ${status}`);
 assert.equal(node('stop').disabled,false,`Stop remains available while ${status}`);
}
node('target').value='87';run('updateControls()');
assert.equal(node('force').disabled,true,'changing the target must not re-enable Start');
assert.equal(node('target-value').textContent,'87 %');
assert.equal(node('soc-target-label').textContent,'87 %');
run(`latest.charging.status='stop_pending';latest.charging.stop_requested=true;
 latest.charging.can_adjust_target=false;updateControls();`);
assert.equal(node('force').disabled,true,'pending Stop must not offer Start');
run(`latest.charging={status:'complete',can_start_manual:true,can_stop:true};updateControls();`);
assert.equal(node('force').disabled,false,'a completed session allows a new Start');
run(`latest.charging={status:'starting',can_start_manual:true,can_retry_start:true,can_stop:true};updateControls();`);
assert.equal(node('force').disabled,false,'explicit backend-authorized legacy retry remains available');
assert.equal(node('force').textContent,'↻ Retry start');
run(`mutating=true;updateControls();`);
assert.equal(node('force').disabled,true,'pending requests disable Start immediately');
console.log('PASS: manual-session Start lock, live target adjustment, Stop, A+ takeover and explicit retry.');

run(`renderSolar({configured:true,sunrise_at:'2026-10-27T06:00:00Z',sunset_at:'2026-10-27T17:00:00Z',night:true,three_phase_selected:true})`);
assert.match(node('solar-times').textContent,/^Sunrise .* · Sunset /);
assert.match(node('solar-policy').textContent,/preselected.*switching may still occur/);
run(`renderSolar({configured:true,night:false,three_phase_selected:false})`);
assert.match(node('solar-policy').textContent,/Manual charging can be requested at any time/);
