# CitiFoxCharger für Windows · 1.6.0

Für Škoda Citigo e iV, SMARTFOX Pro 2 mit Firmware EM3 00.01.16.08 und Charger Pro an Ladeplatz 1.

## Start und Update

Den gesamten ZIP-Inhalt entpacken und **Start.cmd** doppelklicken. Voraussetzung: Python 3.11 oder neuer. Keine Zusatzpakete erforderlich. Die Skripte prüfen `py -3` und verwenden sonst `python` aus dem Suchpfad. Das Startskript öffnet **Brave im Vollbild** unter **http://127.0.0.1:8765/**. Brave wird an den üblichen Windows-Installationsorten und im Suchpfad gesucht; fehlt Brave oder schlägt dessen Start fehl, öffnet sich der Standardbrowser. **F11** verlässt das Vollbild. Bei bereits laufendem Brave kann dessen Fensterverwaltung das Startflag übergehen; dann F11 drücken oder Brave einmal vollständig schließen.

Die Betriebsansicht ist für ein Tablet mit einem 1280 × 800-Panel **im Hochformat (800 × 1280)** angepasst: große Schrift, große Tasten und ein breiter Slider mit großem Griff. Auch Querformat und kleinere Fenster bleiben bedienbar. Die App dreht den Windows-Bildschirm nicht selbst.

**Start.cmd** und der gleichwertige Einstieg **Start-Hintergrund.cmd** starten das Backend ohne dauerhaft offenes Konsolenfenster und öffnen Brave im Vollbild. Ein erneuter Aufruf öffnet die bereits laufende Instanz. Der Browser darf anschließend geschlossen oder der Bildschirm ausgeschaltet werden. Unter **Einstellungen → App beenden** lässt sich das Hintergrundprogramm beenden; während einer aktiven Ladung ist diese Taste gesperrt. Nach lokal bestätigtem Ladestillstand und Stoppwunsch ist sie auch ohne neue Fahrzeugmeldung verfügbar.

**Stoppen.cmd** beendet das Backend geordnet. Bei laufender Ladung oder fehlendem Stoppwunsch bleibt das Beenden gesperrt. Nach angefordertem Stopp ist es auch ohne neue Fahrzeugmeldung möglich, sobald mindestens fünf Sekunden vergangen sind und frisch von Smartfox A+ oder Aus, ein nicht ladender Wallboxstatus und höchstens 0,1 kW bestätigt sind. Dafür liest das Skript über das Backend die lokale Anzeige erneut; es sendet keinen weiteren Fahrzeugbefehl. Die Sitzung wird als bewusst geschlossen gespeichert, ohne eine bestätigte Fahrzeugantwort vorzutäuschen. Bei fehlenden/alten lokalen Daten, M+, Fehler oder weiterer Ladeleistung bleibt Beenden gesperrt. Fremde Prozesse und der Browser werden nicht beendet.

**Neustarten.cmd** startet das Backend geordnet neu und öffnet Brave im Vollbild. Das funktioniert auch während einer Ladeüberwachung, wenn der API-Schlüssel mit Windows verschlüsselt gespeichert ist. Ziel, Sitzung, ausstehende Datenfrist und API-Abfragepausen bleiben erhalten. Es wird kein neuer Ladestart gesendet. Der Browser wird erst nach Bestätigung einer neuen Backend-Instanz geöffnet; bei nicht bestätigtem Neustart nennt das Skript den Fehler. Läuft die App noch nicht, wird sie gestartet. Für einen anderen Port: `Neustarten.cmd --port 8766`; ohne Browser: `Neustarten.cmd --no-browser`. Bei laufender Instanz verwendet deren Backend seinen bisherigen Datenordner und Port weiter. Bei einem Kaltstart kann `--data-dir "C:\Pfad\zum\Datenordner"` angegeben werden.

