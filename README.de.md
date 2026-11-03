# IterWave — KI-gestützter Arbeitsbereich für visuelle Inhalte

[English](README.md) · [Vereinfachtes Chinesisch](README.zh-CN.md) · [Russisch](README.ru.md) · [Japanisch](README.ja.md) · [Französisch](README.fr.md) · [Deutsch](README.de.md)

> **Frühe Vorschauversion** — Ein Open-Source-Projekt in aktiver Entwicklung. Der tatsächliche Funktionsstand ergibt sich aus Code, Tests und Prüfungen in der Zielumgebung.

IterWave ist ein Arbeitsbereich für Creator, die bild- und textbasierte Inhalte auf mehreren Plattformen veröffentlichen. Themenfindung, Inhaltsplanung, plattformspezifische Produktion, Qualitätskontrolle, menschliche Veröffentlichungsfreigabe und Leistungsanalyse werden zu einem nachvollziehbaren Workflow verbunden.

## Grundprinzipien

- **Visuelle Inhalte im Mittelpunkt:** Themen, Texte, Bildkarten, Qualitätskontrolle und operative Auswertung.
- **Planbasierte Produktion:** Gesamtplan, plattformspezifische Teilpläne, Phasenabhängigkeiten und Ausführungsstatus steuern die Produktion.
- **Veröffentlichung unter menschlicher Kontrolle:** Die Automatisierung kann Entwürfe vorbereiten und speichern, klickt aber niemals selbst auf „Veröffentlichen“.
- **Nachvollziehbarkeit:** Versionen von Plänen und Inhalten, Aufgabenstatus, Ausführungsergebnisse und Analysegrundlagen werden dokumentiert.
- **Lokale Kontrolle:** Betreiber behalten die Kontrolle über Zugangsdaten, Konfiguration, Datenbank und Inhaltsressourcen.

## Workflow

```text
Themenbibliothek / Account-Positionierung / Trendermittlung
                         ↓
                    Gesamtplan und Teilpläne
                         ↓
                Phasensteuerung und Abhängigkeiten
                         ↓
             Plattformgerechte Texte und Bildkarten
                         ↓
                    Qualitätsprüfung und Entwurf
                         ↓
                 Veröffentlichung durch Nutzerfreigabe
                         ↓
              Datenerfassung → Auswertung → Iteration
```

## Unterstützte Plattformen

Xiaohongshu, Douyin, Kuaishou und WeChat Channels (Bild-Text-Inhalte). Die tatsächlichen Möglichkeiten hängen von Plattform-Schnittstellen, Kontoberechtigungen und Änderungen der Benutzeroberfläche ab. Reale Kontenabläufe müssen in der Zielumgebung geprüft werden.

## Technologie-Stack

| Ebene | Technologie |
|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic v2 |
| Datenspeicherung | SQLite, versionierte Schema-Migrationen |
| Planung und Ausführung | APScheduler, globale Aufgabenwarteschlange, isolierte Worker |
| Browser-Automatisierung | Playwright (Entwurfsspeicherung und schreibgeschützte Datenerfassung) |
| Frontend | React 18, TypeScript, Vite |
| Bildverarbeitung | Pillow, OpenCV, NumPy |

## Schnellstart

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn src.api.main:app --reload
```

Richten Sie die Konfiguration anhand von `config/config.yaml` ein. API-Schlüssel, Cookies und Tokens gehören ausschließlich in die lokale Umgebungskonfiguration und dürfen nicht ins Repository gelangen.

### Frontend

```bash
cd web
npm install
npm run dev
npm test
npm run build
```

## Bereitstellung und Sicherheit

- Legen Sie vor der ersten Bereitstellung ein starkes, zufälliges `ITERWAVE_BOOTSTRAP_TOKEN` fest und richten Sie über den Initialisierungsprozess das endgültige Zugriffspasswort ein. Entfernen Sie das Bootstrap-Token danach.
- Öffentliche Bereitstellungen müssen HTTPS verwenden. Aktivieren Sie `ITERWAVE_ALLOW_LOCAL_BOOTSTRAP=1` nicht in öffentlichen Umgebungen.
- Beschränken Sie den Zugriff auf Konfigurations-, Datenbank- und Sitzungsverzeichnisse auf den Betriebssystembenutzer des Dienstes.
- APScheduler unterstützt noch keine prozessübergreifende Leader-Wahl. Verwenden Sie genau einen Uvicorn-Worker und konfigurieren Sie keine mehreren `--workers`.
- Die globale Warteschlange verwendet standardmäßig einen Worker und serielle FIFO-Ausführung. Aktivieren Sie Parallelität zwischen Kontogruppen erst nach Bewertung der Auswirkungen eines harten Prozess-Timeouts.
- Durch harte Timeouts oder Worker-Abbrüche unterbrochene Aufgaben werden nicht automatisch erneut gestartet. Prüfen Sie zuerst das Ergebnis auf der Plattform.
- Automatisierte Kommentare, Antworten und Follows sind standardmäßig deaktiviert. Prüfen Sie vor der Aktivierung Plattformregeln und Kontoberechtigungen.
- Die Playwright-Automatisierung endet beim Speichern des Entwurfs. Die tatsächliche Veröffentlichung muss vom Nutzer bestätigt werden.

## Repository-Struktur

```text
src/
  core/       Zustandsautomaten, Planversionen, Aufgabenwarteschlange, Scheduling, Recovery, Analysen
  adapters/   Plattformadapter
  api/        FastAPI-Routen und Anwendungslebenszyklus
  vendor/     Drittanbieter-Code unter Beibehaltung der Upstream-Lizenz
web/          React-Frontend
migrations/   Datenbankschema-Migrationen
tests/        Backend-Tests
config/       Konfigurationsvorlagen ohne echte Geheimnisse
```

## Entwicklungs- und Prüfstatus

Das Projekt befindet sich weiterhin in Entwicklung. Erfolgreiche Repository-Tests bedeuten nicht, dass die Integration mit realen Plattformen vollständig Ende-zu-Ende geprüft wurde. Folgende Punkte müssen in einer Zielumgebung mit den erforderlichen Konten, Netzwerkzugriffen und Modell-Zugangsdaten validiert werden:

- Reale Anmeldung, Entwurfsspeicherung und Datenauslesung auf allen vier Plattformen;
- Vollständiger Modellablauf mit Texterstellung, Bildkartenproduktion und Qualitätsprüfung;
- Der vollständige Kreislauf von der Datenerfassung nach der Veröffentlichung über die Auswertung bis zur Ausführung der nächsten Strategie.

Codebeiträge sollten passende Tests enthalten. Zugangsdaten, Nutzerdaten und Zustände realer Konten dürfen weder in Logs noch im Repository gespeichert werden.

## Drittanbieter-Code und Lizenz

Einige Plattformadapter basieren auf kopiertem und angepasstem Code aus [Easel](https://github.com/kaichen/Easel) (Apache License 2.0). Hinweise zu Urheberrecht und Lizenz stehen in `src/vendor/easel/LICENSE` und `src/vendor/easel/NOTICE`.

Dieses Projekt steht unter der Apache License 2.0. Siehe [LICENSE](LICENSE).
