"use strict";
const $ = id => document.getElementById(id);
let csrf="", refreshing=false, mutating=false, latest=null, targetInitialized=false, appStopped=false, refreshTimer=null, targetEditing=false, localAdmin=true;
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
 renderLimitTest();
 $("reload-app").disabled=mutating||appUpdating;
 if(appStopped){for(const id of ["force","stop","target","quit-app","heater-mode","heater2-mode","hot-water-mode","station-mode"])$(id).disabled=true;return;}
 const session=latest.charging,car=latest.skoda,active=activeStates.has(session?.status)||latest.limit_trial?.active===true,target=Number($("target").value),soc=car.soc;
 const staleOption=session?.can_start_manual_with_stale===true&&typeof soc==="number"&&target>soc;
 const retry=session?.can_retry_start===true||session?.can_retry_start_with_stale===true;
 $("target-value").textContent=`${target} %`;$("target").disabled=mutating||(active&&!session?.can_adjust_target);
 const release=session?.can_release_native_guard===true;
 $("force").textContent=release?"Release A+ normal mode":retry?"↻ Retry start":"↯ Force charge";
 $("force").disabled=mutating||appUpdating||(!release&&(!(session?.can_start_manual||staleOption)||typeof soc!=="number"||target<=soc));
 $("release-limit-guard").hidden=!release;$("release-limit-guard").disabled=mutating||appUpdating;
 $("stop").disabled=mutating||!(session?.can_stop??(active||latest.controls.stop));
 let hint="";
 if(!car.available)hint=car.code==="not_configured"?"Configure vehicle access in Settings.":"The vehicle is currently unreachable.";
 else if(target<=soc)hint="The selected target is already reached.";
 else if((session?.manual_start_reason||session?.force_reason)&&!session.can_start_manual&&!staleOption)hint=session.manual_start_reason||session.force_reason;
 else if(car.stale&&!staleOption&&!session?.awaiting_fresh_soc)hint="Waiting for current vehicle data.";
 if((active||session?.status==="error")&&session?.message)hint="";
 $("operation-hint").textContent=hint;$("operation-hint").hidden=!hint;
 updateHeaterControls();updateEta();
 const updating=appUpdating||latest.app_update_pending===true;
 $("update-app").disabled=mutating||updating||!updateAllowed||!session?.can_shutdown||!updateKeyReady;
 $("update-hint").textContent=!updateKeyReady?"Save the API key with Windows encryption before updating.":!session?.can_shutdown?"Stop charging and wait for confirmation before updating.":"Update from GitHub, restart the backend and reload this page.";
 if(updating)for(const id of ["force","target","quit-app","heater-mode","heater2-mode","hot-water-mode","station-mode"])$(id).disabled=true;
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
 const guarded=activeStates.has(latest?.charging?.status)&&latest.charging.stop_guard===true;
 $("station-mode").disabled=mutating||!live||!charger.control_available||guarded;
 $("station-mode").title=guarded?"Charger kept Off until the vehicle confirms the stop.":"Change mode";
 const active=activeStates.has(latest?.charging?.status),manual=active?!latest.charging.stop_requested&&latest.controls.start&&latest.controls.stop:Number($("target").value)>latest.skoda.soc&&(latest?.charging?.can_start_manual||latest?.charging?.can_start_manual_with_stale);
 $("station-mode").querySelector('option[value="M+"]').disabled=!manual;
 $("station-mode").querySelector('option[value="A+"]').disabled=(active&&latest.charging.stop_guard===true)||latest.limit_test?.active===true;
}
function renderLimitTest(){
 const test=latest.limit_test||{},trial=latest.limit_trial||{},busy=mutating||appUpdating||appStopped,de=CitiFoxI18n.language()==="de";
 $("send-limit-test").disabled=busy||!test.can_set;
 $("limit-test-target").disabled=busy||trial.active;
 $("restore-limit-test").disabled=busy||!test.can_restore;
 $("end-limit-test").disabled=busy||!test.active||trial.active;
 $("start-limit-trial").disabled=busy||!trial.can_start;
 $("stop-limit-trial").disabled=busy||!trial.active;
 $("limit-trial-reason").textContent=trial.active?"":trial.reason||"";
 $("limit-trial-result").textContent=trialDescription(trial,de);
 $("limit-test-reason").textContent=test.reason||(test.active&&!trial.active?"Test isolation active. Only the separate supervised trial can send Start.":"");
 const rows=(test.attempts||[]).slice(-3).map(item=>{
  const outcome=item.outcome==="accepted"?(de?"API angenommen":"API accepted"):item.outcome==="rejected"?(de?"abgelehnt":"rejected"):(de?"Ausgang unbekannt":"outcome unknown");
  const verification=item.verification==="confirmed"?(de?"Fahrzeuggrenze bestätigt":"vehicle limit confirmed"):item.verification==="not_returned"?(de?"Fahrzeug liefert keine Ladegrenze; nicht bestätigt":"vehicle returns no limit; unconfirmed"):item.verification==="different"?(de?`Fahrzeug meldet ${item.reported_limit} %; Ziel nicht bestätigt`:`vehicle reports ${item.reported_limit}%; target unconfirmed`):(de?"keine neuere Bestätigung":"no newer confirmation");
  return `${item.target_percent} % · ${outcome}${item.http_status?` · HTTP ${item.http_status}`:""} · ${verification}${item.code?` · ${item.problem_type||item.code}`:""}`;
 });
 $("limit-test-result").textContent=rows.length?rows.join("\n"):(de?"Noch kein Fahrzeug-Grenzwerttest ausgeführt.":"No vehicle-limit test has been run.");
}
function trialDescription(trial,de){
 if(!trial.phase)return "";
 const phases={waiting_ready:de?"Warte auf M+ und Ladebereitschaft / Phasenumschaltung":"Waiting for M+ and readiness / phase switching",observing:de?"Test läuft; kein App-Stopp am Ziel":"Trial running; no app stop at target",waiting_resume:trial.phase_ended_at?(de?"Phasenumschaltung beendet; warte auf erneute Ladeleistung":"Phase switching ended; waiting for charging power to resume"):(de?"Phasenumschaltung nach Start; warte, kein erneuter Startbefehl":"Phase switching after Start; waiting without another Start request"),stopping:de?"Test wird gestoppt; Aus wird geprüft":"Stopping trial; checking Off",finished:de?"Test beendet; Aus und 0 kW bestätigt":"Trial ended; Off and zero power confirmed"};
 const causes={manual:de?"manueller Stopp":"manual stop",external_power_stop:de?"Leistung ohne App-Stopp abgefallen; Ursache/Zielerreichung unbestätigt. Danach durch Aus abgesichert":"Power dropped without an app stop; cause/target unconfirmed. Subsequently secured Off",time_guard:de?"App-Abbruch nach 10 Minuten; kein Nachweis für Fahrzeug-Stopp":"App fallback after 10 minutes; no proof of vehicle stop",overshoot_guard:de?"App-Abbruch bei Ziel +2 Prozentpunkten; kein Fahrzeug-Stopp am Ziel":"App fallback at target +2 percentage points; no vehicle stop at target",no_charging_power:de?"Keine Ladeleistung binnen 3 Minuten bestätigt":"No charging power confirmed within 3 minutes",backend_restart:de?"App-Abbruch nach Backend-Neustart":"App fallback after backend restart"};
 const details=[`${trial.target_percent} % · ${phases[trial.phase]||trial.phase}`];
 const outcome=de?({accepted:"API angenommen",unknown:"Ausgang unbekannt",rejected:"abgelehnt"}[trial.start_outcome]||trial.start_outcome):trial.start_outcome;
 if(trial.start_outcome)details.push(`${de?"Startanfrage":"Start request"}: ${outcome}${trial.start_http_status?` · HTTP ${trial.start_http_status}`:""}`);
 if(trial.power_confirmed_at)details.push(`${de?"Ladeleistung bestätigt":"Charging power confirmed"}: ${date(trial.power_confirmed_at)}`);
 if(trial.power_resumed_at)details.push(`${de?"Ladeleistung nach Umschaltung bestätigt":"Charging power resumed after switching"}: ${date(trial.power_resumed_at)}`);
 if(trial.stop_cause)details.push(trial.stop_cause==="phase_resume_failed"?(de?"App-Abbruch: 3 Minuten nach Phasenumschaltung keine erneute Ladeleistung; kein Fahrzeug-Stopp am Ziel":"App fallback: no resumed power within 3 minutes after phase switching; not a vehicle stop at target"):(causes[trial.stop_cause]||(de?`App-Abbruch: ${trial.stop_cause}`:`App fallback: ${trial.stop_cause}`)));
 return details.join("\n");
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
 const limit=session?.vehicle_limit||{},reportedLimit=car.charging_settings?.target_percent;
 $("vehicle-limit-info").textContent=limit.phase?`Vehicle limit: ${limit.phase} · Target ${limit.target_percent} % · Restore ${limit.restore_percent} %`:`Reported vehicle limit: ${reportedLimit??"unknown"} · Default restore limit: 80 %`;
 $("restore-vehicle-limit").hidden=limit.phase!=="attention";$("restore-vehicle-limit").disabled=mutating||appUpdating;$("confirm-restored-limit").hidden=limit.phase!=="attention";$("confirm-restored-limit").disabled=mutating||appUpdating;
 $("soc").textContent=car.soc??"—";$("soc-track").style.width=`${car.soc??0}%`;$("battery-fill").style.width=`${car.soc??0}%`;
 const powerAge=(Date.now()-Date.parse(charger.read_at))/1000,livePower=charger.available&&Number.isFinite(powerAge)&&powerAge>=-5&&powerAge<=30&&charger.telemetry_available!==false&&typeof charger.power_kw==="number";
 const switching=livePhaseSwitch(charger),isCharging=!switching&&(livePower?charger.power_kw>0.1:car.state==="CHARGING"&&!car.stale&&car.available);
 $("car-state").textContent=switching?"Phase switching":isCharging?"Charging":livePower&&charger.mode==="OFF"?"Charger Off":livePower&&charger.status_code===2?"Plugged in · ready":livePower&&(car.state==="CHARGING"||charger.status_code===3)?"No charging power":states[car.state]||"Status unknown";$("charging-dot").classList.toggle("charging",isCharging);$("charging-dot").classList.toggle("switching",switching);document.querySelector(".charging-card").classList.toggle("is-charging",isCharging);
 $("power-reading").textContent=livePower?charger.power_kw.toLocaleString(CitiFoxI18n.locale(),{minimumFractionDigits:2,maximumFractionDigits:2})+" kW":"— kW";
 const quotaBlocked=["quota_reserved","rate_limit"].includes(car.code);
 const carLabel=quotaBlocked?"Škoda request limit":car.available?(car.stale?"Stale · "+ageBrief(car.captured_at):"Connected"):car.code==="not_configured"?"Not configured":"Unreachable";$("car-badge").textContent=carLabel;$("car-badge").className=`badge ${car.available&&!car.stale&&!quotaBlocked?"good":"warn"}`;
 $("car-time").textContent=`Last vehicle report: ${date(car.captured_at)}`;$("car-read").textContent=`Last successful query: ${date(car.read_at)}`;
 $("charger-mode").textContent=charger.available?(limit.phase==="verified"?`${charger.mode||"Mode unknown"} · Vehicle target ${limit.target_percent}% ✓`:`Smartfox · ${charger.mode||"Mode unknown"}`):"Smartfox · unreachable";
 $("connection-summary").textContent=`Škoda ${car.available?"connected":"unreachable"} · Smartfox ${sf.available?"connected":"unreachable"}`;
 if(data.limit_trial?.active){$("target").value=data.limit_trial.target_percent;targetInitialized=true;}
 else if(session&&!targetEditing&&(!targetInitialized||activeStates.has(session.status))){let target=session.target_percent;try{if(!targetInitialized&&!activeStates.has(session.status)){const saved=Number(localStorage.getItem("charging-target"));if(Number.isInteger(saved)&&saved>=1&&saved<=100)target=saved;}}catch{}$("target").value=target;targetInitialized=true;}
 const active=activeStates.has(session?.status)||data.limit_trial?.active===true,power=data.monitoring?.power;
 $("monitoring-note").hidden=!active;
 $("monitoring-note").textContent=active?(power?.warning||(!data.monitoring?.running?"Background monitoring is unconfirmed. Please check the app.":power?.requested&&power.on_ac_power===true?"Monitoring in background · screen may turn off.":"Monitoring active. Keep the tablet plugged in and prevent automatic sleep.")):"";
 $("quit-app").disabled=!localAdmin||!session?.can_shutdown||mutating;
 $("session-status").classList.toggle("session-quiet",session?.status==="monitoring"&&isCharging&&!session?.awaiting_fresh_soc&&!session?.stop_requested);
 $("normal-mode-note").textContent=data.limit_trial?.active?"Supervised trial · vehicle limit unconfirmed.":session?.user_charger_mode==="OFF"?(charger.available&&charger.mode==="OFF"?"Charger explicitly switched Off.":"Off selected · change is unconfirmed."):session?.status==="closed"?"Previous monitoring is closed. Select a new target or A+.":session?.stop_guard?"Charger kept Off until the vehicle confirms the stop.":activeStates.has(session?.status)&&session.target_control==="app"?"Target monitored by the app · delayed vehicle reports can delay the stop.":"Normal mode A+ · schedules are preserved.";
 const trialMessage=data.limit_test?.active&&data.limit_trial?.phase?trialDescription(data.limit_trial,CitiFoxI18n.language()==="de"):"";
 const showPhase=switching&&!session?.stop_requested;$("session-status").hidden=!showPhase&&!session?.message&&!trialMessage;$("session-status").textContent=trialMessage||(showPhase?phaseMessage(charger):session?.message||"");$("session-status").classList.toggle("phase-wait",showPhase);$("session-status").classList.toggle("safety-stopped",session?.status==="error"&&["missing_soc","missing_initial_soc","phase_wait","request_quota","vehicle_limit_start","vehicle_limit_lost"].includes(session?.stop_cause));
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
$("station-mode").addEventListener("change",event=>{const mode=event.target.value,active=activeStates.has(latest?.charging?.status);mutation(async()=>{if(mode==="M+"&&!active){const body={target_percent:Number($("target").value),request_id:crypto.randomUUID(),allow_stale:true};targetEditing=false;const result=await api("/api/charging/start",body);toast(result.message);}else{const result=await api("/api/smartfox/charger-mode",{mode,request_id:crypto.randomUUID()});if(result.status!=="confirmed")throw new Error(result.message);toast(result.message);}});});
$("target").addEventListener("input",()=>{targetEditing=true;try{localStorage.setItem("charging-target",$("target").value);}catch{}updateControls();});
$("target").addEventListener("change",()=>{if(!activeStates.has(latest?.charging?.status)){targetEditing=false;return;}const target=Number($("target").value);mutation(async()=>{try{await api("/api/charging/target",{target_percent:target});toast(`Charge target ${target}% saved.`);}finally{targetEditing=false;}});});
$("refresh").addEventListener("click",()=>mutation(async()=>{const result=await api("/api/refresh",{});render(result.status);toast(result.message);}));
$("quit-app").addEventListener("click",()=>mutation(async()=>{const result=await api("/api/app/quit",{});appStopped=true;clearInterval(refreshTimer);toast(result.message);$("connection-summary").textContent="App stopped";updateControls();}));
 $("force").addEventListener("click",()=>{if(latest?.charging?.can_release_native_guard){releaseLimitGuard();return;}mutation(async()=>{const body={target_percent:Number($("target").value),request_id:crypto.randomUUID(),allow_stale:true};targetEditing=false;toast("Preparing Smartfox. Vehicle start follows only after charging permission is confirmed.");const result=await api("/api/charging/start",body);if(result.message)toast(result.message);});});
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
  const causes={vehicle_limit_start:"Vehicle start failed after limit verification.",vehicle_limit_lost:"The vehicle charging limit changed unexpectedly.",request_quota:"Stopped because Škoda request limits prevent charge-level monitoring.",missing_soc:"Stopped because vehicle data became too old.",missing_initial_soc:"Stopped because no new vehicle data arrived after start.",phase_wait:"Stopped during charging preparation.",target:"Stopped at the monitored target."};
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

$("restore-vehicle-limit").addEventListener("click",()=>mutation(async()=>{const result=await api("/api/charging/restore-limit",{request_id:crypto.randomUUID()});if(result.message)toast(result.message);}));

function releaseLimitGuard(){if(latest?.charging?.can_release_native_guard&&!mutating&&!appUpdating)$("release-guard-dialog").showModal();}
$("cancel-release-guard").addEventListener("click",()=>$("release-guard-dialog").close());
$("confirm-release-guard").addEventListener("click",()=>{$("release-guard-dialog").close();if(!latest?.charging?.can_release_native_guard)return;mutation(async()=>{const result=await api("/api/charging/release-limit-guard",{request_id:crypto.randomUUID(),accept_unconfirmed_limit:true});if(result.message)toast(result.message);});});
$("release-limit-guard").addEventListener("click",releaseLimitGuard);

$("send-limit-test").addEventListener("click",()=>mutation(async()=>{
 await api("/api/charging/limit-test/set",{target_percent:Number($("limit-test-target").value),request_id:crypto.randomUUID()});
 toast("Single vehicle-limit request completed. Check the result in Settings. No charging Start was sent.");
}));
$("restore-limit-test").addEventListener("click",()=>mutation(async()=>{
 await api("/api/charging/limit-test/set",{target_percent:80,request_id:crypto.randomUUID()});
 toast("Normal 80% limit requested. API acceptance alone does not confirm restoration.");
}));
let trialTarget=null;
$("start-limit-trial").addEventListener("click",()=>{
 const attempts=latest?.limit_test?.attempts||[];trialTarget=attempts.at(-1)?.target_percent;
 if(!Number.isInteger(trialTarget))return;
 const de=CitiFoxI18n.language()==="de";
 $("limit-trial-confirmation").textContent=de?`Testziel ${trialTarget} %; zuletzt gemeldet ${latest.skoda.soc} %. Die Ladegrenze ist unbestätigt, auch nach HTTP 500. Dieser Versuch schaltet SMARTFOX auf M+, wartet die Phasenumschaltung ab und sendet genau einen Start. Der Grenzwert wird nicht erneut gesendet. Die App stoppt NICHT bei ${trialTarget} %. Abbruch spätestens 10 Minuten nach dem Startbefehl oder bei einem neu gemeldeten Ladestand ab ${trialTarget+2} %. Diese Abbrüche sind kein Fahrzeug-Stopp am Ziel. Beobachte den Versuch und stoppe bei Bedarf. Ein Leistungsabfall beweist allein keine Zielerreichung. Danach bleibt SMARTFOX aus; 80 % werden nicht automatisch wiederhergestellt.`:`Test target ${trialTarget}%; last reported ${latest.skoda.soc}%. The limit is unconfirmed, including after HTTP 500. This trial selects M+, waits for phase switching and sends exactly one Start without resending the limit. The app does NOT stop at ${trialTarget}%. Fallback stop no later than 10 minutes after Start or on a new SOC report at ${trialTarget+2}% or above. These fallbacks are not a vehicle stop at target. Observe the trial and stop if needed. A power drop alone does not prove target completion. Afterwards SMARTFOX stays Off; 80% is not restored automatically.`;
 $("start-limit-trial-dialog").showModal();
});
$("cancel-limit-trial").addEventListener("click",()=>$("start-limit-trial-dialog").close());
$("confirm-limit-trial").addEventListener("click",()=>{
 $("start-limit-trial-dialog").close();const target=trialTarget;
 mutation(async()=>{await api("/api/charging/limit-test/start-trial",{target_percent:target,request_id:crypto.randomUUID(),accept_unconfirmed_limit:true});});
});
$("stop-limit-trial").addEventListener("click",()=>mutation(async()=>{await api("/api/charging/stop",{request_id:crypto.randomUUID()});}));
$("end-limit-test").addEventListener("click",()=>$("end-limit-test-dialog").showModal());
$("cancel-end-limit-test").addEventListener("click",()=>$("end-limit-test-dialog").close());
$("confirm-end-limit-test").addEventListener("click",()=>{
 $("end-limit-test-dialog").close();mutation(async()=>{
  await api("/api/charging/limit-test/end",{acknowledge_unconfirmed_limit:true});
  toast("Test isolation ended. The charger remains explicitly Off; no charging Start was sent.");
 });
});

$("confirm-restored-limit").addEventListener("click",()=>{const target=latest?.charging?.vehicle_limit?.restore_percent;if(!Number.isInteger(target))return;const message=CitiFoxI18n.language()==="de"?`Prüfe direkt in MyŠkoda oder im Auto: Die tatsächliche Fahrzeug-Ladegrenze ist wieder ${target} %. Eine 80-%-Schaltzeit allein reicht nicht. Nur bestätigen, wenn du diesen Wert selbst geprüft hast. Danach wird der Normalbetrieb freigegeben; es wird kein Ladestart gesendet.`:`Check directly in MyŠkoda or the vehicle: the actual vehicle charging limit is restored to ${target} %. A scheduled 80% profile alone is insufficient. Confirm only after checking this value yourself. This releases normal operation without sending a charging start.`;if(!window.confirm(message))return;mutation(async()=>{const result=await api("/api/charging/confirm-restored-limit",{request_id:crypto.randomUUID(),target_percent:target,confirmed_at_vehicle:true});if(result.message)toast(result.message);});});
