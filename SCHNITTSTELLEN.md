# Verifikation der Schnittstellen

Stand: **16.10.2026**, Version 1.1.0. Primärquellen und die tatsächlich installierte Geräteoberfläche wurden geprüft. Die ältere Chat-Unterhaltung wurde nicht als Schnittstellennachweis übernommen.

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

Der Benutzer hat am 16.10.2026 ausdrücklich geantwortet: **„Ja, lokale Websteuerung freigeben“**. Deshalb aktiviert diese auf sein Gerät zugeschnittene Anwendung den eng begrenzten Webadapter. Die Klasse selbst bleibt ohne explizite Aktivierung gesperrt. Erlaubt sind nur M+ und A+ an Charger 1, nur bei exakt passender Firmware und privater IPv4-Adresse, ohne Umleitungen oder Systemproxy. XML-Größe und Schema werden geprüft. Nach einer Änderung muss der zurückgelesene Modus passen; andernfalls startet die App das Auto nicht.

**Realer Nachweis:** A+ → M+ → A+ mit Rücklesebestätigung erfolgreich; Endzustand A+. Kein neuer Fahrzeugbefehl während dieses Tests.

Laut Handbuch bedeutet A+ PV-Überschuss mit zusätzlicher Smartfox-Wochenzeitschaltuhr. Fahrzeug-Schaltzeiten sind eine eigene Ebene. M+ hat native Energie-/Zeitvorgaben und einen Fallback; diese bleiben wirksam und können M+ vor einem SOC-Ziel beenden. Die App ändert weder diese Vorgaben noch Fahrzeug-Ladeprofile.

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

HTTP 202 bedeutet Annahme eines Befehls, keine Zustandsbestätigung. Erst eine neuere passende Fahrzeugmeldung bestätigt den Ausgang. Start wird nie automatisch wiederholt; unsichere Stopps werden nur nach neuerer CHARGING-Telemetrie und begrenzt erneut versucht. Eine neuere gestoppte Meldung kann den Stopp ohne erneuten Befehl bestätigen.

## Brave-Start

[Brave dokumentiert Kommandozeilenflags unter Windows](https://support.brave.app/hc/en-us/articles/360044860011-How-Do-I-Use-Command-Line-Flags-in-Brave). Das Chromium-Startflag `--start-fullscreen` ist im [offiziellen Quellcode](https://chromium.googlesource.com/chromium/src.git/+/2eee6cb90276be4e16fe7f2cf898ec558863a2d1/content/public/common/content_switches.cc) definiert. Der Launcher startet ein neues Fenster mit diesem Flag, ohne Shell-Auswertung. Bereits laufende Browserprozesse können Startflags übergehen; F11 bleibt der manuelle Weg zum Vollbild. Fehlt Brave oder schlägt der Prozessstart fehl, wird der Standardbrowser verwendet.
