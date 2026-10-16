# CitiFoxCharger vom Handy oder einem anderen PC bedienen

Das Windows-Tablet betreibt ein einziges Backend. Weitere Geräte öffnen dieselbe Oberfläche und dürfen Laden, Ziel und Wärmeverbraucher bedienen. Die Überwachung läuft auf dem Tablet weiter, wenn die anderen Browser geschlossen sind. Es werden dadurch keine zusätzlichen Škoda-Abfragen erzeugt. Tablet und Backend müssen weiter laufen.

Der Heimnetz-Zugang ist zunächst **ausgeschaltet**. Lokal bleibt die App unter **http://127.0.0.1:8765/** erreichbar. Der zusätzliche Zugang verwendet **HTTPS, ein eigenes Passwort und standardmäßig Port 8766**, gebunden an eine bestimmte private IPv4-Adresse. Es gibt keine offene HTTP-Freigabe im Netzwerk. API-Schlüssel, Gerätekonfiguration und Backend-Neustart lassen sich ausschließlich direkt auf dem Tablet verwalten.

## 1. Auf dem Tablet einrichten

Den aktuellen Programmordner vollständig auf das Tablet kopieren. Python 3.11+ und Windows mit .NET Framework 4.7.2+ sind erforderlich; es werden keine zusätzlichen Python-Pakete heruntergeladen. Den Škoda-Zugang auf diesem Tablet einrichten und **Mit Windows verschlüsselt speichern** aktivieren. Verschlüsselte Schlüsseldateien eines anderen PCs lassen sich nicht einfach übernehmen.

**Netzwerk-Einrichten.cmd** doppelklicken. Die lokale IPv4-Adresse wird anhand der Route zum Smartfox vorgeschlagen; es wird dabei kein UDP-Paket gesendet. Die vorgeschlagene Adresse muss zum Tablet gehören. Ein eigenes Netzwerkpasswort mit mindestens zwölf Zeichen setzen und wiederholen. Es wird verdeckt eingegeben und nur als gesalzener PBKDF2-Hash gespeichert; es ist unabhängig vom MyŠkoda-Schlüssel.

Die Einrichtung erzeugt individuelle Zertifikate, startet das Backend geordnet neu und prüft, ob der Netzwerkzugang tatsächlich gestartet wurde. Eine laufende Zielüberwachung bleibt erhalten, ohne einen neuen Fahrzeugstart auszulösen. Kann der zusätzliche Listener nicht starten, läuft die lokale Überwachung weiter und meldet den Fehler. Die Einrichtung zeigt die genaue Adresse und den Zertifikats-Fingerabdruck an.

Im Router die Tablet-IP möglichst fest reservieren. Bei einer geänderten IP oder nach zwei Jahren muss die Einrichtung erneut ausgeführt und das neue öffentliche Zertifikat auf den zugreifenden Geräten übernommen werden.

## 2. Windows-Firewall freigeben

Auf dem Tablet **Netzwerk-Firewall.cmd** mit Rechtsklick **Als Administrator ausführen** starten. Das ist ein eigener, bewusst ausgeführter Schritt. Die Regel erlaubt nur dieses Python-Programm, die konfigurierte Tablet-IP, den HTTPS-Port, das lokale Subnetz und das Windows-Netzwerkprofil **Privat**. Die Firewall bleibt eingeschaltet; das öffentliche Profil wird nicht freigegeben. Windows muss das eigene Heimnetz als Privat einstufen. [Microsoft: Firewallregeln und Netzwerkprofile](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule).

Bei einem Python-Upgrade mit geändertem Programmpfad die Firewall-Einrichtung erneut ausführen. Eine Router-Portweiterleitung wird nicht eingerichtet und ist für das Heimnetz nicht nötig.

## 3. Auf dem anderen Gerät einmal das Zertifikat übernehmen

Die Einrichtung legt **CitiFoxCharger-Heimnetz.cer** im Programmordner ab, sofern dieser beschreibbar ist. Die Originaldatei liegt immer unter **%LOCALAPPDATA%\Ladezentrale\network**. Diese öffentliche Datei auf das gewünschte Gerät übertragen und ihren SHA-256-Fingerabdruck mit der Anzeige auf dem Tablet vergleichen. Nur das von deinem Tablet erzeugte Zertifikat verwenden. Die privaten Schlüssel- und Datenordner werden dafür nicht benötigt.

