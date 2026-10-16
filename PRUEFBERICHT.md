# Prüfbericht · Version 1.6.0

Stand: **16.10.2026**, Windows / Python 3.14.

## Automatisierte Prüfung

**174 Tests erfolgreich**, zusätzlich JavaScript-Syntaxprüfung. Geräte und Fahrzeugbefehle sind in Tests simuliert. Lokale Testserver prüfen echte HTTP-/Sicherheitsabläufe; kein neuer realer Start/Stopp oder Wärmeschaltbefehl wird dafür gesendet.

Geprüft: Modbus-Leseregister, Protokollfehler, Wertebereiche, SOC-/FIN-/Fähigkeitsschema, Zeitstempel, Schlüsselablauf, Windows-DPAPI, RateLimit/Retry-After, Kontingentreserve, adaptive Abrufe, Host/Origin/CSRF/Sitzung, Loopback und keine Modbus-Schreibroute.

Ladung: bestätigtes M+ vor Fahrzeugstart, Ladebereitschaft/Anlaufzeit, genau ein Start, fehlgeschlagene/unsichere Befehle, begrenzte Wiederholung nur mit neuen Daten, Prozentziel-Stopp ohne Browser, neue Sitzung ohne alten Stoppwunsch, Neustart ohne Startwiederholung und Bindung an die FIN. Bewusst freigegebener alter SOC behält seinen Zeitstempel und eine persistente Fünf-Minuten-Frist. Neue SOC-Meldung beendet diese Sonderprüfung; Cloudfehler verhindern die lokale Rückstellung nicht. A+ ist keine garantierte Unterbrechung des Stroms.

Zieländerung: Erhöhen ohne Start, Absenken bis zum frischen SOC löst Stopp aus, altes SOC erzwingt keinen Zielstopp, die Fünf-Minuten-Frist bleibt unverändert, nach Stoppwunsch ist das Ziel gesperrt. Explizites Wallbox-Aus bleibt im Leerlauf, nach Neustart und nach Zielabschluss erhalten. A+/M+-Wechsel während einer Sitzung sendet keinen erneuten Fahrzeugstart und erhält das Ziel. M+ ohne überwachte Sitzung ist in der reinen Modusroute gesperrt.

Wärmeausgänge: alle drei Modi, exakte Analog-/Relais-Codes, vorhandene manuelle Leistung, Timerbeendigung vor Relais-Manuell/Aus, feste Zuordnung einschließlich Warmwasser an Relais 2, Firmware-/Freigabeprüfung, fehlender Sollwert, Rücklesefehler und wiederholte Befehlskennung. Energieanzeige prüft Einheiten, Netzvorzeichen, Herstellerformel, Sensorpositionen, fehlenden 850-°C-Wert und Relaiszustände ohne erfundene kW.

## Oberfläche

Browser-Vorschau ausschließlich mit simulierten Geräten bei **800 × 1280**: Ladung und vier kompakte Energiekacheln auf derselben Seite, keine horizontale Überbreite. Hell/Dunkel/System, gespeicherte Auswahl, Slider bei laufender Sitzung und Auswahl Aus/Auto/Manuell für Warmwasser wurden bedient. Wallbox zeigt nur Aus/A+/M+. Zeitpunkt der letzten Fahrzeugmeldung und letzter erfolgreicher Abruf sind sichtbar getrennt.

Beispiel mit 40 % SOC, 90 % Ziel und 4,59 kW: ca. 3 Std. 55 Min.; nach Sliderwechsel auf 89 % ca. 3 Std. 50 Min. Das ist das dokumentierte Modell mit 32,3 kWh nutzbar und angenommenen 90 % Wirkungsgrad. Bei alten/fehlenden Daten gibt es keine numerische Schätzung. Der Slider ändert das gespeicherte Ziel ohne zusätzlichen Start.

Deutliche Ladeanimation mit laufenden Pfeilen zum Autosymbol, pulsierendem Status und bewegter Batterie. Sie folgt frischer lokaler Leistung >0,1 kW, ersatzweise frischem CHARGING ohne aktuelle lokale Messung. HTTP 202 allein aktiviert sie nicht. Der echte SOC-Balken wird nicht künstlich aufgefüllt. Reduzierte Bewegung schaltet die Bewegung ab. Das konkrete Tablet und seine Windows-Skalierung sind nicht angeschlossen.

## Prozesssteuerung und Hintergrundbetrieb

Stoppen/Neustarten verwenden geschützte lokale Aufrufe und beenden keine fremden Python-Prozesse. Neustart persistiert Sitzung, Originalzeitstempel des Fahrzeugcache und Kontingentpausen. RAM-Schlüssel sperrt den Neustart; gespeicherter Schlüssel ermöglicht die fortgesetzte Zielüberwachung ohne neuen Start. Normales Beenden bleibt während aktiver Überwachung gesperrt.

Geordneter Start/Neustart/Stopp wurde in einem echten getrennten Windows-Prozess auf einem Testport mit leerem Fahrzeugzugang und unerreichbarer Geräteadresse geprüft: neuer Instanzbezeichner, gleiche Datenablage, kein verbliebener Testprozess. Der reale Backend-Neustart während einer laufenden Sitzung war mit verschlüsseltem Schlüssel erfolgreich; das durch den Benutzer gewählte 82-%-Ziel blieb erhalten, Smartfox meldete weiterhin M+ und etwa 4,6 kW. Keine zusätzliche Ladestartanforderung.