Der letzte Fahrzeugwert wird beim geordneten Neustart mit seinem ursprünglichen Zeitstempel übernommen. Das verhindert, dass die für Stopp reservierten letzten Anfragen für einen Startabruf verbraucht werden. Übernommene Daten altern normal weiter und gelten nicht als neue Fahrzeugmeldung. Die Ablage ist an FIN und Schlüssel gebunden; der Schlüssel selbst steht dort nicht. Ein bereits offenes Browserfenster nach Neustart gegebenenfalls mit **F5** neu laden.

Bei einem Update die alte App zuerst unter **Einstellungen → App beenden** schließen; bei einer alten oder manuell gestarteten Konsolenversion stattdessen Strg+C verwenden. Erst danach die Programmdateien ersetzen. Der Datenordner behält aus Kompatibilitätsgründen seinen bisherigen Namen. Einstellungen und optional verschlüsselter Schlüssel liegen separat unter **%LOCALAPPDATA%\Ladezentrale**. Ohne aktivierte Windows-Speicherung muss der API-Schlüssel nach einem Neustart erneut eingegeben werden.

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

Die Startseite **Laden & Energie** zeigt Ladung und die kompakte Energieübersicht gemeinsam. Hell, Dunkel oder System lassen sich unter **Einstellungen → Darstellung** wählen; die Sonne/Mond-Taste schaltet direkt um. Die Auswahl bleibt in diesem Browser gespeichert.

Oben stehen SOC, aktuelle Smartfox-Ladeleistung, Ladestatus, Ziel-Slider und die Tasten für Start/Stopp. Eine deutliche Pfeilanimation führt Energie zum Autosymbol, die Batterie zeigt bewegte Streifen und der Status pulsiert. Das läuft nur bei frischer lokaler Leistung über 0,1 kW, ersatzweise bei frischem Fahrzeugstatus CHARGING ohne aktuelle lokale Leistung. Der SOC-Balken bleibt auf dem wirklich gemeldeten Wert. Reduzierte Bewegung im Betriebssystem unterdrückt die Animationen.

**Letzte Fahrzeugmeldung** nennt den ursprünglichen Ladezeitstempel des Autos; **Zuletzt erfolgreich abgerufen** nennt den letzten erfolgreichen Cloud-Abruf. Alle Zeiten erscheinen in der lokalen Windows-Zeitzone. „Veraltet“ zeigt zusätzlich das Alter der Fahrzeugmeldung. Ein neuer Abruf setzt deren Alter nicht zurück.

