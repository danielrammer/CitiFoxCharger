# Verifikation der Schnittstellen

Stand: **16.10.2026**, Version 1.5.0. Primärquellen und die tatsächlich installierte Geräteoberfläche wurden geprüft. Die ältere Chat-Unterhaltung wurde nicht als Schnittstellennachweis übernommen.

## SMARTFOX Pro 2 / Charger Pro 1

Vom Benutzer bestätigte Hardware: Pro 2, Charger Pro (1), Firmware **EM3 00.01.16.08**. Firmware zusätzlich direkt über die lokale Anzeige bestätigt.

Primärquellen:

- [Hersteller-Downloadbereich](https://smartfox.at/downloads/anleitungen/)
- [Offizielle Registerliste v22e / 00.01.03.10](https://smartfox.at/wp-content/uploads/2022/12/Modbus-Register-SMARTFOX-Pro-SMARTFOX-Pro-2-v22e-00.01.03.10.xlsx)
- [Offizielles Charger-Handbuch](https://smartfox.at/wp-content/uploads/2022/12/Anleitung-Installations-Bedienungsanleitung-SMARTFOX-Pro-Charger.pdf), gedruckte Seiten 55 und 60
- [Herstellerinformationen zu EM2 / EM3](https://smartfox.de/neues-software-update/)

SHA-256 der geprüften Registerliste: `ADCE7FC601E46D72E372FC1DE8E8126780328A787B27A125368663908BBB0058`.

TCP-Port 502, Unit ID 1 und FC03 werden für das Lesen verwendet. Die Herstellerliste verwendet Offset −1. Die vom Benutzer gewünschten direkten PDU-Adressen 40999 / 41015 / 42111 bleiben Rohwerte: 40999 ist in der älteren Liste Netzenergie, 41015 Tagesenergie, 42111 ist dort nicht dokumentiert. Diese Angaben bestätigen keine Ladeleistungs- oder Modusinterpretation für die vorhandene Firmware.

Die ältere Liste ordnet Charger 1 die PDU-Adressen 41599 (4 Wörter, Gesamtenergie), 41605 (2 Wörter, Leistung) und 41607 (1 Wort, Modus) zu. Bei der neueren Firmware wurde für 41607 der Wert 2 beobachtet, während die ältere Liste nur 0/1 beschreibt. Das optionale alte Profil verlangt daher eine ausdrückliche Bestätigung der Interpretation. **Modbus-Schreiben ist nicht implementiert.**

### Lokale Hersteller-Websteuerung

Die ausgelieferte Geräteoberfläche unter 192.168.0.81 liest `/values.xml`. Verifiziert wurden `version`, `hidCcMode1` und `cc1Power` mit expliziter kW-Einheit. Das tatsächlich ausgelieferte Hersteller-HTML/JavaScript ordnet die Modustasten zu:

| Modus | Code |
|---|---:|
| A | 0 |
| M | 1 |
| A+ | 2 |
| OFF | 3 |
| M+ | 4 |

Die Herstelleroberfläche verwendet für Charger 1 `/setswcc.cgi?num=0&mode=<code>&value=100`. Dafür wurde keine öffentliche Hersteller-API-Spezifikation gefunden. Die Existenz des Pfads ist keine Freigabe von Modbus-Schreibregistern.

Der Benutzer hat am 16.10.2026 ausdrücklich geantwortet: **„Ja, lokale Websteuerung freigeben“**. Deshalb aktiviert diese auf sein Gerät zugeschnittene Anwendung den eng begrenzten Webadapter. Die Klasse selbst bleibt ohne explizite Aktivierung gesperrt. Erlaubt sind Aus, A+ und M+ an Charger 1 und die nachfolgend beschriebenen angeforderten Wärmeausgänge, nur bei exakt passender Firmware und privater IPv4-Adresse, ohne Umleitungen oder Systemproxy. XML-Größe und Schema werden geprüft. Nach einer Änderung muss der zurückgelesene Modus passen; andernfalls startet die App das Auto nicht.

**Realer Nachweis:** A+ → M+ → A+ mit Rücklesebestätigung erfolgreich; Endzustand A+. Kein neuer Fahrzeugbefehl während dieses Tests.

Laut Handbuch bedeutet A+ PV-Überschuss mit zusätzlicher Smartfox-Wochenzeitschaltuhr. Fahrzeug-Schaltzeiten sind eine eigene Ebene. M+ hat native Energie-/Zeitvorgaben und einen Fallback; diese bleiben wirksam und können M+ vor einem SOC-Ziel beenden. Die App ändert weder diese Vorgaben noch Fahrzeug-Ladeprofile.

### Ladebereitschaft vor dem Fahrzeugstart

Zusätzlich wurden am ausgelieferten Hersteller-JavaScript die Felder hidCcSt1, hidCc1Info, hidCcConnTimeout1 und hidCcControlCurrent geprüft. Status 2 bedeutet ladebereit, 3 lädt; Info 2/8/10 bezeichnet aktive manuelle Freigaben, Info 14 verlangt Authentifizierung. Die Herstelleroberfläche wertet einen Verbindungszähler bis 3 als nicht verbunden. Der Adapter wartet nach M+ mindestens drei Sekunden auf frische Ladebereitschaft mit mindestens 6 A Vorgabe. Diese Herstellerwerte sind keine Interpretation von Modbus 42111. Sie belegen eine Wallboxfreigabe, nicht bereits die Stromaufnahme des Fahrzeugs.

## Škoda Public API

Geprüfte Primärquellen:

- [Offizielle Dokumentation](https://public.api.connect.skoda-auto.cz/docs)
- [OpenAPI-Vertrag 1.1.0](https://public.api.connect.skoda-auto.cz/docs/myskoda-public-api.yaml)
- [Changelog](https://public.api.connect.skoda-auto.cz/docs/changelog)
- [API-Schlüsselverwaltung](https://go.skoda.eu/api-keys)
- [Citigo e iV: Remote Battery Charging](https://www.skoda-auto.com/models/layers/layers/citigoe-iv-connectivity)
- [Herstellerübersicht 2G/3G-Abschaltung](https://www.skoda-auto.com/_doc/1b32a97a-fce0-4d19-a832-0c362b5f7c78)

SHA-256 des geprüften OpenAPI-Vertrags: `FB0514BF3E289E4BFB634F13DD5B7DFD24EB06CBAB829CCF6A6FBB831ECF4CFC`.

| Zweck | Verifizierter Vertrag |
|---|---|
| Zugang | X-API-Key; vom Benutzer lokal hinterlegt |
| Status und Fähigkeiten | GET /api/v1/vehicles/{vin}?include=charging,operations |
| SOC | vehicle.charging.status.battery.stateOfChargeInPercent |
| Fahrzeugzeit | vehicle.charging.carCapturedTimestamp |
| Unterstützte Aktionen | vehicle.operations[].name, insbesondere startCharging / stopCharging |
| Start | POST /api/v1/vehicles/{vin}/charging/start, leere Nutzlast, erwartet 202 |
| Stopp | POST /api/v1/vehicles/{vin}/charging/stop, leere Nutzlast, erwartet 202 |

Die Verbindung nutzt ausschließlich die feste offizielle HTTPS-Domain mit Zertifikatsprüfung, ohne Umleitungen oder Systemproxy. Kein eingebettetes Passwort, kein Kontologin, keine S-PIN. Speicherung nur im RAM oder optional benutzergebunden mit Windows-DPAPI. Schlüssel werden nicht an das Frontend zurückgegeben oder protokolliert.

### Citigo-Kompatibilität

Eine allgemeine Modellbeschreibung ersetzt keine Prüfung des konkreten API-Zugangs. Der konkrete Citigo des Benutzers lieferte inzwischen SOC und beide Fähigkeiten. Einen von ihm ausgelösten Start hat er als reale Ladung bestätigt. Damit sind SOC und Start für diesen Zugang praktisch belegt. Der neue kombinierte M+/Start/Zielstopp-Ablauf bleibt bis zu einem vollständigen realen Durchlauf nur in Teilen am Gerät verifiziert. Mobilfunkabschaltungen und Fahrzeugausstattung können die Verfügbarkeit beeinflussen.

### Frische und Kontingent

Die geprüfte Dokumentation nennt 20 Anfragen pro Stunde und FIN; maßgeblich sind die RateLimit-Header und Serverantworten. Status und Befehle teilen sich das Kontingent. Der Adapter berücksichtigt Reset-Zeitpunkt und Retry-After. Die Abfrageplanung reserviert zwei Aufrufe für Befehle, verteilt übrige Aufrufe während des Ladens und fragt im Leerlauf seltener ab. Die manuelle Neuabfrage hat mindestens 60 Sekunden Abstand und umgeht weder Serverpause noch Befehlsreserve.

Ein neuer Cloud-Abruf ist keine neue Fahrzeugmessung. Im geprüften Vertrag wurde **kein zusätzlicher Wake-up- oder Force-Refresh-Endpunkt** gefunden. Die App behauptet daher keine erzwungene Aktualisierung im Auto. Die frische lokale Smartfox-Leistung zeigt eine aktuelle Ladung an, ersetzt aber keinen gemessenen SOC.

Die reguläre Startsperre bei älterem SOC ist eine App-Regel. Seit 1.3.0 kann ausschließlich der kombinierte manuelle Start durch `allow_stale: true` ausdrücklich freigegeben werden. Dafür gelten maximal 24 Stunden Fahrzeugalter, höchstens fünf Minuten seit erfolgreicher API-Abfrage mit Start-/Stopp-Fähigkeiten und höchstens 30 Sekunden alte lokale Ladebereitschaft ohne Ladeleistung. Der Standard bleibt gesperrt. Die generische Fahrzeug-Befehlsroute akzeptiert diese Ausnahme nicht. SOC und Zeitstempel werden nicht als frisch ausgegeben.

Die Sitzung speichert eine Fünf-Minuten-Frist für einen neuen SOC nach dem Start. Ohne passende neue Meldung wird einmal ein Stopp angefordert und unabhängig vom Cloud-Ergebnis A+ versucht; weitere Stopps erfordern neue CHARGING-Telemetrie. Neustart erhält die Frist und wiederholt keinen Start. A+ ist kein garantierter Strom-Aus-Zustand. Diese Prüfung ist keine neue Škoda-Funktion und erzwingt keine Übertragung durch das Auto.

HTTP 202 bedeutet Annahme eines Befehls, keine Zustandsbestätigung. Erst eine neuere passende Fahrzeugmeldung bestätigt den Ausgang. Start wird nie automatisch wiederholt; unsichere Stopps werden nur nach neuerer CHARGING-Telemetrie und begrenzt erneut versucht. Eine neuere gestoppte Meldung kann den Stopp ohne erneuten Befehl bestätigen.

## Weitere lokale Funktionen

### Lokale Energieanzeige und Zieländerung

Die gelesene Firmware-Oberfläche berechnet den Gesamtverbrauch aus `hidProduction` (kW) + `hidPower` (W, /1000) − `batteryPower` (kW). Positive Netzleistung ist Bezug, negative Einspeisung. Verifiziert sind außerdem `analogOutPower`, `analogOutPercent`, `analogOutTemp`, Sensorzuordnung `hidAoutTemperatureSensors` / `hidR1TempSensor` / `hidR2TempSensor`, Aktivierungen, `hidAoutStatus` sowie `hidR1Status`/`hidR2Status` und deren `Info`-Codes. Sensor 0 ist unzugeordnet, 850 °C ein fehlender Sensorwert. Die App übernimmt nur endliche Werte mit passender Einheit und passendem Bereich; Relaisleistung wird nicht geschätzt. Diese Werte kommen aus dem bereits vorhandenen `/values.xml`-Abruf.

`POST /api/charging/target` ändert nur das lokale Sitzungsziel. Ein dadurch erreichter Grenzwert wird über die bestehende Stopp-Überwachung geprüft. Es wird weder erneut gestartet noch die native Škoda-Ladegrenze ungeprüft beschrieben.

### Wärmeausgänge und begrenzte Wallbox-Auswahl

Die aktuellen Herstellerfunktionen `setswaout` und `setswrel` wurden direkt im ausgelieferten HTML/JavaScript geprüft. Analog: `hidAoutMode` 0=Aus, 1=Manuell, 2=Auto; bestehender `hidAoutPercentage` bleibt erhalten. Befehle: `/setswaout.cgi?mode=0`, `?mode=2` bzw. `?mode=1&power=<bestehender Prozentwert>`. Fehlender manueller Sollwert sperrt Manuell.

Relais: `hidR1Mode`/`hidR2Mode` x=Aus, m=Manuell, 0=Auto. `/setswrel.cgi?rel=N&state=0` für Auto. Manuell/Aus: zuerst `?rel=N&state=3&duration=0` zum Beenden des manuellen Timers, danach `?rel=N&state=1` bzw. `state=2`. N ist fest 1 für Heizstab 2 und 2 für Warmwasser. Die Zuordnung wird anhand Aktivierung, Name und Typ geprüft: Heizstab am Analogausgang, Heizstab 2/Typ 0 an Relais 1, Warmwasser/Typ 2 an Relais 2. Bestätigung nur durch zurückgelesenen Modus; Relaisstatus und Modus bleiben getrennte Werte. Kein Temperatur-/Timer-Konfigurationseditor.

Wallbox-Auswahl begrenzt auf OFF=3, A+=2 und M+=4. M+ ohne aktive Sitzung erfolgt über den überwachten kombinierten Start; die reine Modusroute verlangt für M+ eine aktive Sitzung und aktuellen Start-/Stopp-Zugriff. Aus wird lokal als ausdrücklicher Wunsch gespeichert und verhindert die normale A+-Rückstellung. Alle Änderungen verlangen lokale Sitzung, passende Origin, CSRF und eine eindeutige Befehlskennung; unbestätigte Ergebnisse werden mit gleicher Kennung nicht erneut geschaltet. Die Modbus-Schreibroute bleibt gesperrt. Neue Wärme-/Aus-Schaltwege sind simuliert, nicht an den realen Verbrauchern geprüft.

### Sichtbare Frische und Restzeitmodell

Das Frontend zeigt den unveränderten `carCapturedTimestamp` als Fahrzeugmeldung und den erfolgreichen HTTP-Abruf als eigene Zeit. API-Fehler schreiben keine neue erfolgreiche Abrufzeit. Das Veraltet-Alter bezieht sich auf die Fahrzeugmeldung.

Restzeit: `ceil((32.3 * (Ziel-SOC)/100 / (Wallbox-kW * 0.9) * 60)/5)*5` Minuten. 32,3 kWh nutzbare Kapazität laut [Škoda](https://www.skoda-storyboard.com/en/press-kits/skoda-citigo%E1%B5%89-iv-skoda-superb-iv-press-kit/skoda-citigo%E1%B5%89-iv-zero-emission-range-of-up-to-260-km/). 90 % Wirkungsgrad sind eine ausdrücklich dokumentierte Annahme. Kein Hochrechnen des angezeigten SOC. Fehlender/veralteter SOC oder fehlende/ältere lokale Leistung (>30 Sekunden) sperrt die Schätzung; ein Sliderwechsel berechnet neu. Keine native Ladezeit aus der API und keine garantierte Fertigstellungszeit.

### Direkter Fahrzeug-Refresh

Der offizielle Vertrag wurde erneut auf alle Pfade geprüft: kein Wake-up-/Force-Refresh-Pfad. Die separate Community-Bibliothek [myskoda/rest_api.py](https://github.com/skodaconnect/myskoda/blob/main/myskoda/rest_api.py) enthält `wakeup` über `/v1/vehicle-wakeup/{vin}?applyRequestLimiter=true`, einen anderen Authentifizierungsweg und einen Hinweis auf maximal drei Aufrufe pro Tag. Die [HA-Integration](https://github.com/skodaconnect/homeassistant-myskoda/blob/main/docs/entities.md#wake-up-car) warnt zusätzlich vor fahrzeugabhängigen Grenzen bis zur nächsten Fahrt. Das ist kein Nachweis für diesen Citigo und nicht mit dem öffentlichen API-Schlüssel nutzbar. Kein solcher Befehl wurde ausprobiert. Die dortigen `refresh_*`-Methoden allein sind wiederum Datenabrufe, keine verifizierte neue Messung im Fahrzeug.

### Lokale Prozesssteuerung

`Stoppen.cmd` und `Neustarten.cmd` nutzen ausschließlich Loopback, ohne Proxy/Redirects, mit Produktprüfung, Sitzung und CSRF. Der Neustart-Endpunkt verlangt gespeicherten Zugang, wenn ein Schlüssel oder eine aktive Sitzung vorliegt. Er persistiert Sitzung und Abfragepausen, beendet Worker, Server, Energieanforderungen und Instanzsperre und startet dann dasselbe Profil. Ein neuer zufälliger Instanzbezeichner im Health-Status bestätigt den Prozesswechsel. Es gibt kein pauschales Beenden von Python-Prozessen. Normales Beenden bleibt bei aktiver Überwachung gesperrt.

[Brave dokumentiert Kommandozeilenflags unter Windows](https://support.brave.app/hc/en-us/articles/360044860011-How-Do-I-Use-Command-Line-Flags-in-Brave). Das Chromium-Startflag `--start-fullscreen` ist im [offiziellen Quellcode](https://chromium.googlesource.com/chromium/src.git/+/2eee6cb90276be4e16fe7f2cf898ec558863a2d1/content/public/common/content_switches.cc) definiert. Der Launcher startet ein neues Fenster mit diesem Flag, ohne Shell-Auswertung. Bereits laufende Browserprozesse können Startflags übergehen; F11 bleibt der manuelle Weg zum Vollbild. Fehlt Brave oder schlägt der Prozessstart fehl, wird der Standardbrowser verwendet.

## Windows-Hintergrundüberwachung

`Start-Hintergrund.cmd` startet Python über einen getrennten Prozess mit CREATE_NO_WINDOW, umgeleitetem Ein-/Ausgang und festem Programmverzeichnis. Ein offener Browser ist für den Monitor nicht erforderlich. Es wird kein Dienst, Autostart oder geänderter Energiesparplan installiert.

Die Windows-API [PowerCreateRequest](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powercreaterequest) erstellt ein begründetes Request-Objekt. Während einer aktiven Zielüberwachung fordert [PowerSetRequest](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powersetrequest) SystemRequired (1) und ExecutionRequired (3) an. DisplayRequired und AwayModeRequired werden nicht angefordert. PowerClearRequest und CloseHandle geben Anforderungen nach Ende der Sitzung und beim Prozessabbau frei.

Laut Microsoft enden diese Anforderungen auf Modern-Standby-Systemen im Akkubetrieb fünf Minuten nach Ablauf des System-Schlafzeitlimits; ein explizit ausgelöster Standby beendet sie ebenfalls. Das Verhalten am konkreten Tablet muss daher im Netzbetrieb mit dessen Windows-Energiepolitik geprüft werden. Die App zeigt eine angeforderte Wachhaltung und die Netzversorgung an, behauptet keine Fähigkeit, während echten Schlafs oder bei ausgeschaltetem PC zu arbeiten.