Windows-Energieanforderungen SystemRequired/ExecutionRequired, keine Bildschirmanforderung, Freigabe nach Sitzung/Prozessende und Teilfehler sind geprüft. Brave-Vollbildargumente und Standardbrowser-Fallback sind getestet. Die Skripte prüfen einen tatsächlich verfügbaren `py -3`-Interpreter und verwenden andernfalls `python`.

## Gerätebestätigung und Grenzen

Pro 2 / EM3 00.01.16.08 und Charger 1 unter 192.168.0.81 sind lesend bestätigt. Rohregister 40999/41015/42111 bleiben ohne ungeprüfte Bedeutungsübernahme. Lokale Energie-/Modusfelder und die eng begrenzten CGI-Schaltpfade wurden in der ausgelieferten Herstelleroberfläche geprüft. Es gibt dafür keine öffentliche API-Spezifikation. M+/A+-Umschaltung wurde nach Benutzerfreigabe real zurückgelesen; neue Wärme-/Aus-Befehle wurden ausschließlich simuliert geprüft.

Am konkreten Citigo sind SOC, Start-/Stopp-Fähigkeiten und der durch den Benutzer bestätigte Ladestart belegt. Eine vollständige reale Sitzung bis zur automatischen Prozentabschaltung steht aus. Die offizielle API enthält weiterhin keinen dokumentierten Weckbefehl. Der Community-Weckweg benötigt eine andere Anmeldung und ist für diesen Citigo nicht bestätigt oder eingebaut. Eine neue Cloud-Abfrage garantiert keinen neuen SOC.

Der Zielstopp ist eine lokale Überwachung, keine native Fahrzeuggrenze. Melde-/Befehlsverzögerungen können zum Überschreiten führen. Native M+-Zeit-/Energiegrenzen bleiben bestehen. Kein Langzeittest über Energiesparzustände oder Mobilfunkabschaltungen. Das Backend muss laufen; expliziter Standby oder Ausschalten wird nicht übergangen. Am Wandtablet sind Netzversorgung und passende Windows-Energieeinstellungen erforderlich. Kein Dienst oder Autostart eingerichtet.

## Heimnetz-Zugang

Zusätzlich geprüft: konkrete private IPv4 statt Wildcard, Passwort-Hash und Mindestlänge, fehlerhafte/abgelaufene Konfiguration, standardmäßig deaktivierter Listener, Sperre wiederholter falscher Anmeldung und Unicode-Passwort. Native Windows-PowerShell-5.1-/Framework-Zertifikate wurden in einer temporären Ablage erzeugt. Echte HTTPS-Tests mit strenger Zertifikatsketten- und IP-Prüfung bestätigen CA-Vertrauen ausschließlich im Testclient; die Prüfung wurde nicht abgeschaltet.

Unangemeldete Netzwerkzugriffe sind auch auf HTML/JS, Sitzung und Health gesperrt. Secure-Cookie, HTTPS-Origin, CSRF und Host werden geprüft. Remote-Ziel-/Heizungsbefehle erreichen denselben simulierten Dienst; Schlüssel-/Geräteeinrichtung und Backend-Beenden/Neustart bleiben gesperrt. Schließen des Netzwerklisteners lässt den gemeinsamen Monitor bestehen, Schließen des Elternservers beendet beide Listener und den Monitor genau einmal. Ungültige Netzwerk-Konfiguration beeinträchtigt den lokalen Backendstart nicht. Nach Neustartabsicht sind keine neuen Hardwarewirkungen oder Abfragen erlaubt.

Keine Netzwerkfreigabe, keine Firewalländerung und kein Zertifikatsimport am aktuellen PC. Kein Zugriff von einem zweiten physischen Gerät am späteren Wandtablet geprüft. Der Heimnetz-Zugang wird erst bei lokaler Einrichtung mit eigenem Passwort aktiviert; die öffentliche CER-Datei muss auf den Clients bewusst vertraut werden.

## Programmende nach physischem Ladestopp

Eine angeforderte, aber vom Auto noch nicht bestätigte Abschaltung blockiert das Programmende nicht unbegrenzt. Geprüft: mindestens fünf Sekunden alter Stoppwunsch, bestätigte Modusrückstellung, neuer lokaler XML-Abruf, A+/OFF, nicht ladender CP-Status und endliche Leistung zwischen 0 und 0,1 kW. Dann darf ein ausdrückliches lokales Beenden die Sitzung als `closed` markieren. Das ist von `complete` getrennt; `vehicle_stop_confirmed` bleibt false. Kein zusätzlicher Fahrzeug-/Wallboxbefehl, keine aufgegebene aktive Ladung. M+, Fehler, alte Daten, fehlende Verbindung, Leistungsfluss oder fehlender Stoppwunsch bleiben gesperrt. Auch die echte lokale HTTP-Beendenroute wurde damit erfolgreich geprüft. Neustart nimmt eine bewusst geschlossene Überwachung nicht wieder auf.

## Verfügbare Bildschirmhöhe

Die gerätefreie Browserprüfung mit sichtbarer TEST-Kennzeichnung bestätigte ohne vertikalen oder horizontalen Überlauf: 800 × 1280, 640 × 1024, gerundet 534 × 854 (150-%-Skalierung eines 800 × 1280-Panels) und 1280 × 800. Auch der längere Hinweis bei altem SOC passte. Hochformat behält die Verbraucher in einer Reihe; Querformat nutzt zwei Hauptspalten. Wichtige Schaltziele bleiben mindestens 44/48 Pixel hoch. Das konkrete Wandtablet ist weiterhin nicht angeschlossen; abweichende Windows-Skalierung und Browserleisten können die Fläche verändern.
