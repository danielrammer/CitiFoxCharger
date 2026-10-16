# Prüfbericht · Version 1.2.1

Stand: **16.10.2026**, Windows / Python 3.14.

## Korrektur nach unbestätigtem Start

Im realen Fehlerfall stand die Wallbox auf M+ mit „ladebereit“, Stromvorgabe 11,5 A und 0 kW. Škoda hatte den Start angenommen, lieferte aber zunächst nur eine vor dem Start erfasste Meldung CHARGING_INTERRUPTED mit SOC 88 %. Daraus folgt keine bestätigte Ladung und keine gesicherte Ursache der MyŠkoda-Meldung über erneute Freigabe bzw. Klimatisierung. Es wurden zur Diagnose keine zusätzlichen Start- oder Klimabefehle gesendet.

Zwei App-Probleme wurden korrigiert: Eine aktive Zielüberwachung sperrte jeden erneuten bewussten Start dauerhaft; außerdem konnte der vor der M+-Schaltung gespeicherte A+-Cache einen falschen Wechselhinweis stehen lassen. Neu sind ein expliziter Wiederholungsweg ohne automatische Startwiederholung und eine Prüfung der Ladebereitschaft vor dem Fahrzeugbefehl.

Neue Tests prüfen frische Geräteverbindung, Status/Stromvorgabe und Anlaufzeit ohne zusätzliche Schreibaufrufe. Fehlende Freigabe, lokale Authentifizierung, Wallboxfehler oder verlorenes M+ verhindern den Fahrzeugstart. Die Wiederholung hält dasselbe Prozentziel fest und ist bei Ladung, alten Gerätestatusdaten oder laufendem Stopp gesperrt. Ein erfolgreicher simulierter Test ersetzt keinen realen Nachweis, dass die ursprüngliche MyŠkoda-Störung damit beseitigt ist.

## Automatisierte Prüfung

**100 Tests erfolgreich**, zusätzlich JavaScript-Syntaxprüfung. Automatisierte Tests verwenden simulierte Geräte und lokale Test-Sockets, keine echten Fahrzeug- oder Wallboxbefehle.

Geprüft sind Modbus-Protokoll und Fehlerantworten, genaue PDU-Adressen, verlustfreie uint64-Darstellung, ungültige Registerlesebereiche, Škoda-Schema und FIN-Zuordnung, fehlende oder ungültige SOC-Werte und Zeitstempel, fehlende Fähigkeiten, API-Fehler, RateLimit und Retry-After, zwei reservierte Befehlsaufrufe, Abfrageabstände beim Laden und im Leerlauf sowie manuelle Neuabfrage.

Die Ladesteuerung ist geprüft auf M+ vor Fahrzeugstart, Sperre bei fehlender Freigabe oder Rücklesebestätigung, A+-Rückkehr nach Startfehler, gültigen Zielwert, SOC-Stopp ohne Browser, unmittelbaren manuellen Stopp unterhalb des Ziels, neuere Fahrzeugmeldungen zur Bestätigung, begrenzte Wiederholung unsicherer Stopps, Abschluss anhand neuer gestoppter Telemetrie, neue Sitzung ohne alten Stoppwunsch, Wiederaufnahme ohne Startwiederholung und Bindung an die ursprüngliche FIN.

Außerdem geprüft: Windows-DPAPI-Rundlauf, keine Schlüssel in Statusantworten, Konfigurationsvalidierung, atomische Speicherung, aktive Sitzung schützt vor Schlüsselentfernung und Gerätewechsel, Origin/Host/CSRF/Sitzung/Sicherheitsheader, ausschließlich Loopback, keine Modbus-Schreibroute, Prozesssperre pro Datenordner und Brave-Start einschließlich Vollbildargument und Standardbrowser-Fallback.

## Reale Geräte und Grenzen

SMARTFOX Pro 2, Firmware **EM3 00.01.16.08**, Charger 1 unter 192.168.0.81 waren lesend erreichbar. Die angefragten Modbus-Adressen 40999, 41015 und 42111 wurden als Rohwerte bestätigt. Ihre ursprünglich vermutete physikalische Bedeutung wurde nicht übernommen. Die lokale Herstelleranzeige lieferte A+ und aktive Leistungen wie 1,37 / 2,93 / 2,29 kW.

