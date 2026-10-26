// Exercise the real download handler without a browser, device calls or timers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function fixture(response) {
 const nodes = new Map(), links = [], timers = [];
 function node(id) {
  if (!nodes.has(id)) nodes.set(id, {hidden:true,value:'80',dataset:{},textContent:'',listeners:{},
   classList:{toggle(){}},addEventListener(name,fn){this.listeners[name]=fn;},
   setAttribute(){},removeAttribute(){},click(){this.clicked=true;},remove(){}});
  return nodes.get(id);
 }
 const document = {getElementById:node,querySelectorAll(){return[];},addEventListener(){},documentElement:node('root'),
  body:{append(link){links.push(link);}},createElement(){return node('file');}};
 let calls=0;
 const context=vm.createContext({document,window:{matchMedia(){return{matches:false,addEventListener(){}};},addEventListener(){}},
  localStorage:{getItem(){return null;},setItem(){}},CitiFoxI18n:{locale(){return'en';}},
  setTimeout(fn,ms){timers.push({fn,ms});return timers.length;},setInterval(){},clearTimeout(){},
  AbortController,URL:{createObjectURL(){return'blob:test';},revokeObjectURL(){}},
  fetch:async(path,options)=>{calls++;assert.equal(path,'/api/network/certificate');assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');return response;},
  Date,Number,Set,console});
 vm.runInContext(fs.readFileSync('citifoxcharger/web/app.js','utf8').replace('\ninit();','\n'),context);
 return {node,links,timers,calls:()=>calls,click:()=>node('download-certificate').listeners.click({preventDefault(){}})};
}

(async()=>{
 const success=fixture({ok:true,headers:{get(){return'application/pkix-cert';}},blob:async()=>new Blob(['test certificate'])});
 await success.click();
 assert.equal(success.links.length,1);
 assert.equal(success.links[0].download,'CitiFoxCharger-Home-Network.cer');
 assert.equal(success.links[0].clicked,true);
 assert.match(success.node('certificate-download-status').textContent,/browser downloads/);
 assert.equal(success.node('certificate-download-status').hidden,false);

 for(const [status,message] of [[403,'Access from another website is not allowed.'],[404,'Public certificate unavailable.']]){
  const failed=fixture({ok:false,status,json:async()=>({error:message})});
  await failed.click();await failed.click(); // A failed attempt must allow a retry.
  assert.equal(failed.calls(),2);assert.equal(failed.links.length,0);
  assert.equal(failed.node('certificate-download-status').textContent,message);
 }
 const proxy=fixture({ok:true,headers:{get(){return'text/html';}}});
 await proxy.click();
 assert.equal(proxy.links.length,0);assert.match(proxy.node('certificate-download-status').textContent,/did not return a public certificate/);
 const fallback=fixture({ok:false,status:502,json:async()=>{throw Error('HTML gateway error');}});
 await fallback.click();assert.match(fallback.node('certificate-download-status').textContent,/HTTP 502/);
 console.log('PASS: certificate download saves binary data; rejected/missing/proxy responses show errors and remain retryable.');
})().catch(error=>{console.error(error);process.exitCode=1;});
