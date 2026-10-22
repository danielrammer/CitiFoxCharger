"use strict";
const $ = id => document.getElementById(id);
let csrf="", refreshing=false, mutating=false, latest=null, targetInitialized=false, appStopped=false, refreshTimer=null, consentFor="", targetEditing=false, localAdmin=true;
let appUpdating=false,updateAllowed=false,updateKeyReady=false,updateJob=null,updatePreviousJob=null,updateStarted=0;
const activeStates=new Set(["starting","monitoring","stop_pending","restore_pending","paused","interrupted"]);
const themeMedia=window.matchMedia("(prefers-color-scheme: dark)");
let themeChoice="system";
try{const saved=localStorage.getItem("citifox-theme");if(["system","light","dark"].includes(saved))themeChoice=saved;}catch{}
function applyTheme(choice,persist=true){
 themeChoice=choice;const dark=choice==="dark"||(choice==="system"&&themeMedia.matches);
 document.documentElement.dataset.theme=dark?"dark":"light";$("theme-choice").value=choice;
 $("theme-toggle").textContent=dark?"☀":"☾";$("theme-toggle").setAttribute("aria-label",dark?"Switch to light mode":"Switch to dark mode");$("theme-toggle").setAttribute("aria-pressed",String(dark));
 if(persist)try{localStorage.setItem("citifox-theme",choice);}catch{}
}
applyTheme(themeChoice,false);
themeMedia.addEventListener("change",()=>{if(themeChoice==="system")applyTheme("system",false);});
$("theme-toggle").addEventListener("click",()=>applyTheme(document.documentElement.dataset.theme==="dark"?"light":"dark"));
$("theme-choice").addEventListener("change",event=>applyTheme(event.target.value));
const states={CHARGING:"Charging",CONNECT_CABLE:"Connect cable",CONSERVING:"Charge target reached",READY_FOR_CHARGING:"Ready to charge",DISCHARGING:"Discharging",CHARGING_INTERRUPTED:"Charging interrupted"};
const date=value=>{const d=new Date(value);return value&&!Number.isNaN(d.getTime())?d.toLocaleString(CitiFoxI18n.locale(),{dateStyle:"short",timeStyle:"medium"}):"—";};
function age(value){const t=Date.parse(value);if(!Number.isFinite(t))return "Vehicle time unknown";const seconds=Math.max(0,Math.round((Date.now()-t)/1000));return seconds<60?"Vehicle data a few seconds ago":seconds<3600?`Vehicle data ${Math.floor(seconds/60)} minutes ago`:`Vehicle data from ${date(value)}`;}
function ageBrief(value){const seconds=(Date.now()-Date.parse(value))/1000;if(!Number.isFinite(seconds)||seconds< -300)return "Time unknown";return seconds<60?"just now":seconds<3600?`${Math.floor(seconds/60)} min ago`:`${Math.floor(seconds/3600)} h ago`;}
function error(message){$("global-error").textContent=message;$("global-error").hidden=false;}
function toast(message){$("toast").textContent=message;$("toast").hidden=false;setTimeout(()=>$("toast").hidden=true,6000);}
async function api(path,data){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);try{const response=await fetch(path,{method:data===undefined?"GET":"POST",credentials:"same-origin",cache:"no-store",signal:controller.signal,headers:data===undefined?{}:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:data===undefined?undefined:JSON.stringify(data)});const result=await response.json();if(!response.ok){const problem=new Error(result.error||"Request failed.");problem.status=response.status;throw problem;}return result;}catch(e){if(e.name==="AbortError")throw new Error("No response. Check charging status before trying again.");throw e;}finally{clearTimeout(timer);}}
function updateControls(){
 if(!latest)return;
 $("reload-app").disabled=mutating||appUpdating;
 if(appStopped){for(const id of ["force","stop","target","quit-app","allow-stale","heater-mode","heater2-mode","hot-water-mode","station-mode"])$(id).disabled=true;return;}
 const session=latest.charging,car=latest.skoda,active=activeStates.has(session?.status),target=Number($("target").value),soc=car.soc;
 const staleOption=session?.can_force_with_stale===true&&typeof soc==="number"&&target>soc;
 const token=staleOption?`${car.captured_at}|${soc}|${session.request_id||""}`:"";
 if(token!==consentFor||!staleOption){$("allow-stale").checked=false;consentFor=token;}
 $("stale-consent").hidden=!staleOption;$("allow-stale").disabled=mutating;
 $("stale-consent-text").textContent=`Use last reported battery level (${soc??"—"} % · ${date(car.captured_at)})`;
 const retry=session?.can_retry_start===true||session?.can_retry_start_with_stale===true;
 $("target-value").textContent=`${target} %`;$("target").disabled=mutating||(active&&!session?.can_adjust_target);
 $("force").textContent=retry?"↻ Retry start":"↯ Force charge";
 $("force").disabled=mutating||!(session?.can_force||(staleOption&&$("allow-stale").checked))||typeof soc!=="number"||target<=soc;
 $("stop").disabled=mutating||!(session?.can_stop??(active||latest.controls.stop));
 let hint="";
 if(!car.available)hint=car.code==="not_configured"?"Configure vehicle access in Settings.":"The vehicle is currently unreachable.";
 else if(target<=soc)hint="The selected target is already reached.";
 else if(session?.force_reason&&!session.can_force&&!staleOption)hint=session.force_reason;
 else if(car.stale&&!staleOption&&!session?.awaiting_fresh_soc)hint="Waiting for current vehicle data.";
 if((active||session?.status==="error")&&session?.message)hint="";
 $("operation-hint").textContent=hint;$("operation-hint").hidden=!hint;
 updateHeaterControls();updateEta();
 const updating=appUpdating||latest.app_update_pending===true;
 $("update-app").disabled=mutating||updating||!updateAllowed||!session?.can_shutdown||!updateKeyReady;
 $("update-hint").textContent=!updateKeyReady?"Save the API key with Windows encryption before updating.":!session?.can_shutdown?"Stop charging and wait for confirmation before updating.":"Update from GitHub, restart the backend and reload this page.";
 if(updating)for(const id of ["force","target","allow-stale","quit-app","heater-mode","heater2-mode","hot-water-mode","station-mode"])$(id).disabled=true;
}
function updateEta(){
 const car=latest.skoda,charger=latest.smartfox.charger||{},target=Number($("target").value),soc=car.soc;
 const powerAge=(Date.now()-Date.parse(charger.read_at))/1000,power=charger.power_kw;
 let text;
 if(!car.available||typeof soc!=="number")text="Remaining time: vehicle data missing";
 else if(car.stale)text="Remaining time: waiting for current vehicle data";
 else if(target<=soc)text="Target battery level reached";
 else if(livePhaseSwitch(charger))text="Remaining time: available after phase switching";
 else if(!charger.available||charger.telemetry_available!==true||!Number.isFinite(powerAge)||powerAge< -5||powerAge>30||typeof power!=="number"||power<=.1)text="Remaining time: available while charging";
 else{const minutes=Math.ceil((32.3*(target-soc)/100/(power*.9)*60)/5)*5,hours=Math.floor(minutes/60),rest=minutes%60,duration=[hours?hours+" h":null,rest?rest+" min":null].filter(Boolean).join(" ");text=`Estimated time: about ${duration} at ${number(power)} kW`;}
 $("charge-eta").textContent=text;$("charge-eta").dataset.estimate=String(text.startsWith("Estimated")||text.startsWith("Target"));
}
const number=(value,digits=2)=>typeof value==="number"&&Number.isFinite(value)?value.toLocaleString(CitiFoxI18n.locale(),{minimumFractionDigits:digits,maximumFractionDigits:digits}):"—";
function livePhaseSwitch(charger){const seconds=(Date.now()-Date.parse(charger.read_at))/1000;return charger.available&&charger.mode!=="OFF"&&charger.telemetry_available===true&&charger.phase_switching===true&&Number.isFinite(seconds)&&seconds>=-5&&seconds<=30;}
function phaseMessage(charger){const seconds=charger.phase_switch_seconds;return Number.isInteger(seconds)&&seconds>=0&&seconds<=3600?`Phase switching in progress. Smartfox reports ${seconds} s. Waiting for charging permission.`:"Phase switching in progress. Waiting for charging permission.";}
function updateHeaterControls(){
 const charger=latest?.smartfox?.charger||{},energy=charger.energy||{},ageSeconds=(Date.now()-Date.parse(energy.read_at))/1000;
 const live=charger.available&&Number.isFinite(ageSeconds)&&ageSeconds>=-5&&ageSeconds<=30;
 for(const [device,id] of [["heater","heater"],["heater2","heater2"],["hot_water","hot-water"]]){const value=energy[device]||{};$(id+"-mode").disabled=mutating||!live||!value.control_available;}
 $("station-mode").disabled=mutating||!live||!charger.control_available;
 const active=activeStates.has(latest?.charging?.status),manual=active?!latest.charging.stop_requested&&latest.controls.start&&latest.controls.stop:Number($("target").value)>latest.skoda.soc&&(latest?.charging?.can_force||(latest?.charging?.can_force_with_stale&&$("allow-stale").checked));
 $("station-mode").querySelector('option[value="M+"]').disabled=!manual;
 $("station-mode").querySelector('option[value="A+"]').disabled=active&&latest.charging.stop_guard===true;
}
function renderEnergy(charger){
 const energy=charger.energy||{},readAge=(Date.now()-Date.parse(energy.read_at))/1000;
 const live=charger.available&&Number.isFinite(readAge)&&readAge>=-5&&readAge<=30;
 const grid=live?energy.grid_kw:null;
 $("house-power").textContent=number(live?energy.house_kw:null);$("grid-power").textContent=number(typeof grid==="number"?Math.abs(grid):null);
 $("grid-label").textContent=typeof grid==="number"&&grid<0?"Grid export":"Grid import";
 $("grid-ring").classList.toggle("exporting",typeof grid==="number"&&grid<0);
 $("energy-flow").classList.toggle("flowing",typeof grid==="number"&&Math.abs(grid)>.05);
 $("energy-flow").classList.toggle("exporting",typeof grid==="number"&&grid<0);
 $("energy-badge").textContent=live?"Live · Smartfox":"No data";$("energy-badge").className=`badge ${live?"good":"warn"}`;
 const heater=live?energy.heater||{}:{};
 const percentPrimary=heater.percent_primary===true||(typeof heater.percent==="number"&&heater.percent>0&&heater.power_kw===0);
 $("heater-power").textContent=percentPrimary?number(heater.percent,0)+" %":number(heater.power_kw)+" kW";
 $("heater-power").title=percentPrimary?"Output actuation; not measured electrical consumption":"Smartfox power estimate";
 $("heater-detail").textContent=[typeof heater.temperature_c==="number"?number(heater.temperature_c,1)+" °C":null,!percentPrimary&&typeof heater.percent==="number"?number(heater.percent,0)+" %":null].filter(Boolean).join(" · ")||"—";
 $("heater-status").textContent=heater.status||"Unavailable";$("heater-dot").classList.toggle("on",typeof heater.percent==="number"?heater.percent>0:typeof heater.power_kw==="number"&&heater.power_kw>.01);
 for(const [device,id] of [["heater","heater"],["heater2","heater2"],["hot_water","hot-water"]]){
  const value=live?energy[device]||{}:{},select=$(id+"-mode");
  select.value=({A:"auto",M:"manual",OFF:"off"})[value.mode]||"unknown";
  select.title=value.control_reason||"Change mode";
  const manual=select.querySelector('option[value="manual"]');
  if(device==="heater"){manual.textContent=typeof value.manual_percent==="number"?`Manual · ${value.manual_percent} %`:"Manual";manual.disabled=typeof value.manual_percent!=="number";}else manual.disabled=false;
 }
 $("station-mode").value=live&&["OFF","A+","M+"].includes(charger.mode)?charger.mode:"unknown";
 for(const [key,id] of [["heater2","heater2"],["hot_water","hot-water"]]){
  const device=live?energy[key]||{}:{};
  $(id+"-state").textContent=device.on===true?"On":device.on===false?"Off":"—";
  $(id+"-detail").textContent=typeof device.temperature_c==="number"?number(device.temperature_c,1)+" °C":"—";
  $(id+"-status").textContent=device.status||"Unavailable";$(id+"-dot").classList.toggle("on",device.on===true);
 }
 const stationLive=live&&charger.telemetry_available===true;
 $("station-power").textContent=number(stationLive?charger.power_kw:null)+" kW";$("station-detail").textContent=stationLive?charger.mode||"—":"—";
 $("station-status").textContent=stationLive&&charger.mode==="OFF"?"Charger Off":livePhaseSwitch(charger)?"Phase switching":stationLive&&charger.status_code===3&&typeof charger.power_kw==="number"&&charger.power_kw<=.1?"No charging power":stationLive?charger.status_text||"Status unknown":"Unavailable";
 $("station-dot").classList.toggle("on",stationLive&&charger.power_kw>.1);
 $("energy-time").textContent=live?`Smartfox · updated ${date(energy.read_at)}`:"Smartfox data are missing or stale.";
}
function render(data){latest=data;const car=data.skoda,sf=data.smartfox,charger=sf.charger||{},session=data.charging;
 renderEnergy(charger);
 $("soc").textContent=car.soc??"—";$("soc-track").style.width=`${car.soc??0}%`;$("battery-fill").style.width=`${car.soc??0}%`;
 const powerAge=(Date.now()-Date.parse(charger.read_at))/1000,livePower=charger.available&&Number.isFinite(powerAge)&&powerAge>=-5&&powerAge<=30&&charger.telemetry_available!==false&&typeof charger.power_kw==="number";
 const switching=livePhaseSwitch(charger),isCharging=!switching&&(livePower?charger.power_kw>0.1:car.state==="CHARGING"&&!car.stale&&car.available);
 $("car-state").textContent=switching?"Phase switching":isCharging?"Charging":livePower&&charger.mode==="OFF"?"Charger Off":livePower&&charger.status_code===2?"Plugged in · ready":livePower&&(car.state==="CHARGING"||charger.status_code===3)?"No charging power":states[car.state]||"Status unknown";$("charging-dot").classList.toggle("charging",isCharging);$("charging-dot").classList.toggle("switching",switching);document.querySelector(".charging-card").classList.toggle("is-charging",isCharging);
 $("power-reading").textContent=livePower?charger.power_kw.toLocaleString(CitiFoxI18n.locale(),{minimumFractionDigits:2,maximumFractionDigits:2})+" kW":"— kW";
 const carLabel=car.available?(car.stale?"Stale · "+ageBrief(car.captured_at):"Connected"):car.code==="not_configured"?"Not configured":"Unreachable";$("car-badge").textContent=carLabel;$("car-badge").className=`badge ${car.available&&!car.stale?"good":"warn"}`;
 $("car-time").textContent=`Last vehicle report: ${date(car.captured_at)}`;$("car-read").textContent=`Last successful query: ${date(car.read_at)}`;
 $("charger-mode").textContent=charger.available?`Smartfox · ${charger.mode||"Mode unknown"}`:"Smartfox · unreachable";
 $("connection-summary").textContent=`Škoda ${car.available?"connected":"unreachable"} · Smartfox ${sf.available?"connected":"unreachable"}`;
 if(session&&!targetEditing&&(!targetInitialized||activeStates.has(session.status))){let target=session.target_percent;try{if(!targetInitialized&&!activeStates.has(session.status)){const saved=Number(localStorage.getItem("charging-target"));if(saved>=1&&saved<=100)target=saved;}}catch{}$("target").value=target;targetInitialized=true;}
 const active=activeStates.has(session?.status),power=data.monitoring?.power;
 $("monitoring-note").hidden=!active;
 $("monitoring-note").textContent=active?(power?.warning||(!data.monitoring?.running?"Background monitoring is unconfirmed. Please check the app.":power?.requested&&power.on_ac_power===true?"Monitoring in background · screen may turn off.":"Monitoring active. Keep the tablet plugged in and prevent automatic sleep.")):"";
 $("quit-app").disabled=!localAdmin||!session?.can_shutdown||mutating;
 $("session-status").classList.toggle("session-quiet",session?.status==="monitoring"&&isCharging&&!session?.awaiting_fresh_soc&&!session?.stop_requested);
 $("normal-mode-note").textContent=session?.user_charger_mode==="OFF"?(charger.available&&charger.mode==="OFF"?"Charger explicitly switched Off.":"Off selected · change is unconfirmed."):session?.stop_guard?"Charger kept Off until the vehicle confirms the stop.":"Normal mode A+ · schedules are preserved.";
 const showPhase=switching&&!session?.stop_requested;$("session-status").hidden=!showPhase&&!session?.message;$("session-status").textContent=showPhase?phaseMessage(charger):session?.message||"";$("session-status").classList.toggle("phase-wait",showPhase);$("session-status").classList.toggle("safety-stopped",session?.status==="error"&&["missing_soc","missing_initial_soc","phase_wait"].includes(session?.stop_cause));
 $("car-message").textContent=car.message||"";$("sf-notice").textContent=sf.notice||"";$("sf-control-reason").textContent=charger.control_reason||charger.message||"";
 $("sf-web-meta").textContent=`Charge mode: ${charger.mode||"unknown"} · Power: ${charger.power_kw??"—"} kW · Wallbox: ${charger.status_text||"unknown"} · Setpoint: ${charger.offered_current_a??"—"} A · Last read: ${date(charger.read_at||sf.read_at)}`;
 $("api-meta").textContent=`Next query in ${data.next_skoda_seconds} s · Key valid until ${date(car.key_expires_at)} · Quota left: ${car.quota_remaining??"unknown"}${car.errors?.length?" · "+car.errors.join(", "):""}`;
 $("register-rows").replaceChildren(...(sf.registers||[]).map(row=>{const tr=document.createElement("tr");[String(row.address),row.words?.join(" · ")??"—",row.candidate??"—",row.error||"Read"].forEach(text=>{const td=document.createElement("td");td.textContent=text;tr.append(td);});return tr;}));
 $("events").replaceChildren(...(data.events.length?data.events:[{message:"No actions yet."}]).map(event=>{const li=document.createElement("li");li.textContent=(event.at?date(event.at)+" · ":"")+event.message;return li;}));updateControls();}