Die **geschätzte Restzeit** zum Sliderziel wird nur mit frischem SOC und höchstens 30 Sekunden alter gemessener Ladeleistung über 0,1 kW gezeigt. Modell: 32,3 kWh nutzbare Akkukapazität × fehlende Prozent / aktuelle kW / angenommener Wirkungsgrad 90 %. Die Kapazität stammt aus der [offiziellen Citigo-e-iV-Beschreibung](https://www.skoda-storyboard.com/en/press-kits/skoda-citigo%E1%B5%89-iv-skoda-superb-iv-press-kit/skoda-citigo%E1%B5%89-iv-zero-emission-range-of-up-to-260-km/); der Wirkungsgrad ist eine Annahme, kein gemessener Fahrzeugwert. Auf fünf Minuten aufgerundet, bei gleichbleibender Leistung. Temperatur, Ladeverluste, Pausen und alte SOC-Meldungen innerhalb des Frischefensters begrenzen die Genauigkeit. Die Schätzung steuert keinen Stopp und verändert weder SOC noch Ziel.

Darunter stehen Hausverbrauch, Netzbezug/Einspeisung und vier Verbraucherkacheln. Hausverbrauch folgt der Herstellerberechnung Erzeugung + Netzleistung − Batterieleistung und enthält auch die gesteuerten Verbraucher. Heizstab zeigt gemeldete kW, Prozent und Temperatur; Heizstab 2 und Warmwasser zeigen Relaiszustand und zugeordnete Temperatur, ohne erfundene elektrische Leistung. Fehlende Werte erscheinen als „—“. Diese Ansicht verwendet denselben lokalen XML-Abruf und erzeugt keine zusätzlichen Škoda-Anfragen.

### Betriebsarten der Verbraucher

- **Heizstab / Heizstab 2 / Warmwasser:** Aus, Automatik oder Manuell. Beim analogen Heizstab bleibt die in Smartfox konfigurierte manuelle Prozentleistung erhalten und wird in der Auswahl genannt. Keine Temperaturgrenze oder Gerätekonfiguration wird überschrieben. Bei Relais-Manuell bzw. Aus wird ein vorhandener manueller Timer wie in der Herstelleroberfläche beendet.
- **Ladestation:** ausschließlich Aus, A+ oder M+. Aus bleibt als ausdrücklicher Wunsch auch nach Neustart und bei Abschluss einer Zielüberwachung erhalten. Mit A+ wird wieder der normale Betrieb gewählt. M+ im Leerlauf benutzt denselben kombinierten Start mit Prozentziel wie **Manuell laden**. Während einer aktiven Zielüberwachung ändert die Auswahl nur die Wallbox; sie sendet keinen weiteren Fahrzeugstart. Fehlender Fahrzeugzugriff oder laufender Stopp sperrt M+.

Die Schaltwege wurden im ausgelieferten Hersteller-JavaScript dieser Firmware geprüft. Eine öffentliche CGI-API-Spezifikation fehlt. Es werden nur die konkret zugeordneten Ausgänge angeboten: Heizstab am Analogausgang, Heizstab 2 an Relais 1, Warmwasser an Relais 2 und Charger 1. Jede Umschaltung wird zurückgelesen. Fehlende Zuordnung, andere Firmware oder fehlende Bestätigung führen zu einem Fehler. Die neuen Wärme-/Aus-Befehle wurden ausschließlich mit simulierten Geräten getestet; reale Bedienung erfolgt durch den Benutzer.

### Manuell laden

1. Einen Ziel-Ladestand zwischen 1 und 100 % wählen. Das Ziel muss über dem aktuellen Akkustand liegen.
2. **Manuell laden** drücken. Die ausdrücklich freigegebene Smartfox-Websteuerung schaltet zuerst Charger 1 auf **M+** und liest den Modus zur Bestätigung zurück. Danach wartet die App bis zu 15 Sekunden auf frische Herstellerdaten mit Ladebereitschaft, aktivem M+ und mindestens 6 A Stromvorgabe; die Anlaufprüfung dauert mindestens drei Sekunden. Erst danach sendet sie genau einmal **Start** an den Citigo. Bei ausbleibender Freigabe erfolgt kein Fahrzeugstart, und die App versucht, zu A+ zurückzukehren.
3. Das Backend überwacht den Škoda-Ladestand. Sobald eine aktuelle Fahrzeugmeldung das Ziel erreicht oder überschreitet, fordert es **Stopp** an und stellt Smartfox auf **A+** zurück.

Der **Slider bleibt während des Ladens verstellbar**. Nach Loslassen wird das neue Prozentziel gespeichert, ohne einen weiteren Start zu senden. Liegt das Ziel bereits beim oder unter dem frisch gemeldeten SOC, fordert die App Stopp an. Bei alten Fahrzeugdaten wartet sie weiter auf eine frische Meldung; eine laufende Fünf-Minuten-Frist wird durch Zieländerungen nicht verlängert. Nach angefordertem Stopp ist das Ziel gesperrt, damit eine Erhöhung den Stopp nicht unbeabsichtigt aufhebt.

### Start erneut freigeben

Ein angenommener Start kann ohne tatsächliche Ladung bleiben. **Start erneut freigeben** wird angeboten, wenn die Wallbox frische Daten mit „ladebereit“ und höchstens 0,1 kW liefert, kein Stopp läuft und Škoda gültigen Start-/Stopp-Zugriff sowie einen ausreichend aktuellen SOC liefert. Zusätzlich müssen seit dem Versuch mindestens 30 Sekunden vergangen sein und entweder eine neuere nicht ladende Fahrzeugmeldung vorliegen oder der Versuch seit zehn Minuten unbestätigt sein.

Nur ein ausdrücklicher neuer Tastendruck sendet einen weiteren Start. Das bisherige Prozentziel bleibt fest. Doppelte Anfragen mit derselben Kennung lösen keinen weiteren Start aus. Bei tatsächlicher Ladung, alten Wallboxdaten oder laufendem Stopp bleibt der erneute Start gesperrt. Die Betriebsansicht nennt den Sperrgrund und verwendet bei frischen lokalen Daten „Eingesteckt · ladebereit“. Der Cloud-Status bleibt separat erhalten.

### Mit älterem Ladestand starten

Bei **Daten veraltet** erscheint unter dem Slider gegebenenfalls **Zuletzt gemeldeten Ladestand verwenden**. Erst nach bewusstem Anhaken wird **Manuell laden** bzw. **Start erneut freigeben** aktiv. Die Freigabe gilt für diesen einen Start; Prozentwert, Fahrzeugzeit und Veraltet-Markierung bleiben unverändert.

Voraussetzungen: gültiger API-Zugang, innerhalb der letzten fünf Minuten erfolgreich abgefragte Start-/Stopp-Fähigkeiten, bekannter SOC mit Fahrzeugzeit höchstens 24 Stunden alt sowie frische lokale Smartfox-Daten mit Ladebereitschaft und höchstens 0,1 kW. Fehlende Daten, abgelaufener Schlüssel, Serverpause, tatsächliche Ladung oder laufender Stopp werden dadurch nicht übergangen. Beim erneuten Start bleiben zusätzlich die bestehende Wartezeit und das Prozentziel erhalten. Fehlt die Freigabe wegen einer älteren Cloud-Abfrage, kann **↻** im Rahmen des API-Kontingents erneut abfragen.

Nach einem solchen Start wartet das Backend höchstens **fünf Minuten auf eine neue, nach dem Start erfasste SOC-Meldung**. Bleibt sie aus, fordert es einen Fahrzeugstopp an und versucht auch bei Cloud-Ausfall oder abgelehntem Stopp, Smartfox auf **A+** zurückzustellen. Eine fehlgeschlagene Rückstellung wird weiter versucht. Die Frist bleibt nach einem Backend-Neustart erhalten; Start wird nicht wiederholt. Ein neuer Fahrzeugwert beendet diese Sonderprüfung, anschließend gilt die normale Zielüberwachung. Ein unbestätigter Start mit unbekanntem Ausgang behält die Prüfung ebenfalls bei.

Das ist keine garantierte Abschaltung des Ladestroms: Der Cloud-Stopp kann scheitern, und A+ erlaubt weiterhin das normale Überschuss-/Zeitprogramm. Der Status bleibt bis zur passenden neuen Fahrzeugmeldung unbestätigt. Bei alten Daten ist auch der angezeigte Prozentwert kein sicherer aktueller Wert.

### Stoppen

**Stoppen** fordert einen Fahrzeugstopp an und versucht auch bei einem Cloud-Fehler, Smartfox auf **A+** zurückzustellen. Stopp ist auch direkt nach einem Start möglich. Ein veralteter Ladezeitstempel sperrt einen neuen Start ohne die oben beschriebene ausdrückliche Freigabe; ein Stoppsignal bleibt bei bestätigter, zuletzt erfolgreich abgefragter Stopp-Fähigkeit und gültigem API-Zugang möglich.

Ein angenommener Befehl ist noch keine bestätigte Ladungsänderung. Die App wartet auf einen passenden neueren Fahrzeugstatus. Wenn der Ausgang eines Stopps unklar bleibt, wird er nur bei neuerer Telemetrie mit weiterhin laufender Ladung erneut versucht, höchstens drei Mal. Der Fahrzeugstart wird niemals automatisch wiederholt.

### Normalbetrieb

Außerhalb einer manuellen Sitzung ist **A+** vorgesehen. Steht Smartfox auf **A**, wird **A+** eingestellt. Die App ändert keine Wochenzeitschaltuhr oder Fahrzeug-Ladeprofile.

A+ bedeutet laut Smartfox-Handbuch Überschussladen mit zusätzlicher Berücksichtigung der **Smartfox-Wochenzeitschaltuhr**. Die Zeitsteuerung im Fahrzeug ist eine eigene Ebene. M+ verwendet die bereits in Smartfox konfigurierten Energie- oder Zeitparameter und dessen Fallback. Diese Einstellungen können M+ vor dem Prozentziel beenden; die App verändert sie nicht automatisch.

## Zielüberwachung

Die Überwachung läuft in einem Hintergrund-Thread des Backends. Der Browser darf geschlossen werden. **Der Windows-PC und das Backend müssen weiterlaufen.** Energiesparmodus, Netzwerkverlust oder abgelaufener Schlüssel können eine Zielabschaltung verzögern oder verhindern. Ein Sitzungszustand wird lokal gespeichert. Nach einem Neustart wird eine vorhandene Überwachung anhand frischer Daten fortgesetzt; es wird kein Startbefehl wiederholt.

Während einer aktiven Zielüberwachung fordert die App von Windows **SystemRequired und ExecutionRequired** an. Damit soll das Backend auch bei automatischer Bildschirmabschaltung weiterarbeiten. Eine DisplayRequired-Anforderung wird nicht gesetzt: Der Bildschirm darf nach dem Windows-Zeitplan ausgehen. Die Anforderungen werden nach bestätigtem Sitzungsende oder beim Beenden des Backends freigegeben. Windows-Energiepläne werden nicht dauerhaft verändert. Die Betriebsansicht zeigt den Status während einer aktiven Überwachung.

**Das Wandtablet am Netzteil betreiben.** Auf Modern-Standby-Geräten begrenzt Windows solche Anforderungen im Akkubetrieb nach Ablauf des Schlafzeitlimits. Manuell ausgelöster Standby, Ruhezustand, Ausschalten und Abmelden können die Überwachung unterbrechen; ein ausgeschalteter Bildschirm allein braucht das nicht. Für den ersten Betrieb auf dem konkreten Tablet zusätzlich unter Windows **Energiesparen im Netzbetrieb: Nie**, aber **Bildschirm ausschalten: nach Wunsch** einstellen und einmal mit dunklem Bildschirm prüfen. Die App setzt keinen Admin-Dienst und keinen Autostart auf. [Microsoft erklärt die Grenzen von Energieanforderungen](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powersetrequest).

Für automatisches Wiederaufnehmen nach einem Programm- oder Windows-Neustart muss der Schlüssel über die vorhandene Option mit Windows verschlüsselt gespeichert sein; ein reiner RAM-Schlüssel ist nach Prozessende weg. `background.log` im Datenordner enthält Start-/Fehlermeldungen ohne absichtliche Ausgabe von FIN oder API-Schlüssel.

Das Prozentziel wird anhand der Cloud-Fahrzeugmeldungen geprüft. Der Stopp erfolgt beim ersten neuen, gültigen SOC-Wert ab Ziel. Der tatsächliche SOC kann durch Melde- oder Befehlsverzögerungen darüber liegen. Die native Fahrzeug-Ladegrenze wird durch diese App nicht geändert.

Die lokale Ansicht und die Smartfox-Leistung aktualisieren sich standardmäßig alle zehn Sekunden; nach mehr als 30 Sekunden gilt die Leistung für die Anzeige als veraltet. Während einer Ladung verteilt die App die verfügbaren Škoda-Abfragen anhand der RateLimit-Header bis zur nächsten Kontingenterneuerung und reserviert zwei Aufrufe für Befehle. Ohne verwertbare Limitangaben gilt während der Ladung mindestens das konfigurierte Intervall bzw. 180 Sekunden, im Leerlauf mindestens zehn Minuten. Bei bekanntem ausreichendem Kontingent kann das Ladeintervall bis auf 60 Sekunden sinken. Befehle und Abfragen teilen sich das Kontingent; Serverpausen bleiben verbindlich.

Die Taste **↻** fragt die verfügbaren Fahrzeugdaten ausdrücklich neu bei Škoda ab, frühestens 60 Sekunden nach der letzten Anfrage. Reservierte Befehlsaufrufe und Serverlimits werden dabei nicht aufgebraucht bzw. umgangen. Eine häufigere Cloud-Abfrage erzeugt keinen neuen Fahrzeugmesswert. Im geprüften [offiziellen API-Vertrag](https://public.api.connect.skoda-auto.cz/docs/myskoda-public-api.yaml) ist kein gesonderter Weck- oder Zwangsaktualisierungsbefehl dokumentiert. Maßgeblich für „Daten veraltet“ bleibt der Zeitstempel der Fahrzeugmeldung.

Die separate [Community-Integration MySkoda](https://github.com/skodaconnect/homeassistant-myskoda/blob/main/docs/entities.md#wake-up-car) besitzt einen echten Weckbefehl über die nicht öffentliche App-Schnittstelle. Dessen Zugang verwendet eine andere Anmeldung als unseren öffentlichen API-Schlüssel; Home Assistant ist dafür technisch nicht zwingend. Die Bibliothek begrenzt das Wecken und warnt vor fahrzeugseitigem Schutz der 12-V-Batterie. Funktion und zulässiges Kontingent am konkreten Citigo sind nicht bestätigt. Dieser alternative Adapter ist daher **nicht eingebaut oder als verfügbar dargestellt**. Es werden weder Klimatisierung noch wiederholte Ladebefehle als vermeintlicher Daten-Refresh ausgelöst.

## Smartfox-Status und Steuerfreigabe

Modbus TCP bleibt **ausschließlich lesend**: 192.168.0.81, Port 502, Unit ID 1, FC03. Die gewünschten PDU-Adressen 40999, 41015 und 42111 bleiben als Rohwerte in der Diagnose erhalten. 41015 ist laut älterer Herstellerliste kein bestätigtes Ladeleistungsregister; 42111 bleibt ungeklärt.

Die tatsächliche Anzeige von **A+**, **M+** und Leistung wird ergänzend aus der lokalen Herstelleranzeige `values.xml` für genau **EM3 00.01.16.08** gelesen. Die Zuordnung wurde am ausgelieferten Hersteller-JavaScript geprüft. Die Register werden dafür nicht umgedeutet.

Die Umschaltung verwendet einen eng begrenzten Adapter: ausschließlich Charger 1, Aus/A+/M+ sowie die angeforderten drei Wärmeausgänge, feste lokale Pfade und anschließende Rückleseprüfung. Die CGI-Befehle werden von der Hersteller-Weboberfläche selbst verwendet, sind aber nicht öffentlich als API spezifiziert. **Der Benutzer hat diese lokale Websteuerung am 16.10.2026 ausdrücklich freigegeben. Sie ist in diesem Stand aktiviert.** Der reale Test A+ → M+ → A+ war mit Rückleseprüfung erfolgreich. Andere Firmware, nicht erreichbare Anzeige oder fehlende Modusbestätigung sperren den kombinierten Start.

## MyŠkoda einrichten

Unter **Einstellungen** die vollständige 17-stellige FIN eintragen und speichern. Einen in MyŠkoda für dieses Fahrzeug erstellten API-Schlüssel lokal hinterlegen. Die Schlüsselverwaltung befindet sich nach Nutzerberichten unter **Profil → Smart Home**; alternativ den [offiziellen Link](https://go.skoda.eu/api-keys) auf dem Smartphone öffnen.

Der Schlüssel bleibt wahlweise im Arbeitsspeicher oder wird mit Windows DPAPI benutzergebunden verschlüsselt gespeichert. Er wird nicht in Statusantworten zurückgegeben und nicht protokolliert. Kein Kontopasswort und keine S-PIN erforderlich. Aktive Sitzungen verhindern das Entfernen des Schlüssels oder einen Gerätewechsel; ein abgelaufener Schlüssel kann ersetzt werden.

Der SOC-Abruf wurde für das Fahrzeug des Benutzers praktisch bestätigt. Der Benutzer bestätigte nach einem Startbefehl auch die tatsächliche Ladung. Die lokale Smartfox-Schaltung A+ → M+ → A+ wurde real bestätigt. Der gemeinsame Ablauf und die SOC-Zielabschaltung wurden mit simulierten Geräten getestet; eine komplette neue reale Sitzung bis zum Prozentziel steht noch aus.

## Fehler und Diagnose

- **Nicht eingerichtet:** FIN und API-Schlüssel hinterlegen.
- **Daten veraltet:** Wenn angeboten, **Zuletzt gemeldeten Ladestand verwenden** bewusst anhaken und anschließend starten. Nach fünf Minuten ohne neue SOC-Meldung fordert die App Stopp und A+ an. Sonst mit **↻** neu abfragen und Sperrgrund prüfen. Die MyŠkoda-App kann einen anderen Zeitpunkt anzeigen als der Ladezeitstempel; eine Cloud-Abfrage erzwingt keine neue Messung im Fahrzeug.
- **Smartfox-Umschaltung gesperrt:** Firmware, lokaler Zugriff oder Modusbestätigung passen nicht. Bei einer bewusst deaktivierten Adapterfreigabe bleibt der kombinierte Start ebenfalls gesperrt.
- **Zielüberwachung eingeschränkt:** Neue verlässliche Fahrzeugdaten oder API-Zugriff fehlen; die Abschaltung kann nicht bestätigt werden.
- **Rückkehr zu A+ steht aus:** Fahrzeugstopp und Smartfox-Modus werden separat geprüft. Die App behauptet keinen erfolgreichen Moduswechsel ohne Rückleseprüfung.
- **Schlüssel abgelaufen / Abfragepause:** Schlüssel erneuern bzw. das angezeigte Limit abwarten.

Die ausführliche technische Herleitung steht in [SCHNITTSTELLEN.md](SCHNITTSTELLEN.md), der Teststand in [PRUEFBERICHT.md](PRUEFBERICHT.md).

## Zugriff von anderen Geräten im Heimnetz

Optionaler, mit Passwort geschützter HTTPS-Zugang zum Anzeigen und Bedienen ist vorbereitet. Das Tablet betreibt weiterhin genau eine Ladeüberwachung. Zur Einrichtung auf dem Tablet **Netzwerk-Einrichten.cmd**, anschließend **Netzwerk-Firewall.cmd** als Administrator ausführen und das erzeugte öffentliche Zertifikat auf den zugreifenden Geräten übernehmen. Erst danach ist die angezeigte `https://Tablet-IP:8766/`-Adresse nutzbar. Der Zugang bleibt bis zur lokalen Einrichtung ausgeschaltet. Vollständige Schritte: **[NETZWERK.md](NETZWERK.md)**.

## Speicherung und Zugriffsschutz

Die lokale Verwaltung bindet an 127.0.0.1. Der optionale zusätzliche HTTPS-Listener bindet nur an eine konfigurierte private Tablet-IPv4-Adresse und verlangt ein eigenes Netzwerkpasswort. Origin-/Host-Prüfung, lokale Sitzung, HttpOnly-/SameSite-Cookie, CSRF und feste Škoda-HTTPS-Domain mit Zertifikatsprüfung bleiben aktiv. Geräte- und Schlüsseleinrichtung sowie Backend-Beenden/Neustart bleiben ausschließlich lokal. Es wird keine Router-Portweiterleitung eingerichtet. Die Oberfläche hat keine externen Skripte, Schriften oder Analysezugriffe.

Dateien im Datenordner: `config.json` mit FIN und Einstellungen, optional `skoda-key.dpapi`, `charging-session.json` mit Ziel und Sitzungsstatus sowie `vehicle-cache.json` und `rate-limits.json` für geordneten Neustart. Bei optionaler Netzwerkeinrichtung kommen `network.json` mit Passwort-Hash und die Zertifikatsablage `network/` hinzu; der private TLS-Schlüssel ist benutzergebunden mit Windows-DPAPI verschlüsselt. Die Sitzung enthält eine FIN-Zuordnung, keinen API-Schlüssel. Beim Weitergeben des Datenordners personenbezogene Angaben berücksichtigen.

Ein eigener Datenordner kann mit `--data-dir "C:\MeinOrdner\Ladezentrale-Daten"` gesetzt werden. Das Beenden des Backends stoppt eine reale Ladung nicht automatisch. Nach Prozessende kann die App auch keine Zielabschaltung ausführen.

## Tests

**Test.cmd** oder:

```powershell
py -3 -m unittest discover -s tests -v
```

174 Tests, ohne reale Fahrzeug- oder Wallboxbefehle. Geprüft sind Protokolle, Sicherheit, verschlüsselte Schlüsselablage, kombinierte Abläufe, Fehler, neue Sitzungen nach einem Stopp, Neustartzustände, Hintergrundüberwachung ohne Browser, Kontingentsteuerung, Instanzsperre, Brave-Start, getrennter Hintergrundprozess, sichere Beendigung und Freigabe der Windows-Energieanforderungen nach dem 90-%-Ziel. Zusätzlich geprüft: ausdrücklicher Start mit älterem SOC, eng begrenzte Freigabe, persistente Fünf-Minuten-Frist, neue SOC-Meldungen, A+-Rückstellung bei abgelehntem/gesperrtem Cloud-Stopp, Energieeinheiten/Vorzeichen/Sensorzuordnung, Zieländerungen ohne Start und geschützter Neustart mit erhaltenem Zustand.

## Repository

Privates GitHub-Repository: [danielrammer/CitiFoxCharger](https://github.com/danielrammer/CitiFoxCharger). Lokale Arbeitskopie: `D:\Repositories\CitiFoxCharger`. Konfiguration, Schlüssel, Fahrzeugcache, Kontingentdaten und Ladesitzungen gehören nicht ins Repository; die Laufzeitdaten liegen weiterhin separat im oben genannten Windows-Datenordner.

## Kompakte Tabletfläche

Die Betriebsansicht passt sich der nutzbaren Browserfläche an. Für 1280 × 800 im Querformat stehen Laden und Energie nebeneinander; für 800 × 1280 im Hochformat untereinander. Bei üblichen Windows-Skalierungen werden Abstände reduziert, die vier Verbraucher in einer Reihe gehalten und dekorative Symbole/zusätzliche Normalhinweise ausgeblendet. Die Aktionstasten bleiben mindestens 48 Pixel, Modusauswahlen mindestens 44 Pixel hoch. Fahrzeug- und Abrufzeit bleiben sichtbar. Erfolgsnachrichten verändern die Seitenhöhe nicht. Bei sehr kleinen Flächen oder langen Fehlermeldungen kann weiterhin Scrollen erforderlich sein; Einstellungen bleiben eine eigene, scrollbar bedienbare Seite.
