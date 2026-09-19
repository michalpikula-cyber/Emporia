# Kalkulator kosztów energii

Aplikacja Flask do analizy eksportów zużycia z Emporia Vue i porównywania kosztów taryf Tauron: G11, G12, G12w, G13 oraz G13s.

## Uruchomienie

Wymagany jest Python 3.14 lub nowszy.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Następnie otwórz: <http://127.0.0.1:5000>

## Użycie

1. Wyeksportuj dane godzinowe z Emporia Vue do pliku CSV.
2. Wgraj plik na stronie głównej.
3. Opcjonalnie wybierz zakres dat i taryfę referencyjną.
4. Odczytaj porównanie kosztów, zużycie roczne oraz informacje o brakujących danych.

Aplikacja oczekuje kolumn:

- `Time Bucket (Europe/Budapest)`
- `DomPik-Mains_A (kWhs)`
- `DomPik-Mains_B (kWhs)`
- `DomPik-Mains_C (kWhs)`

Brakujące odczyty są wykrywane i zgłaszane, ale nie są automatycznie uzupełniane. Przy dużych lukach wyniki zużycia rocznego są orientacyjne.

## Ceny taryf

Stawki są przechowywane w `tariff_prices.json`. Można je edytować na stronie `/tariffs`, a następnie zapisać lub wyeksportować. Wszystkie ceny są wartościami netto; VAT jest doliczany w kalkulatorze.

## Symulacja magazynu energii

Niezależny moduł `battery_simulator.py` symuluje przesuwanie zużycia z drogich godzin G13 do tańszych godzin. Przykład:

```powershell
python battery_simulator.py uploads/emporia_history.csv --start-date 2025-09-01 --end-date 2025-09-30 --capacity 10 --charge-power 5 --discharge-power 5 --efficiency 0.90
```

Moduł pokazuje koszt bez magazynu, koszt z magazynem, oszczędność, energię przesuniętą, liczbę cykli i końcowy poziom naładowania. Koszt inwestycji, degradację baterii i końcowy SOC należy interpretować osobno; wynik jest symulacją, nie ofertą instalatora.

Tryb PV (ładowanie z nadwyżki produkcji) wymaga CSV z kolumną `production_kwh`:

```powershell
python battery_simulator.py uploads/emporia_history.csv --pv-production-csv production.csv --export-price 0.25
```

## Zasadność fotowoltaiki

Moduł PV (`/pv`, `pv_feasibility.py`) łączy godzinowe zużycie Emporia z produkcją z PVGIS:

1. Ustaw lokalizację, nachylenie, azymut (0 = południe), straty, **koszt PV (zł/kWp)**, **koszt magazynu (stała kwota zł)** i cenę eksportu w `pv_config.json` lub w formularzu.
2. Otwórz <http://127.0.0.1:5000/pv> i uruchom analizę (wymaga wcześniej zsynchronizowanej historii Emporia).
3. Wynik pokazuje autoconsumption, wartość eksportu, payback, NPV oraz rekomendowaną moc z listy scenariuszy kWp.

Całkowity CAPEX = `(zł/kWp × moc PV) + koszt magazynu` (magazyn tylko gdy włączony). Brutto inwestycji liczone jest z **VAT 8%** (preferencyjna stawka na montaż domowy). Oszczędności na rachunku nadal przeliczane są z **VAT 23%** (energia).

CLI:

```powershell
python pv_feasibility.py uploads/emporia_history.csv --kwp 5,8,10 --tariff G11
```

Produkcja godzinowa jest cache'owana w `uploads/pvgis_cache/`. Gdy PVGIS jest niedostępne, używany jest awaryjny profil klimatyczny PL.