async function refresh(){if(refreshing||appStopped||appUpdating)return;refreshing=true;$("refresh").setAttribute("aria-busy","true");try{render(await api("/api/status"));$("global-error").hidden=true;}catch(e){$("force").disabled=true;$("stop").disabled=true;error(e.message||"The local app is unreachable.");}finally{refreshing=false;$("refresh").removeAttribute("aria-busy");}}
function renderAccess(result){
 localAdmin=result.access?.local_admin!==false;
 updateAllowed=result.access?.can_update===true||localAdmin;$("app-update-panel").hidden=!updateAllowed;
 updateKeyReady=!result.key?.configured||result.key?.persisted===true;
 const network=result.network||{},link=$("network-url");
 $("network-status").textContent=network.message||"Home network access is disabled.";
 link.hidden=!network.enabled;link.textContent=network.url||"";if(network.url)link.href=network.url;
 if(!localAdmin){$("location-label").textContent="Home network access";$("key-form").hidden=true;document.querySelector(".app-control").hidden=true;$("config-form").querySelectorAll("input,select,button").forEach(element=>element.disabled=true);}
}
async function loadConfig(){const result=await api("/api/config");renderAccess(result);const sf=result.config.smartfox,car=result.config.skoda;for(const [id,value]of Object.entries({host:sf.host,port:sf.port,unit:sf.unit_id,firmware:sf.firmware,"sf-poll":sf.poll_seconds,"sf-timeout":sf.timeout_seconds,profile:sf.profile,"word-order":sf.word_order,vin:car.vin,"car-poll":car.poll_seconds,"car-stale":car.stale_seconds}))$(id).value=value;$("interpretation").checked=sf.interpretation_confirmed;$("persist").disabled=!result.key.persistence_available;$("persist").checked=result.key.persisted;$("clear-key").hidden=!result.key.configured;$("key-status").textContent=result.key.warning||(result.key.configured?(result.key.persisted?"Access saved with Windows encryption.":"Access saved until the app exits."):"No key configured.");$("sf-meta").textContent=`Pro 2 · ${sf.host} · ${sf.firmware}`;$("device-link").href=`http://${sf.host}/`;}
async function mutation(fn,allowDuringUpdate=false){if(mutating||(appUpdating&&!allowDuringUpdate))return;mutating=true;updateControls();let failure=null;try{await fn();}catch(e){failure=e.message;}finally{mutating=false;await refresh();if(failure)error(failure);}}
function updateBusy(busy){appUpdating=busy;for(const form of [$("config-form"),$("key-form")])form.querySelectorAll("input,select,button").forEach(node=>node.disabled=busy||!localAdmin);updateControls();}
async function pollUpdate(){
 if(!appUpdating)return;
 if(Date.now()-updateStarted>900000){$("update-status").textContent="Update status is still unconfirmed. Check update.log or Update.cmd on the tablet.";return;}
 try{
  csrf=(await api("/api/session")).csrf;
  const state=await api("/api/app/update/status");
  if(!updateJob&&state.job_id&&state.job_id!==updatePreviousJob)updateJob=state.job_id;
  if(!updateJob&&["idle","complete","failed"].includes(state.phase)){updateBusy(false);await loadConfig();await refresh();error("No new update job was confirmed. Check Update.cmd on the tablet.");return;}
  if(state.job_id===updateJob){
   $("update-status").textContent=state.message;
   if(state.phase==="complete"){window.location.reload();return;}
   if(state.phase==="failed"){updateBusy(false);await loadConfig();await refresh();error(state.message);return;}
  }
 }catch{ $("update-status").textContent="Waiting for the backend to restart. This page will reload automatically."; }
 setTimeout(pollUpdate,1500);
}
$("update-app").addEventListener("click",()=>{if(!$("update-app").disabled)$("update-confirmation").hidden=false;});
$("cancel-update").addEventListener("click",()=>{$("update-confirmation").hidden=true;});
$("confirm-update").addEventListener("click",async()=>{
 if(appUpdating||mutating||$("update-app").disabled)return;
 $("update-confirmation").hidden=true;
 updatePreviousJob=updateJob;updateJob=null;updateStarted=Date.now();updateBusy(true);$("global-error").hidden=true;$("update-status").textContent="Update queued.";
 try{
  const state=await api("/api/app/update",{});updateJob=state.job_id;updateStarted=Date.now();pollUpdate();
 }catch(e){if(typeof e.status!=="number"){pollUpdate();return;}updateBusy(false);await loadConfig();await refresh();error(e.message);}
});
$("reload-app").addEventListener("click",()=>{if(!mutating&&!appUpdating)window.location.reload();});
async function loadRelease(){const health=await api("/health"),release=health.release||{version:health.version},day=/^\d{4}-\d{2}-\d{2}$/.test(release.date||"")?release.date.split("-").reverse().join("."):"—",hash=/^[0-9a-f]{8}$/.test(release.git_hash||"")?release.git_hash:"—";$("release-info").textContent=`v${release.version||"—"} | ${hash} | ${day}`;}
async function resumeUpdate(){if(!updateAllowed)return;const state=await api("/api/app/update/status");updateJob=state.job_id;$("update-status").textContent=state.phase==="complete"?"Update complete.":state.message;if(!["idle","complete","failed"].includes(state.phase)){updateStarted=Date.now();updateBusy(true);pollUpdate();}}
document.querySelectorAll("[data-tab]").forEach(button=>button.addEventListener("click",()=>{const tab=button.dataset.tab;for(const id of ["overview","settings"])$(id).hidden=tab!==id;document.querySelectorAll("[data-tab]").forEach(b=>b.classList.toggle("active",b===button));}));
for(const [device,id] of [["heater","heater"],["heater2","heater2"],["hot_water","hot-water"]])$(id+"-mode").addEventListener("change",event=>{const mode=event.target.value;mutation(async()=>{toast("Checking the Smartfox mode change.");const result=await api("/api/smartfox/heater",{device,mode,request_id:crypto.randomUUID()});if(result.status!=="confirmed")throw new Error(result.message);toast(result.message);});});
$("station-mode").addEventListener("change",event=>{const mode=event.target.value,active=activeStates.has(latest?.charging?.status),allowStale=!$("stale-consent").hidden&&$("allow-stale").checked;mutation(async()=>{if(mode==="M+"&&!active){const body={target_percent:Number($("target").value),request_id:crypto.randomUUID()};if(allowStale)body.allow_stale=true;$("allow-stale").checked=false;targetEditing=false;const result=await api("/api/charging/start",body);toast(result.message);}else{const result=await api("/api/smartfox/charger-mode",{mode,request_id:crypto.randomUUID()});if(result.status!=="confirmed")throw new Error(result.message);toast(result.message);}});});
$("target").addEventListener("input",()=>{targetEditing=true;try{localStorage.setItem("charging-target",$("target").value);}catch{}updateControls();});
$("target").addEventListener("change",()=>{if(!activeStates.has(latest?.charging?.status)){targetEditing=false;return;}const target=Number($("target").value);mutation(async()=>{try{await api("/api/charging/target",{target_percent:target});toast(`Charge target ${target}% saved.`);}finally{targetEditing=false;}});});
$("refresh").addEventListener("click",()=>mutation(async()=>{const result=await api("/api/refresh",{});render(result.status);toast(result.message);}));
$("quit-app").addEventListener("click",()=>mutation(async()=>{const result=await api("/api/app/quit",{});appStopped=true;clearInterval(refreshTimer);toast(result.message);$("connection-summary").textContent="App stopped";updateControls();}));
$("allow-stale").addEventListener("change",updateControls);
$("force").addEventListener("click",()=>{const allowStale=!$("stale-consent").hidden&&$("allow-stale").checked;mutation(async()=>{const body={target_percent:Number($("target").value),request_id:crypto.randomUUID()};targetEditing=false;if(allowStale)body.allow_stale=true;$("allow-stale").checked=false;toast("Preparing Smartfox. Vehicle start follows only after charging permission is confirmed.");const result=await api("/api/charging/start",body);if(result.message)toast(result.message);});});
$("stop").addEventListener("click",()=>mutation(async()=>{const result=await api("/api/charging/stop",{request_id:crypto.randomUUID()});if(result.message)toast(result.message);},true));
$("config-form").addEventListener("submit",event=>{event.preventDefault();mutation(async()=>{const value={smartfox:{host:$("host").value.trim(),port:Number($("port").value),unit_id:Number($("unit").value),firmware:$("firmware").value,poll_seconds:Number($("sf-poll").value),timeout_seconds:Number($("sf-timeout").value),profile:$("profile").value,word_order:$("word-order").value,interpretation_confirmed:$("interpretation").checked},skoda:{vin:$("vin").value.trim(),poll_seconds:Number($("car-poll").value),stale_seconds:Number($("car-stale").value)}};const result=await api("/api/config",value);await loadConfig();toast(result.message);});});
$("key-form").addEventListener("submit",event=>{event.preventDefault();mutation(async()=>{try{const result=await api("/api/key",{key:$("api-key").value,persist:$("persist").checked});await loadConfig();toast(result.message);}finally{$("api-key").value="";}});});
$("clear-key").addEventListener("click",()=>mutation(async()=>{const result=await api("/api/key/clear",{});await loadConfig();toast(result.message);}));
let diagnosticsDownloading=false;
$("download-diagnostics").addEventListener("click",async event=>{
 event.preventDefault();if(diagnosticsDownloading)return;
 diagnosticsDownloading=true;$("download-diagnostics").setAttribute("aria-busy","true");
 try{
  const report=await api("/api/diagnostics/download");
  const blob=new Blob([JSON.stringify(report,null,2)+"\n"],{type:"application/json;charset=utf-8"});
  const url=URL.createObjectURL(blob),link=document.createElement("a");
  link.href=url;link.download="CitiFoxCharger-Diagnostics.json";document.body.append(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),60000);
 }catch(e){$("diagnostic-summary").textContent=e.message;}
 finally{diagnosticsDownloading=false;$("download-diagnostics").removeAttribute("aria-busy");}
});
$("show-diagnostics").addEventListener("click",async()=>{
 const button=$("show-diagnostics");button.disabled=true;
 try{const report=await api("/api/diagnostics");
  $("diagnostic-json").textContent=JSON.stringify(report,null,2);$("diagnostic-json").hidden=false;
  const causes={missing_soc:"Stopped because vehicle data became too old.",missing_initial_soc:"Stopped because no new vehicle data arrived after start.",phase_wait:"Stopped during charging preparation.",target:"Stopped at the monitored target."};
  $("diagnostic-summary").textContent=causes[report.session?.stop_cause]||"Current cached status. Diagnostics do not query the vehicle.";
 }catch(e){$("diagnostic-summary").textContent=e.message;}finally{button.disabled=false;}
});
async function init(){try{csrf=(await api("/api/session")).csrf;await loadRelease();await loadConfig();await refresh();refreshTimer=setInterval(refresh,10000);await resumeUpdate();}catch(e){error(e.message);}}
function updateHeaderClock(){
 const now=new Date(),locale=CitiFoxI18n.locale(),clock=$("header-clock");
 clock.dateTime=now.toISOString();
 clock.textContent=now.toLocaleDateString(locale,{day:"2-digit",month:"2-digit",year:"numeric"})+" · "+now.toLocaleTimeString(locale,{hour:"2-digit",minute:"2-digit",hour12:false});
 clock.title=now.toLocaleString(locale,{dateStyle:"full",timeStyle:"long"});
}
updateHeaderClock();
setInterval(updateHeaderClock,1000);
document.addEventListener("visibilitychange",()=>{if(!document.hidden)updateHeaderClock();});
window.addEventListener("focus",updateHeaderClock);
init();

window.addEventListener("citifoxlanguagechange",()=>{updateHeaderClock();if(latest)render(latest);});
