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
