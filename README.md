# CitiFoxCharger für Windows · 1.1.0

Für Škoda Citigo e iV, SMARTFOX Pro 2 mit Firmware EM3 00.01.16.08 und Charger Pro an Ladeplatz 1.

## Start und Update

Den gesamten ZIP-Inhalt entpacken und **Start.cmd** doppelklicken. Voraussetzung: Python 3.11 oder neuer. Keine Zusatzpakete erforderlich. Das Startskript öffnet **Brave im Vollbild** unter **http://127.0.0.1:8765/**. Brave wird an den üblichen Windows-Installationsorten und im Suchpfad gesucht; fehlt Brave oder schlägt dessen Start fehl, öffnet sich der Standardbrowser. **F11** verlässt das Vollbild. Bei bereits laufendem Brave kann dessen Fensterverwaltung das Startflag übergehen; dann F11 drücken oder Brave einmal vollständig schließen.

Die Betriebsansicht ist für ein Tablet mit einem 1280 × 800-Panel **im Hochformat (800 × 1280)** angepasst: große Schrift, große Tasten und ein breiter Slider mit großem Griff. Auch Querformat und kleinere Fenster bleiben bedienbar. Die App dreht den Windows-Bildschirm nicht selbst.

Bei einem Update die alte App zuerst mit Strg+C beenden und die Programmdateien ersetzen. Der Datenordner behält aus Kompatibilitätsgründen seinen bisherigen Namen. Einstellungen und optional verschlüsselter Schlüssel liegen separat unter **%LOCALAPPDATA%\Ladezentrale**. Ohne aktivierte Windows-Speicherung muss der API-Schlüssel nach einem Neustart erneut eingegeben werden.

Alternativ im Programmordner:

```powershell
py -3 run.py
```

Anderen Port verwenden:

```powershell
py -3 run.py --port 8766
```

Brave mit Vollbild aus der Konsole starten: `py -3 run.py --browser brave --fullscreen`. Standardbrowser verwenden: `py -3 run.py --browser default`. Nur das Backend starten: `py -3 run.py --no-browser`. Pro Datenordner verhindert eine lokale Sperre mehrere gleichzeitig laufende Ladeüberwachungen, auch bei verschiedenen Ports.

## Betriebsansicht

Die Startseite zeigt Akkustand, **aktuelle Ladeleistung in kW**, Ladestatus, Ziel-Slider und zwei Tasten. Bei gemessener Ladeleistung über 0,1 kW pulsieren Blitz und Statuspunkt, ein Licht läuft über den SOC-Balken. Der Balken bleibt auf dem tatsächlich gemeldeten Prozentwert. Ohne frische lokale Leistungsmessung kann ein frischer Fahrzeugstatus „CHARGING“ die Animation aktivieren. Veraltete oder fehlende Leistung erscheint als „— kW“. Die Animation berücksichtigt die Windows-Einstellung für reduzierte Bewegung. Technische Daten stehen unter **Einstellungen → Diagnose und Verbindungsdetails**.

### Manuell laden

1. Einen Ziel-Ladestand zwischen 1 und 100 % wählen. Das Ziel muss über dem aktuellen Akkustand liegen.
2. **Manuell laden** drücken. Die ausdrücklich freigegebene Smartfox-Websteuerung schaltet zuerst Charger 1 auf **M+** und liest den Modus zur Bestätigung zurück. Erst danach sendet die App **Start** an den Citigo.
3. Das Backend überwacht den Škoda-Ladestand. Sobald eine aktuelle Fahrzeugmeldung das Ziel erreicht oder überschreitet, fordert es **Stopp** an und stellt Smartfox auf **A+** zurück.

### Stoppen

**Stoppen** fordert einen Fahrzeugstopp an und stellt Smartfox auf **A+** zurück. Stopp ist auch direkt nach einem Start möglich. Ein veralteter Ladezeitstempel sperrt einen neuen Start; ein Stoppsignal bleibt bei bestätigter, zuletzt erfolgreich abgefragter Stopp-Fähigkeit und gültigem API-Zugang möglich.

Ein angenommener Befehl ist noch keine bestätigte Ladungsänderung. Die App wartet auf einen passenden neueren Fahrzeugstatus. Wenn der Ausgang eines Stopps unklar bleibt, wird er nur bei neuerer Telemetrie mit weiterhin laufender Ladung erneut versucht, höchstens drei Mal. Der Fahrzeugstart wird niemals automatisch wiederholt.

### Normalbetrieb

Außerhalb einer manuellen Sitzung ist **A+** vorgesehen. Steht Smartfox auf **A**, wird **A+** eingestellt. Die App ändert keine Wochenzeitschaltuhr oder Fahrzeug-Ladeprofile.

