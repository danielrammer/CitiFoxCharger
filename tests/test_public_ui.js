// Exercise the shipped UI in an inert DOM. No fetch, device or timer runs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function fixture(){
 const nodes=new Map();
 function node(id){if(!nodes.has(id)){const section={hidden:false};nodes.set(id,{id,hidden:false,disabled:false,value:'80',textContent:'',dataset:{},style:{setProperty(){}},classList:{toggle(){}},addEventListener(){},setAttribute(){},removeAttribute(){},querySelectorAll(){return[];},closest(){return section;}});}return nodes.get(id);}
 const document={getElementById:node,querySelector:node,querySelectorAll(){return[];},documentElement:node('root'),addEventListener(){}};
 const context=vm.createContext({document,window:{matchMedia(){return{matches:false,addEventListener(){}};},addEventListener(){}},localStorage:{getItem(){return null;},setItem(){}},CitiFoxI18n:{locale(){return'en';}},setTimeout(){},setInterval(){},Date,Number,Set,console});
 let source=fs.readFileSync('citifoxcharger/web/app.js','utf8');
 // Block asynchronous startup; we drive the real access/control render functions below.
 source=source.replace('\ninit();','\n');
 vm.runInContext(source,context);
 vm.runInContext('renderLimitTest=()=>{};updateHeaterControls=()=>{};updateEta=()=>{};',context);
 return {context,node,run:code=>vm.runInContext(code,context)};
}
const publicUi=fixture();
publicUi.run('renderAccess({access:{local_admin:false,public_access:true,can_update:false,can_limit_test:false},key:{},network:{enabled:true,url:"https://192.168.0.95:8766/"}})');
for(const id of ['app-update-panel','key-form','config-form','device-link','network-url','network-certificate-download','ownership-controls','charging-policy-form','home-network-panel'])assert.equal(publicUi.node(id).hidden,true,id);
assert.equal(publicUi.node('location-label').textContent,'Public access');
publicUi.run('latest={charging:{can_release_native_guard:true,can_start_manual:true,can_shutdown:true,can_stop:true},skoda:{soc:50,available:true},limit_test:{active:false}};updateControls()');
assert.equal(publicUi.node('force').disabled,true);
assert.equal(publicUi.node('limit-test-panel').closest().hidden,true);
publicUi.run('latest.limit_test.active=true;latest.limit_trial={can_start:true};updateControls()');
assert.equal(publicUi.node('force').disabled,true);
assert.equal(publicUi.node('open-limit-trial').hidden,true);
const lan=fixture();
lan.run('renderAccess({access:{local_admin:false,public_access:false,can_update:true,can_limit_test:true},key:{}})');
assert.equal(lan.node('app-update-panel').hidden,false);
assert.equal(lan.node('location-label').textContent,'Home network access');
assert.equal(lan.node('config-form').hidden,false);
console.log('PASS: public controls hidden/disabled; LAN update access preserved.');

publicUi.run('renderChargingPolicy({only:true,automatic:true,start:"22:00",end:"05:00",target:83})');
assert.equal(publicUi.node('ownership-controls').hidden,true);
assert.equal(publicUi.node('charging-policy-form').hidden,true);
assert.equal(publicUi.node('citifox-only').disabled,true);
lan.run('renderChargingPolicy({only:false,automatic:false,start:"22:00",end:"05:00",target:83})');
assert.equal(lan.node('ownership-controls').hidden,false);
assert.equal(lan.node('charging-policy-form').hidden,false);
assert.equal(lan.node('policy-status').hidden,true);

assert.equal(lan.node('home-network-panel').hidden,false);