Nach ausdrücklicher Benutzerfreigabe der lokalen Websteuerung wurde **A+ → M+ → A+** real geschaltet und jeweils zurückgelesen. Der Test ließ Smartfox auf A+ zurück. Es gab dabei keinen neuen Škoda-Start-/Stoppbefehl. Die Befehlsform stammt aus der tatsächlich ausgelieferten Herstelleroberfläche; eine öffentliche API-Spezifikation dafür fehlt. Modbus bleibt nur lesend.

Am konkreten Citigo wurden SOC und die Fähigkeiten startCharging/stopCharging bereits praktisch bestätigt. Nach einem vom Benutzer ausgelösten Start bestätigte dieser die tatsächliche Ladung. Die neue komplette kombinierte Sitzung einschließlich automatischer Prozentziel-Abschaltung wurde noch nicht real bis zum Ende durchlaufen. Simulation ersetzt diesen Nachweis nicht.

Die Cloud kann alte Fahrzeugmeldungen liefern. Die offizielle geprüfte API bietet keinen dokumentierten zusätzlichen Weckbefehl. Der Zielstopp ist keine native Fahrzeug-Ladegrenze; Melde- und Befehlsverzögerungen können zum Überschreiten führen. Native Smartfox-M+-Zeit-/Energiegrenzen bleiben unverändert wirksam. Der PC und das Backend müssen für die Überwachung laufen.

## Oberfläche und Tablet

Die Betriebsansicht trennt Einstellungen und Diagnose vom täglichen Betrieb. Angezeigt werden SOC, reale Ladeleistung, Status, Prozentziel, manuelles Laden und Stopp. Der Slider wurde in der getrennten Browser-Vorschau bedient. Ohne Fahrzeugzugang zeigt die Vorschau keinen erfundenen SOC.

Für ein im Hochformat gedrehtes 1280 × 800-Panel gibt es ein eigenes Layout für 800 × 1280 mit größeren Schriften, 64 Pixel hohen Aktionstasten und einem 36 Pixel großen Slidergriff. Kleinere Hochformatfenster verwenden eine kompaktere Variante. Brave ist auf diesem Windows-PC am üblichen Installationsort vorhanden. Der Launcher ist automatisiert auf korrekte Argumente getestet; das konkrete Tablet und dessen Windows-Skalierung sind hier nicht angeschlossen.

Die Ladeanimation beruht auf frischer lokaler Leistung über 0,1 kW, ersatzweise auf einem frischen CHARGING-Fahrzeugstatus. Ein HTTP-202-Ergebnis allein aktiviert sie nicht. Die Animation verändert den dargestellten SOC nicht und lässt sich durch die Systemeinstellung für reduzierte Bewegung unterdrücken.

## Hintergrundbetrieb

Zusätzlich simuliert geprüft: Backend fordert bei einer manuellen Sitzung SystemRequired und ExecutionRequired an, fordert nie den Bildschirm an, stoppt ohne Browser bei 90 %, stellt A+ wieder her und gibt nach bestätigtem Fahrzeugstopp die Energieanforderungen frei. Teilfehler und Prozessende schließen alle angelegten Handles. Ein Beenden über die Oberfläche erfordert die lokale Sitzung plus CSRF und ist während einer aktiven Ladeüberwachung gesperrt.

Der echte getrennte Windows-Prozess wurde ohne Fahrzeugzugang und mit absichtlich nicht passender Smartfox-Firmwarekonfiguration gestartet, wodurch Schreibbefehle gesperrt blieben. Der Starterprozess beendete sich, das Backend lief weiter, ein zweiter Start verwendete dieselbe Instanz, und die lokale Beenden-Route fuhr den Testprozess kontrolliert herunter.

Die Windows-Power-API nahm auf diesem PC beide Anforderungen an; anschließend wurden sie freigegeben. Die ergänzende Abfrage mit powercfg /requests verlangte erhöhte Rechte und lieferte hier keinen zusätzlichen Nachweis. Der PC meldete Akkubetrieb. Das konkrete Wandtablet mit ausgeschaltetem Bildschirm wurde hier nicht real getestet. Auf Modern-Standby-Geräten kann Windows die Anforderungen im Akkubetrieb zeitlich begrenzen; Netzbetrieb und passende Windows-Einstellungen bleiben Voraussetzung für den Wandbetrieb.

Kein Langzeittest über Windows-Energiesparzustände oder Mobilfunkabschaltungen; kein Windows-Dienst und kein Autostart eingerichtet. Expliziter Standby oder Ausschalten wird nicht übergangen.