A+ bedeutet laut Smartfox-Handbuch Überschussladen mit zusätzlicher Berücksichtigung der **Smartfox-Wochenzeitschaltuhr**. Die Zeitsteuerung im Fahrzeug ist eine eigene Ebene. M+ verwendet die bereits in Smartfox konfigurierten Energie- oder Zeitparameter und dessen Fallback. Diese Einstellungen können M+ vor dem Prozentziel beenden; die App verändert sie nicht automatisch.

## Zielüberwachung

Die Überwachung läuft in einem Hintergrund-Thread des Backends. Der Browser darf geschlossen werden. **Der Windows-PC und das Backend müssen weiterlaufen.** Energiesparmodus, Netzwerkverlust oder abgelaufener Schlüssel können eine Zielabschaltung verzögern oder verhindern. Ein Sitzungszustand wird lokal gespeichert. Nach einem Neustart wird eine vorhandene Überwachung anhand frischer Daten fortgesetzt; es wird kein Startbefehl wiederholt.

Das Prozentziel wird anhand der Cloud-Fahrzeugmeldungen geprüft. Der Stopp erfolgt beim ersten neuen, gültigen SOC-Wert ab Ziel. Der tatsächliche SOC kann durch Melde- oder Befehlsverzögerungen darüber liegen. Die native Fahrzeug-Ladegrenze wird durch diese App nicht geändert.

Die lokale Ansicht und die Smartfox-Leistung aktualisieren sich standardmäßig alle zehn Sekunden; nach mehr als 30 Sekunden gilt die Leistung für die Anzeige als veraltet. Während einer Ladung verteilt die App die verfügbaren Škoda-Abfragen anhand der RateLimit-Header bis zur nächsten Kontingenterneuerung und reserviert zwei Aufrufe für Befehle. Ohne verwertbare Limitangaben gilt während der Ladung mindestens das konfigurierte Intervall bzw. 180 Sekunden, im Leerlauf mindestens zehn Minuten. Bei bekanntem ausreichendem Kontingent kann das Ladeintervall bis auf 60 Sekunden sinken. Befehle und Abfragen teilen sich das Kontingent; Serverpausen bleiben verbindlich.