- **Windows:** `certmgr.msc` für den aktuellen Benutzer öffnen, unter **Vertrauenswürdige Stammzertifizierungsstellen → Zertifikate** über **Alle Aufgaben → Importieren** die öffentliche CER-Datei importieren. Das ist eine ausdrückliche Vertrauensentscheidung des Benutzers. [Microsoft: private Stammzertifikate](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/trusted-root-certification-authorities-certificate-store).
- **Android:** In den Systemeinstellungen nach „Zertifikat installieren“ suchen und die Datei als **CA-Zertifikat** installieren. Auf Pixel-Geräten liegt die Funktion unter Sicherheit und Datenschutz → Weitere Sicherheitseinstellungen → Verschlüsselung und Anmeldedaten; andere Hersteller können sie anders benennen. [Google: Zertifikate hinzufügen und entfernen](https://support.google.com/pixelphone/answer/2844832?hl=de).

Es wird weder ein Zertifikat automatisch als vertrauenswürdig installiert noch eine Browserwarnung umgangen. Meldet der Browser weiter einen Zertifikatsfehler, IP, Uhrzeit, Fingerabdruck und den Import prüfen. Firefox kann einen eigenen Zertifikatsspeicher verwenden. Der reale Import am konkreten Handy oder Tablet ist hier noch nicht getestet.

## 4. Öffnen und anmelden

Auf dem anderen Gerät die **genau angezeigte HTTPS-Adresse** öffnen, beispielsweise `https://192.168.0.123:8766/` (nur ein Beispiel, keine automatisch festgelegte Tablet-IP). Benutzername **citifox**, dazu das selbst gesetzte Netzwerkpasswort. Das Browser-Anmeldefenster gehört zum verschlüsselten Heimnetz-Zugang.

Nun stehen Ladestand, Leistung, Ziel-Slider, Start/Stopp und die freigegebenen Wärme-/Wallbox-Modi zur Verfügung. Die Zugangsdaten zum Auto bleiben ausschließlich beim Backend. Darstellung Hell/Dunkel/System und das bevorzugte inaktive Sliderziel werden je Browser gespeichert; ein aktives Ladeziel kommt für alle Geräte aus derselben zentralen Sitzung.

## Abschalten und Fehlerbehebung

`Netzwerk-Einrichten.cmd --disable` schaltet den Heimnetz-Zugang nach geordnetem Neustart ab. Der MyŠkoda-Schlüssel muss dafür wie bei anderen Neustarts gespeichert sein. Lokal bleibt die App erreichbar. Die eigene Firewallregel kann anschließend als Administrator mit `Remove-NetFirewallRule -Name CitiFoxCharger-Private-LAN` entfernt werden.

Bei Zeitüberschreitung: Tablet eingeschaltet, Backend aktiv, beide Geräte im gleichen Heimnetz, keine Gäste-WLAN-Isolation, richtige Tablet-IP, Firewallregel und privates Windows-Netzwerkprofil prüfen. Bei „Netzwerkzugang konnte nicht starten“ zeigt die lokale App den Fehler unter Einstellungen → Zugriff im Heimnetz. Fünf falsche Anmeldeversuche sperren die betroffene Client-IP für bis zu eine Minute. Eine Passwortänderung erfolgt durch erneutes Ausführen der Einrichtung; dabei wird auch ein neues Zertifikat erzeugt.

Eigener Datenordner bzw. andere Ports: `Netzwerk-Einrichten.cmd --data-dir "C:\MeinOrdner" --backend-port 8765 --port 8766`. Dieselbe Datenablage muss bereits von der lokalen Instanz verwendet werden. Für die Firewall ebenfalls `--data-dir` angeben, wenn die Standardablage nicht verwendet wird.

## Technischer Prüfstand

Native Windows-Zertifikatserzeugung, verschlüsselte Schlüsselablage und echte TLS-Verbindungen mit vollständiger Zertifikats- und IP-Prüfung sind gerätefrei auf Loopback getestet. Authentifizierung schützt auch Startseite, Skripte, Sitzung und Health-Status. Host, HTTPS-Origin, SameSite-/Secure-Cookie und CSRF bleiben aktiv. Netzwerkclients dürfen keine API-Schlüssel oder Gerätezugänge ändern und das Backend nicht beenden/neustarten. Mehrere Listener teilen denselben Dienst und Monitor. Keine Freigabe am aktuellen PC und keine Änderung am Windows-Vertrauensspeicher wurden für die Prüfung vorgenommen. Eine Verbindung von einem zweiten physischen Gerät am Wandtablet steht noch aus.
