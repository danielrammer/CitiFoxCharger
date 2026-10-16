"use strict";
const $ = id => document.getElementById(id);
let csrf="", refreshing=false, mutating=false, latest=null, targetInitialized=false, appStopped=false, refreshTimer=null, consentFor="";
const activeStates=new Set(["starting","monitoring","stop_pending","restore_pending","paused","interrupted"]);
const states={CHARGING:"Lädt",CONNECT_CABLE:"Kabel anschließen",CONSERVING:"Ladeziel erreicht",READY_FOR_CHARGING:"Ladebereit",DISCHARGING:"Entlädt",CHARGING_INTERRUPTED:"Laden unterbrochen"};
const date=value=>{const d=new Date(value);return value&&!Number.isNaN(d.getTime())?d.toLocaleString("de-AT",{dateStyle:"short",timeStyle:"medium"}):"—";};
function age(value){const t=Date.parse(value);if(!Number.isFinite(t))return "Fahrzeugzeit unbekannt";const seconds=Math.max(0,Math.round((Date.now()-t)/1000));return seconds<60?"Fahrzeugdaten vor wenigen Sekunden":seconds<3600?`Fahrzeugdaten vor ${Math.floor(seconds/60)} Minuten`:`Fahrzeugdaten vom ${date(value)}`;}
function error(message){$("global-error").textContent=message;$("global-error").hidden=false;}
function toast(message){$("toast").textContent=message;$("toast").hidden=false;setTimeout(()=>$("toast").hidden=true,6000);}
async function api(path,data){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);try{const response=await fetch(path,{method:data===undefined?"GET":"POST",credentials:"same-origin",cache:"no-store",signal:controller.signal,headers:data===undefined?{}:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:data===undefined?undefined:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw new Error(result.error||"Anfrage fehlgeschlagen.");return result;}catch(e){if(e.name==="AbortError")throw new Error("Antwort ausgeblieben. Bitte den Ladestatus prüfen, bevor du erneut startest.");throw e;}finally{clearTimeout(timer);}}
function updateControls(){
 if(!latest)return;
 if(appStopped){$("force").disabled=true;$("stop").disabled=true;$("target").disabled=true;$("quit-app").disabled=true;$("allow-stale").disabled=true;return;}
 const session=latest.charging,car=latest.skoda,active=activeStates.has(session?.status),target=Number($("target").value),soc=car.soc;
 const staleOption=session?.can_force_with_stale===true&&typeof soc==="number"&&target>soc;
 const token=staleOption?`${car.captured_at}|${soc}|${session.request_id||""}`:"";
 if(token!==consentFor||!staleOption){$("allow-stale").checked=false;consentFor=token;}
 $("stale-consent").hidden=!staleOption;$("allow-stale").disabled=mutating;
 $("stale-consent-text").textContent=`Zuletzt gemeldeten Ladestand verwenden (${soc??"—"} % · ${date(car.captured_at)})`;
 const retry=session?.can_retry_start===true||session?.can_retry_start_with_stale===true;
 $("target-value").textContent=`${target} %`;$("target").disabled=active||mutating;
 $("force").textContent=retry?"↻ Start erneut freigeben":"↯ Manuell laden";
 $("force").disabled=mutating||!(session?.can_force||(staleOption&&$("allow-stale").checked))||typeof soc!=="number"||target<=soc;
 $("stop").disabled=mutating||!latest.controls.stop;
 let hint="";
 if(!car.available)hint=car.code==="not_configured"?"Bitte den Fahrzeugzugang in den Einstellungen einrichten.":"Fahrzeug ist gerade nicht erreichbar.";
 else if(target<=soc)hint="Das eingestellte Ziel ist bereits erreicht.";
 else if(session?.force_reason&&!session.can_force&&!staleOption)hint=session.force_reason;
 else if(car.stale&&!staleOption&&!session?.awaiting_fresh_soc)hint="Warte auf aktuelle Fahrzeugdaten.";
 $("operation-hint").textContent=hint;$("operation-hint").hidden=!hint;
}
function render(data){latest=data;const car=data.skoda,sf=data.smartfox,charger=sf.charger||{},session=data.charging;
 $("soc").textContent=car.soc??"—";$("soc-track").style.width=`${car.soc??0}%`;$("battery-fill").style.width=`${car.soc??0}%`;
 const powerAge=(Date.now()-Date.parse(charger.read_at))/1000,livePower=charger.available&&Number.isFinite(powerAge)&&powerAge>=-5&&powerAge<=30&&charger.telemetry_available!==false&&typeof charger.power_kw==="number";
 const isCharging=livePower?charger.power_kw>0.1:car.state==="CHARGING"&&!car.stale&&car.available;
 $("car-state").textContent=isCharging?"Lädt":livePower&&charger.status_code===2?"Eingesteckt · ladebereit":livePower&&car.state==="CHARGING"?"Keine Ladeleistung":states[car.state]||"Status unbekannt";$("charging-dot").classList.toggle("charging",isCharging);document.querySelector(".charging-card").classList.toggle("is-charging",isCharging);
 $("power-reading").textContent=livePower?charger.power_kw.toLocaleString("de-AT",{minimumFractionDigits:2,maximumFractionDigits:2})+" kW":"— kW";
 const carLabel=car.available?(car.stale?"Daten veraltet":"Verbunden"):car.code==="not_configured"?"Nicht eingerichtet":"Nicht erreichbar";$("car-badge").textContent=carLabel;$("car-badge").className=`badge ${car.available&&!car.stale?"good":"warn"}`;
 $("car-time").textContent=age(car.captured_at);$("car-time").title=`Fahrzeug: ${date(car.captured_at)} · Abgefragt: ${date(car.read_at)}`;
 $("charger-mode").textContent=charger.available?`Smartfox · ${charger.mode||"Modus unbekannt"}`:"Smartfox · nicht erreichbar";
 $("connection-summary").textContent=`Škoda ${car.available?"verbunden":"nicht erreichbar"} · Smartfox ${sf.available?"verbunden":"nicht erreichbar"}`;
 if(session&&(!targetInitialized||activeStates.has(session.status))){let target=session.target_percent;try{if(!targetInitialized&&!activeStates.has(session.status)){const saved=Number(localStorage.getItem("charging-target"));if(saved>=1&&saved<=100)target=saved;}}catch{}$("target").value=target;targetInitialized=true;}
 const active=activeStates.has(session?.status),power=data.monitoring?.power;
 $("monitoring-note").hidden=!active;
 $("monitoring-note").textContent=active?(power?.warning||(!data.monitoring?.running?"Hintergrundüberwachung nicht bestätigt. Bitte App prüfen.":power?.requested&&power.on_ac_power===true?"Überwachung läuft im Hintergrund · Bildschirm darf ausgehen.":"Überwachung läuft. Tablet am Netzteil betreiben und automatisches Einschlafen verhindern.")):"";
 $("quit-app").disabled=active||mutating;
 $("session-status").hidden=!session?.message;$("session-status").textContent=session?.message||"";
 $("car-message").textContent=car.message||"";$("sf-notice").textContent=sf.notice||"";$("sf-control-reason").textContent=charger.control_reason||charger.message||"";
 $("sf-web-meta").textContent=`Lademodus: ${charger.mode||"unbekannt"} · Leistung: ${charger.power_kw??"—"} kW · Wallbox: ${charger.status_text||"unbekannt"} · Vorgabe: ${charger.offered_current_a??"—"} A · Zuletzt gelesen: ${date(charger.read_at||sf.read_at)}`;
 $("api-meta").textContent=`Nächste Abfrage in ${data.next_skoda_seconds} s · Schlüssel gültig bis ${date(car.key_expires_at)} · Restkontingent: ${car.quota_remaining??"unbekannt"}${car.errors?.length?" · "+car.errors.join(", "):""}`;
 $("register-rows").replaceChildren(...(sf.registers||[]).map(row=>{const tr=document.createElement("tr");[String(row.address),row.words?.join(" · ")??"—",row.candidate??"—",row.error||"Gelesen"].forEach(text=>{const td=document.createElement("td");td.textContent=text;tr.append(td);});return tr;}));
 $("events").replaceChildren(...(data.events.length?data.events:[{message:"Noch keine Aktionen."}]).map(event=>{const li=document.createElement("li");li.textContent=(event.at?date(event.at)+" · ":"")+event.message;return li;}));updateControls();}
async function refresh(){if(refreshing||appStopped)return;refreshing=true;$("refresh").setAttribute("aria-busy","true");try{render(await api("/api/status"));$("global-error").hidden=true;}catch(e){$("force").disabled=true;$("stop").disabled=true;error(e.message||"Die lokale App ist nicht erreichbar.");}finally{refreshing=false;$("refresh").removeAttribute("aria-busy");}}
async function loadConfig(){const result=await api("/api/config"),sf=result.config.smartfox,car=result.config.skoda;for(const [id,value]of Object.entries({host:sf.host,port:sf.port,unit:sf.unit_id,firmware:sf.firmware,"sf-poll":sf.poll_seconds,"sf-timeout":sf.timeout_seconds,profile:sf.profile,"word-order":sf.word_order,vin:car.vin,"car-poll":car.poll_seconds,"car-stale":car.stale_seconds}))$(id).value=value;$("interpretation").checked=sf.interpretation_confirmed;$("persist").disabled=!result.key.persistence_available;$("persist").checked=result.key.persisted;$("clear-key").hidden=!result.key.configured;$("key-status").textContent=result.key.warning||(result.key.configured?(result.key.persisted?"Zugang mit Windows verschlüsselt gespeichert.":"Zugang bis zum Beenden der App hinterlegt."):"Kein Schlüssel hinterlegt.");$("sf-meta").textContent=`Pro 2 · ${sf.host} · ${sf.firmware}`;$("device-link").href=`http://${sf.host}/`;}
async function mutation(fn){if(mutating)return;mutating=true;updateControls();let failure=null;try{await fn();}catch(e){failure=e.message;}finally{mutating=false;await refresh();if(failure)error(failure);}}
document.querySelectorAll("[data-tab]").forEach(button=>button.addEventListener("click",()=>{const tab=button.dataset.tab;$("overview").hidden=tab!=="overview";$("settings").hidden=tab!=="settings";document.querySelectorAll("[data-tab]").forEach(b=>b.classList.toggle("active",b===button));}));
$("target").addEventListener("input",()=>{try{localStorage.setItem("charging-target",$("target").value);}catch{}updateControls();});$("refresh").addEventListener("click",()=>mutation(async()=>{const result=await api("/api/refresh",{});render(result.status);toast(result.message);}));
$("quit-app").addEventListener("click",()=>mutation(async()=>{const result=await api("/api/app/quit",{});appStopped=true;clearInterval(refreshTimer);toast(result.message);$("connection-summary").textContent="App beendet";updateControls();}));
$("allow-stale").addEventListener("change",updateControls);
$("force").addEventListener("click",()=>{const allowStale=!$("stale-consent").hidden&&$("allow-stale").checked;mutation(async()=>{const body={target_percent:Number($("target").value),request_id:crypto.randomUUID()};if(allowStale)body.allow_stale=true;$("allow-stale").checked=false;toast("Smartfox wird vorbereitet. Der Fahrzeugstart folgt erst nach bestätigter Ladefreigabe.");const result=await api("/api/charging/start",body);if(result.message)toast(result.message);});});
$("stop").addEventListener("click",()=>mutation(async()=>{const result=await api("/api/charging/stop",{request_id:crypto.randomUUID()});if(result.message)toast(result.message);}));
$("config-form").addEventListener("submit",event=>{event.preventDefault();mutation(async()=>{const value={smartfox:{host:$("host").value.trim(),port:Number($("port").value),unit_id:Number($("unit").value),firmware:$("firmware").value,poll_seconds:Number($("sf-poll").value),timeout_seconds:Number($("sf-timeout").value),profile:$("profile").value,word_order:$("word-order").value,interpretation_confirmed:$("interpretation").checked},skoda:{vin:$("vin").value.trim(),poll_seconds:Number($("car-poll").value),stale_seconds:Number($("car-stale").value)}};const result=await api("/api/config",value);await loadConfig();toast(result.message);});});
$("key-form").addEventListener("submit",event=>{event.preventDefault();mutation(async()=>{try{const result=await api("/api/key",{key:$("api-key").value,persist:$("persist").checked});await loadConfig();toast(result.message);}finally{$("api-key").value="";}});});
$("clear-key").addEventListener("click",()=>mutation(async()=>{const result=await api("/api/key/clear",{});await loadConfig();toast(result.message);}));
async function init(){try{csrf=(await api("/api/session")).csrf;await loadConfig();await refresh();refreshTimer=setInterval(refresh,10000);}catch(e){error(e.message);}}
init();