Die Taste **↻** fragt die verfügbaren Fahrzeugdaten ausdrücklich neu bei Škoda ab, frühestens 60 Sekunden nach der letzten Anfrage. Reservierte Befehlsaufrufe und Serverlimits werden dabei nicht aufgebraucht bzw. umgangen. Eine häufigere Cloud-Abfrage erzeugt keinen neuen Fahrzeugmesswert. Im geprüften [offiziellen API-Vertrag](https://public.api.connect.skoda-auto.cz/docs/myskoda-public-api.yaml) ist kein gesonderter Weck- oder Zwangsaktualisierungsbefehl dokumentiert. Maßgeblich für „Daten veraltet“ bleibt der Zeitstempel der Fahrzeugmeldung.

## Smartfox-Status und Steuerfreigabe

Modbus TCP bleibt **ausschließlich lesend**: 192.168.0.81, Port 502, Unit ID 1, FC03. Die gewünschten PDU-Adressen 40999, 41015 und 42111 bleiben als Rohwerte in der Diagnose erhalten. 41015 ist laut älterer Herstellerliste kein bestätigtes Ladeleistungsregister; 42111 bleibt ungeklärt.

Die tatsächliche Anzeige von **A+**, **M+** und Leistung wird ergänzend aus der lokalen Herstelleranzeige `values.xml` für genau **EM3 00.01.16.08** gelesen. Die Zuordnung wurde am ausgelieferten Hersteller-JavaScript geprüft. Die Register werden dafür nicht umgedeutet.

Die Umschaltung verwendet einen eng begrenzten Adapter: ausschließlich Charger 1, ausschließlich M+ und A+, fester lokaler Pfad und anschließende Rückleseprüfung. Die CGI-Befehle werden von der Hersteller-Weboberfläche selbst verwendet, sind aber nicht öffentlich als API spezifiziert. **Der Benutzer hat diese lokale Websteuerung am 16.10.2026 ausdrücklich freigegeben. Sie ist in diesem Stand aktiviert.** Der reale Test A+ → M+ → A+ war mit Rückleseprüfung erfolgreich. Andere Firmware, nicht erreichbare Anzeige oder fehlende Modusbestätigung sperren den kombinierten Start.

## MyŠkoda einrichten

Unter **Einstellungen** die vollständige 17-stellige FIN eintragen und speichern. Einen in MyŠkoda für dieses Fahrzeug erstellten API-Schlüssel lokal hinterlegen. Die Schlüsselverwaltung befindet sich nach Nutzerberichten unter **Profil → Smart Home**; alternativ den [offiziellen Link](https://go.skoda.eu/api-keys) auf dem Smartphone öffnen.

Der Schlüssel bleibt wahlweise im Arbeitsspeicher oder wird mit Windows DPAPI benutzergebunden verschlüsselt gespeichert. Er wird nicht in Statusantworten zurückgegeben und nicht protokolliert. Kein Kontopasswort und keine S-PIN erforderlich. Aktive Sitzungen verhindern das Entfernen des Schlüssels oder einen Gerätewechsel; ein abgelaufener Schlüssel kann ersetzt werden.

Der SOC-Abruf wurde für das Fahrzeug des Benutzers praktisch bestätigt. Der Benutzer bestätigte nach einem Startbefehl auch die tatsächliche Ladung. Die lokale Smartfox-Schaltung A+ → M+ → A+ wurde real bestätigt. Der gemeinsame Ablauf und die SOC-Zielabschaltung wurden mit simulierten Geräten getestet; eine komplette neue reale Sitzung bis zum Prozentziel steht noch aus.

## Fehler und Diagnose

- **Nicht eingerichtet:** FIN und API-Schlüssel hinterlegen.
- **Daten veraltet:** Die App wartet auf neue Fahrzeug-Ladedaten. Die MyŠkoda-App kann einen anderen Zeitpunkt anzeigen als der Ladezeitstempel. Der Cloud-Cache kann zusätzlich bis zum nächsten Abfrageintervall verzögern.
- **Smartfox-Umschaltung gesperrt:** Firmware, lokaler Zugriff oder Modusbestätigung passen nicht. Bei einer bewusst deaktivierten Adapterfreigabe bleibt der kombinierte Start ebenfalls gesperrt.
- **Zielüberwachung eingeschränkt:** Neue verlässliche Fahrzeugdaten oder API-Zugriff fehlen; die Abschaltung kann nicht bestätigt werden.
- **Rückkehr zu A+ steht aus:** Fahrzeugstopp und Smartfox-Modus werden separat geprüft. Die App behauptet keinen erfolgreichen Moduswechsel ohne Rückleseprüfung.
- **Schlüssel abgelaufen / Abfragepause:** Schlüssel erneuern bzw. das angezeigte Limit abwarten.

Die ausführliche technische Herleitung steht in [SCHNITTSTELLEN.md](SCHNITTSTELLEN.md), der Teststand in [PRUEFBERICHT.md](PRUEFBERICHT.md).

## Speicherung und Zugriffsschutz

Der Dienst bindet ausschließlich an 127.0.0.1. Origin-/Host-Prüfung, lokale Sitzung, HttpOnly-/SameSite-Cookie, CSRF und feste Škoda-HTTPS-Domain mit Zertifikatsprüfung bleiben aktiv. Kein LAN-Zugriff oder Portforwarding vorgesehen. Die Oberfläche hat keine externen Skripte, Schriften oder Analysezugriffe.

Dateien im Datenordner: `config.json` mit FIN und Einstellungen, optional `skoda-key.dpapi`, sowie `charging-session.json` mit Ziel und Sitzungsstatus. Die Sitzung enthält eine FIN-Zuordnung, keinen API-Schlüssel. Beim Weitergeben des Datenordners personenbezogene Angaben berücksichtigen.

Ein eigener Datenordner kann mit `--data-dir "C:\MeinOrdner\Ladezentrale-Daten"` gesetzt werden. Das Beenden des Backends stoppt eine reale Ladung nicht automatisch. Nach Prozessende kann die App auch keine Zielabschaltung ausführen.

## Tests

**Test.cmd** oder:

```powershell
py -3 -m unittest discover -s tests -v
```

80 Tests, ohne reale Fahrzeug- oder Wallboxbefehle. Geprüft sind Protokolle, Sicherheit, verschlüsselte Schlüsselablage, kombinierte Abläufe, Fehler, neue Sitzungen nach einem Stopp, Neustartzustände, Hintergrundüberwachung ohne Browser, Kontingentsteuerung, Instanzsperre und Brave-Start mit Rückfall auf den Standardbrowser.

## Repository

Privates GitHub-Repository: [danielrammer/CitiFoxCharger](https://github.com/danielrammer/CitiFoxCharger). Lokale Arbeitskopie: `D:\Repositories\CitiFoxCharger`. Konfiguration, Schlüssel und Ladesitzungen gehören nicht ins Repository; die Laufzeitdaten liegen weiterhin separat im oben genannten Windows-Datenordner.
