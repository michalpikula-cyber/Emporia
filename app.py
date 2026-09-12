from flask import Flask, render_template, request, jsonify
import pandas as pd
import numpy as np
from datetime import datetime
import plotly.graph_objs as go
import plotly.utils
import json
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

PRICES_FILE = 'tariff_prices.json'

class TariffCalculator:
    def __init__(self):
        # Inicjalizacja atrybutów, aby uniknąć błędów AttributeError
        self.prices = {}
        self.last_update = "Inicjalizacja 2026"
        self.vat_rate = 0.23
        self.load_prices()
    
    def load_prices(self):
        """Wczytuje ceny z pliku JSON lub ustawia domyślne taryfy 2026."""
        try:
            if os.path.exists(PRICES_FILE):
                with open(PRICES_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    raw_prices = data.get('prices', {})
                    # Konwersja na float, aby zapobiec błędom obliczeniowym
                    self.prices = {k: float(v) for k, v in raw_prices.items()}
                    self.last_update = data.get('last_update', "Wczytano z pliku")
            else:
                self.set_default_prices()
        except Exception:
            self.set_default_prices()
    
    def set_default_prices(self):
        """Oficjalne stawki netto Tauron na rok 2026 z dokumentów PDF."""
        self.prices = {
            # G11 - Energia czynna (Sprzedaż) [doc 5, str. 2]
            'g11_energy': 0.4970,
            'g11_ss': 0.2464,  # [doc 4, str. 3]
            
            # G12 - Dwustrefowa [doc 5, str. 2 + doc 4, str. 3]
            'g12_energy_day': 0.5430, 
            'g12_energy_night': 0.4130,
            'g12_ss_day': 0.2841, 
            'g12_ss_night': 0.0558,
            
            # G12w - Weekendowa [doc 5, str. 2 + doc 4, str. 3]
            'g12w_energy_day': 0.6220, 
            'g12w_energy_night': 0.4130,
            'g12w_ss_day': 0.3298, 
            'g12w_ss_night': 0.0512,
            
            # G13 - Trójstrefowa [doc 5, str. 2 + doc 4, str. 3]
            'g13_energy_przed': 0.4718,  # szczyt przedpołudniowy 7:00-13:00
            'g13_energy_po': 0.7830,     # szczyt popołudniowy (lato 19-22, zima 16-21)
            'g13_energy_reszta': 0.4260, # pozostałe godziny
            'g13_ss_przed': 0.2203, 
            'g13_ss_po': 0.3898,
            'g13_ss_reszta': 0.0392,
            

            # ========== G13s - Tanie Godziny ==========
            # Ceny energii netto (zł/kWh) - z dokumentu ofertowego
            'g13s_energy_lato_roboczy_szczyt': 0.7092,      # 7-9, 18-22 (lato, dzień roboczy)
            'g13s_energy_lato_roboczy_pozaszczyt': 0.2750,  # 10-17 (lato, dzień roboczy)
            'g13s_energy_lato_wolny_szczyt': 0.2867,        # 7-9, 18-22 (lato, dzień wolny)
            'g13s_energy_lato_wolny_pozaszczyt': 0.1130,    # 10-17 (lato, dzień wolny)
            'g13s_energy_lato_noc': 0.5050,                 # 0-7, 22-24 (lato)
            
            'g13s_energy_zima_roboczy_szczyt': 0.7092,      # 7-10, 16-22 (zima, dzień roboczy)
            'g13s_energy_zima_roboczy_pozaszczyt': 0.5550,  # 11-15 (zima, dzień roboczy)
            'g13s_energy_zima_wolny_szczyt': 0.4275,        # 7-10, 16-22 (zima, dzień wolny)
            'g13s_energy_zima_wolny_pozaszczyt': 0.3350,    # 11-15 (zima, dzień wolny)
            'g13s_energy_zima_noc': 0.4950,                 # 0-7, 22-24 (zima)
            
            # Składniki sieciowe G13s netto (zł/kWh) - z taryfy dystrybucyjnej
            'g13s_ss_lato_roboczy_pozaszczyt': 0.1000,
            'g13s_ss_lato_roboczy_szczyt': 0.2842,
            'g13s_ss_lato_wolny_pozaszczyt': 0.0400,
            'g13s_ss_lato_wolny_szczyt': 0.1176,
            'g13s_ss_zima_roboczy_pozaszczyt': 0.1999,
            'g13s_ss_zima_roboczy_szczyt': 0.3332,
            'g13s_ss_zima_wolny_pozaszczyt': 0.1200,
            'g13s_ss_zima_wolny_szczyt': 0.1960,
            'g13s_ss_noc': 0.1094,  # noc cały rok
            
            # Opłata handlowa G13s (stała)
            'oplata_handlowa_g13s': 19.10,  # zł/miesiąc netto


            # Opłaty stałe i dodatkowe - Netto [doc 4, str. 3-4]
            'skladnik_staly_ss': 10.86,  # zł/miesiąc (układ 3-fazowy)
            'skladnik_staly_ss_g12as': 21.72,  # zł/miesiąc dla G12as (układ 3-fazowy)
            'oplata_abonamentowa': 4.56, # zł/miesiąc dla G13 zgodnie z fakturą
            'oplata_mocowa': 24.05,      # zł/miesiąc (dla zużycia > 2800 kWh/rok)
            'stawka_jakosciowa': 0.0332, # zł/kWh
            'oplata_oze': 0.0073,        # zł/kWh (7.30 zł/MWh)
            'oplata_kogeneracyjna': 0.0030  # zł/kWh (3.00 zł/MWh)
        }
        self.last_update = "Taryfa Tauron 2026 (Oficjalna - 17.12.2025)"

    def is_holiday(self, date):
        """Dni ustawowo wolne w 2026 r."""
        holidays_2026 = ["2026-01-01", "2026-01-06", "2026-04-05", "2026-04-06", "2026-05-01", "2026-05-03", "2026-05-24", "2026-06-04", "2026-08-15", "2026-11-01", "2026-11-11", "2026-12-25", "2026-12-26"]
        return date.strftime("%Y-%m-%d") in holidays_2026

    def get_g13_zone(self, date, hour):
        """Wyznacza strefę dla taryfy G13 zgodnie z wyciągiem z taryfy[cite: 35, 36]."""
        # Soboty, niedziele i święta to zawsze strefa 'pozostałe' (reszta) [cite: 37]
        if self.is_holiday(date) or date.weekday() >= 5:
            return "reszta"
        
        # Szczyt przedpołudniowy: 7:00 - 13:00 [cite: 36]
        if 7 <= hour < 13:
            return "przed"
            
        is_summer = 4 <= date.month <= 9
        # Szczyt popołudniowy: Lato (19-22), Zima (16-21) [cite: 36]
        if is_summer:
            if 19 <= hour < 22: return "po"
        else:
            if 16 <= hour < 21: return "po"
            
        return "reszta"
    
    def get_g13s_zone(self, date, hour, month, weekday):
        """
        Zwraca strefę G13s dla uproszczonego modelu 3-strefowego.
        Na podstawie tabel dystrybucyjnych.
        """
        is_summer = 4 <= month <= 9
        is_workday = weekday < 5 and not self.is_holiday(date)
        
        # Strefa nocna (21:00-7:00) - taka sama dla wszystkich
        if hour >= 21 or hour < 7:
            if is_summer:
                return 'lato_noc' if is_workday else 'lato_noc'  # noc taka sama
            else:
                return 'zima_noc' if is_workday else 'zima_noc'  # noc taka sama
        
        # Określ strefę dzienną
        if is_summer:
            # Lato
            if is_workday:
                # Dzień roboczy lato: pozaszczyt 9-17, szczyt 7-9 i 17-21
                if 9 <= hour < 17:
                    return 'lato_roboczy_pozaszczyt'
                else:  # 7-9 lub 17-21
                    return 'lato_roboczy_szczyt'
            else:
                # Dzień wolny lato
                if 9 <= hour < 17:
                    return 'lato_wolny_pozaszczyt'
                else:  # 7-9 lub 17-21
                    return 'lato_wolny_szczyt'
        else:
            # Zima
            if is_workday:
                # Dzień roboczy zima: pozaszczyt 10-15, szczyt 7-10 i 15-21
                if 10 <= hour < 15:
                    return 'zima_roboczy_pozaszczyt'
                else:  # 7-10 lub 15-21
                    return 'zima_roboczy_szczyt'
            else:
                # Dzień wolny zima
                if 10 <= hour < 15:
                    return 'zima_wolny_pozaszczyt'
                else:  # 7-10 lub 15-21
                    return 'zima_wolny_szczyt'

    def detect_data_anomalies(self, df):
        """
        Wykrywa anomalie w danych Emporia:
        1. Brakujące wiersze (dziury czasowe)
        2. Wiersze z podejrzanie niskimi wartościami (utrata połączenia)
        3. Nagłe spadki zużycia
        """
        anomalies = {
            'time_gaps': [],
            'low_consumption_periods': [],
            'total_anomaly_hours': 0,
            'data_completeness': 100.0
        }
        
        # 1. Detekcja dziur czasowych
        df = df.sort_values('timestamp')
        df['time_diff'] = df['timestamp'].diff()
        
        # Znajdź luki dłuższe niż 2 godziny
        time_gaps = df[df['time_diff'] > pd.Timedelta(hours=2)]
        for _, gap in time_gaps.iterrows():
            gap_start = gap['timestamp'] - gap['time_diff']
            gap_end = gap['timestamp']
            gap_hours = gap['time_diff'].total_seconds() / 3600
            
            anomalies['time_gaps'].append({
                'start': gap_start.strftime("%Y-%m-%d %H:%M"),
                'end': gap_end.strftime("%Y-%m-%d %H:%M"),
                'hours': round(gap_hours, 1),
                'type': 'missing_data'
            })
            anomalies['total_anomaly_hours'] += gap_hours
        
        # 2. Detekcja podejrzanie niskiego zużycia
        # Normalne zużycie w godzinie to zazwyczaj > 0.01 kWh (10 Wh)
        suspicious_threshold = 0.01  # kWh
        
        # Znajdź okresy z podejrzanie niskim zużyciem (dłuższe niż 2 godziny)
        df['is_suspicious'] = df['total_usage'] < suspicious_threshold
        
        # Grupuj ciągłe okresy z niskim zużyciem
        suspicious_groups = []
        current_group = None
        
        for idx, row in df.iterrows():
            if row['is_suspicious']:
                if current_group is None:
                    current_group = {
                        'start': row['timestamp'],
                        'end': row['timestamp'],
                        'count': 1,
                        'avg_usage': row['total_usage']
                    }
                else:
                    current_group['end'] = row['timestamp']
                    current_group['count'] += 1
                    current_group['avg_usage'] = (current_group['avg_usage'] + row['total_usage']) / 2
            else:
                if current_group is not None and current_group['count'] >= 2:  # Co najmniej 2 godziny
                    suspicious_groups.append(current_group)
                current_group = None
        
        # Dodaj ostatnią grupę jeśli istnieje
        if current_group is not None and current_group['count'] >= 2:
            suspicious_groups.append(current_group)
        
        for group in suspicious_groups:
            anomalies['low_consumption_periods'].append({
                'start': group['start'].strftime("%Y-%m-%d %H:%M"),
                'end': group['end'].strftime("%Y-%m-%d %H:%M"),
                'hours': group['count'],
                'avg_usage': round(group['avg_usage'], 4),
                'type': 'low_consumption'
            })
            anomalies['total_anomaly_hours'] += group['count']
        
        # 3. Oblicz kompletność danych
        total_hours = (df['timestamp'].max() - df['timestamp'].min()).total_seconds() / 3600
        if total_hours > 0:
            anomalies['data_completeness'] = round((1 - (anomalies['total_anomaly_hours'] / total_hours)) * 100, 1)
        
        anomalies['has_anomalies'] = len(anomalies['time_gaps']) > 0 or len(anomalies['low_consumption_periods']) > 0
        
        return anomalies

    def analyze_usage(self, csv_file_path, start_date=None, end_date=None, reference_tariff='G11'):
        try:

            df = pd.read_csv(csv_file_path)
            time_col = 'Time Bucket (Europe/Budapest)'
            df['timestamp'] = pd.to_datetime(df[time_col], format='%m/%d/%Y %H:%M:%S')
            
            # Czyszczenie danych
            val_cols = [c for c in df.columns if 'kWhs' in c]
            for c in val_cols:
                df[c] = pd.to_numeric(df[c].astype(str).str.replace(',', '.'), errors='coerce').fillna(0)
            
            # Sumujemy główne fazy
            df['total_usage'] = df[['DomPik-Mains_A (kWhs)', 'DomPik-Mains_B (kWhs)', 'DomPik-Mains_C (kWhs)']].sum(axis=1)
            
            # ========== DETEKCJA ANOMALII ==========
            anomalies = self.detect_data_anomalies(df.copy())


            df = pd.read_csv(csv_file_path)
            # Obsługa amerykańskiego formatu daty z Emporii
            time_col = 'Time Bucket (Europe/Budapest)'
            df['timestamp'] = pd.to_datetime(df[time_col], format='%m/%d/%Y %H:%M:%S')
            
            # ========== ANALIZA DZIUR W DANYCH ==========
            # Sortuj chronologicznie
            df = df.sort_values('timestamp')
            
            # Oblicz odstępy między pomiarami
            df['time_diff'] = df['timestamp'].diff()
            
            # Znajdź dziury dłuższe niż 2 godziny
            data_gaps = df[df['time_diff'] > pd.Timedelta(hours=2)]
            
            gap_info = {
                'total_gaps': len(data_gaps),
                'max_gap_hours': 0,
                'total_gap_hours': 0,
                'gap_periods': []
            }
            
            if len(data_gaps) > 0:
                gap_info['max_gap_hours'] = data_gaps['time_diff'].max().total_seconds() / 3600
                gap_info['total_gap_hours'] = data_gaps['time_diff'].sum().total_seconds() / 3600
                
                # Zapis okresów z dziurami
                for _, gap in data_gaps.iterrows():
                    gap_start = gap['timestamp'] - gap['time_diff']
                    gap_end = gap['timestamp']
                    gap_info['gap_periods'].append({
                        'start': gap_start.strftime("%Y-%m-%d %H:%M"),
                        'end': gap_end.strftime("%Y-%m-%d %H:%M"),
                        'hours': gap['time_diff'].total_seconds() / 3600
                    })
            
            # Czyszczenie danych - "No CT" zamieniamy na 0
            val_cols = [c for c in df.columns if 'kWhs' in c]
            for c in val_cols:
                df[c] = pd.to_numeric(df[c].astype(str).str.replace(',', '.'), errors='coerce').fillna(0)
            
            # Sumujemy główne fazy
            df['total_usage'] = df[['DomPik-Mains_A (kWhs)', 'DomPik-Mains_B (kWhs)', 'DomPik-Mains_C (kWhs)']].sum(axis=1)
            
            if start_date and end_date:
                start_dt = pd.to_datetime(start_date)
                end_dt = pd.to_datetime(end_date) + pd.Timedelta(days=1)
                mask = (df['timestamp'] >= start_dt) & (df['timestamp'] < end_dt)
                df = df[mask].copy()

            if df.empty:
                return {"error": "Brak odczytów w wybranym zakresie dat."}

            # Jakość danych musi dotyczyć wybranego zakresu, nie całego pliku.
            anomalies = self.detect_data_anomalies(df.copy())

            df['hour'] = df['timestamp'].dt.hour
            df['month'] = df['timestamp'].dt.month
            df['weekday'] = df['timestamp'].dt.weekday
            
            # Podział na strefy dla G12/G12w
            df['is_n'] = df['hour'].apply(lambda h: (h >= 22 or h < 6) or (13 <= h < 15))
            df['is_w'] = df.apply(lambda r: r['is_n'] or r['weekday'] >= 5 or self.is_holiday(r['timestamp']), axis=1)
            
            # Podział na strefy dla G12as (22:00-6:00 = noc)
            df['is_n_g12as'] = df['hour'].apply(lambda h: h >= 22 or h < 6)
            
            # Obliczenie zużycia z poprzedniego roku dla G12as
            # Zakładamy, że mamy dane od września 2024
            df_prev_year = df[df['timestamp'] < pd.to_datetime('2025-01-01')].copy()
            if len(df_prev_year) > 0:
                # Obliczamy średnie zużycie dzienne z poprzedniego roku
                prev_year_daily_avg = df_prev_year.groupby(df_prev_year['timestamp'].dt.date)['total_usage'].sum().mean()
                # Dla każdego dnia w analizowanym okresie sprawdzamy czy przekraczamy limit
                df['date'] = df['timestamp'].dt.date
                daily_usage = df.groupby('date')['total_usage'].sum()
                # Proste podejście: zakładamy równy limit dla każdego dnia
                df['exceeds_prev_year'] = df['date'].map(lambda d: daily_usage.get(d, 0) > prev_year_daily_avg)
            else:
                df['exceeds_prev_year'] = False
            
            period_days = (df['timestamp'].max() - df['timestamp'].min()).days + 1
            # Stałe opłaty rozliczamy w miesięcznych jednostkach, nie jako
            # ułamek miesiąca wynikający z liczby godzinnych odczytów.
            months = max(1, round(period_days / 30.44))
            tariffs_data = {}

            # Pętla po wszystkich taryfach
            for t in ['G11', 'G12', 'G12w', 'G13', 'G13s']:
                if t == 'G11':
                    e = df['total_usage'].sum() * self.prices['g11_energy']
                    ss = df['total_usage'].sum() * self.prices['g11_ss']
                    
                elif t == 'G12':
                    e = df.apply(lambda r: r['total_usage'] * (
                        self.prices['g12_energy_night'] if r['is_n'] 
                        else self.prices['g12_energy_day']
                    ), axis=1).sum()
                    ss = df.apply(lambda r: r['total_usage'] * (
                        self.prices['g12_ss_night'] if r['is_n'] 
                        else self.prices['g12_ss_day']
                    ), axis=1).sum()

                elif t == 'G13s':
                    # Oblicz strefy G13s dla każdego wiersza
                    df['z13s'] = df.apply(lambda r: self.get_g13s_zone(
                        r['timestamp'], r['hour'], r['month'], r['weekday']
                    ), axis=1)
                    
                    # Oblicz koszt energii
                    energy_cost = df.apply(lambda r: r['total_usage'] * self.prices[f'g13s_energy_{r["z13s"]}'], axis=1).sum()
                    
                    # Oblicz składnik sieciowy (różne mapowanie)
                    ss_mapping = {
                        'lato_roboczy_pozaszczyt': 'lato_roboczy_pozaszczyt',
                        'lato_roboczy_szczyt': 'lato_roboczy_szczyt',
                        'lato_wolny_pozaszczyt': 'lato_wolny_pozaszczyt',
                        'lato_wolny_szczyt': 'lato_wolny_szczyt',
                        'lato_noc': 'noc',
                        'zima_roboczy_pozaszczyt': 'zima_roboczy_pozaszczyt',
                        'zima_roboczy_szczyt': 'zima_roboczy_szczyt',
                        'zima_wolny_pozaszczyt': 'zima_wolny_pozaszczyt',
                        'zima_wolny_szczyt': 'zima_wolny_szczyt',
                        'zima_noc': 'noc'
                    }
                    
                    ss_cost = df.apply(lambda r: r['total_usage'] * self.prices[f'g13s_ss_{ss_mapping[r["z13s"]]}'], axis=1).sum()
                    
                    # DODATKOWA OPŁATA: opłata handlowa G13s (zamiast opłaty abonamentowej?)
                    # Uwaga: G13s ma OPŁATĘ HANDLOWĄ 19.10 zł zamiast standardowej opłaty abonamentowej
                    e = energy_cost
                    ss = ss_cost
                    
                    # Opłaty dodatkowe (takie same jak inne)
                    v_oplaty = df['total_usage'].sum() * (
                        self.prices['stawka_jakosciowa'] + 
                        self.prices['oplata_oze'] + 
                        self.prices['oplata_kogeneracyjna']
                    )
                    
                    # STAŁE OPŁATY DLA G13s (inne niż dla standardowych taryf!)
                    f_oplaty = months * (
                        self.prices['skladnik_staly_ss'] +  # składnik stały sieciowy
                        self.prices['oplata_handlowa_g13s'] +  # OPŁATA HANDLOWA G13s (zamiast abonamentowej!)
                        self.prices['oplata_mocowa']
                    )

                elif t == 'G12w':
                    e = df.apply(lambda r: r['total_usage'] * (
                        self.prices['g12w_energy_night'] if r['is_w'] 
                        else self.prices['g12w_energy_day']
                    ), axis=1).sum()
                    ss = df.apply(lambda r: r['total_usage'] * (
                        self.prices['g12w_ss_night'] if r['is_w'] 
                        else self.prices['g12w_ss_day']
                    ), axis=1).sum()
                
                elif t == 'G13':
                    # Logika trójstrefowa G13
                    df['z13'] = df.apply(lambda r: self.get_g13_zone(r['timestamp'], r['hour']), axis=1)
                    e = df.apply(lambda r: r['total_usage'] * self.prices[f'g13_energy_{r["z13"]}'], axis=1).sum()
                    ss = df.apply(lambda r: r['total_usage'] * self.prices[f'g13_ss_{r["z13"]}'], axis=1).sum()
                
                # Dodanie opłat dodatkowych i stałych
                v_oplaty = df['total_usage'].sum() * (
                    self.prices['stawka_jakosciowa'] + 
                    self.prices['oplata_oze'] + 
                    self.prices['oplata_kogeneracyjna']
                )
                
                # G13s ma opłatę handlową zamiast abonamentowej.
                skladnik_staly = self.prices.get('skladnik_staly_ss_g12as', 21.72) if t == 'G12as' else self.prices['skladnik_staly_ss']
                oplata_stala_dodatkowa = (
                    self.prices['oplata_handlowa_g13s'] if t == 'G13s'
                    else self.prices['oplata_abonamentowa']
                )
                
                f_oplaty = months * (
                    skladnik_staly + 
                    oplata_stala_dodatkowa +
                    self.prices['oplata_mocowa']
                )
                
                total_brutto = round((e + ss + v_oplaty + f_oplaty) * 1.23, 2)
                tariffs_data[t] = {
                    'energy_cost': round(e, 2), 
                    'ss_cost': round(ss, 2),
                    'fixed_costs': round(f_oplaty, 2), 
                    'total_costs_brutto': total_brutto
                }

            costs_comp = {n: d['total_costs_brutto'] for n, d in tariffs_data.items()}

            annual_usage = []
            for year, year_df in df.groupby(df['timestamp'].dt.year):
                days_in_year = 366 if pd.Timestamp(year, 12, 31).dayofyear == 366 else 365
                days_with_data = year_df['timestamp'].dt.normalize().nunique()
                observed_usage = year_df['total_usage'].sum()
                average_daily_usage = observed_usage / days_with_data if days_with_data else 0

                annual_usage.append({
                    'year': int(year),
                    'observed_kwh': round(observed_usage, 2),
                    'days_with_data': int(days_with_data),
                    'coverage_percent': round(days_with_data / days_in_year * 100, 1),
                    'annualized_kwh': round(average_daily_usage * days_in_year, 2)
                })
            
            return {
                "success": True, 
                "costs_comparison": costs_comp, 
                "cheapest_tariff": min(costs_comp, key=costs_comp.get),
                "total_usage": round(df['total_usage'].sum(), 2), 
                "days_in_period": len(df['timestamp'].dt.date.unique()),
                "reference_tariff": reference_tariff, 
                "tariffs_data": tariffs_data,
                "savings_vs_reference": round(costs_comp[reference_tariff] - min(costs_comp.values()), 2),
                "period_start": df['timestamp'].min().strftime("%Y-%m-%d"), 
                "period_end": df['timestamp'].max().strftime("%Y-%m-%d"),
                "annual_usage": annual_usage,
                "hourly_usage": df.groupby('hour')['total_usage'].mean().reset_index().to_dict('records'),
                # DODAJ INFORMACJĘ O DZIURACH
                "data_quality": anomalies
            }            
        except Exception as e:
            return {"error": f"Błąd analizy: {str(e)}"}

calculator = TariffCalculator()

@app.route('/')
def index(): return render_template('index.html')

@app.route('/tariffs')
def tariffs(): return render_template('tariffs.html')

@app.route('/api/tariffs', methods=['GET', 'POST'])
def api_tariffs():
    if request.method == 'GET':
        return jsonify({'success': True, 'prices': calculator.prices, 'last_update': calculator.last_update})
    data = request.get_json()
    calculator.prices.update({k: float(v) for k, v in data.get('prices', {}).items()})
    calculator.last_update = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(PRICES_FILE, 'w', encoding='utf-8') as f:
        json.dump({'prices': calculator.prices, 'last_update': calculator.last_update}, f, indent=2)
    return jsonify({'success': True, 'timestamp': calculator.last_update})

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files.get('file')
    if not file: 
        return jsonify({'error': 'Brak pliku'})
    
    path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    file.save(path)
    
    res = calculator.analyze_usage(
        path, 
        request.form.get('start_date'), 
        request.form.get('end_date'), 
        request.form.get('reference_tariff', 'G11')
    )
    os.remove(path)
    
    if 'success' in res:
        # 1. Wykres porównania kosztów
        fig_costs = go.Figure(go.Bar(
            x=list(res['costs_comparison'].keys()), 
            y=list(res['costs_comparison'].values()),
            marker_color='#4682B4',
            text=[f"{v:.2f} zł" for v in res['costs_comparison'].values()],
            textposition='auto'
        ))
        fig_costs.update_layout(
            title='Porównanie kosztów taryf (Brutto)',
            xaxis_title='Taryfa',
            yaxis_title='Koszt (zł)',
            height=400
        )
        
        # 2. Wykres godzinowy użycia
        hourly_data = res.get('hourly_usage', [])
        fig_hourly = go.Figure(go.Bar(
            x=[h['hour'] for h in hourly_data],
            y=[h['total_usage'] for h in hourly_data],
            marker_color='#FF6B6B'
        ))
        fig_hourly.update_layout(
            title='Średnie zużycie energii w ciągu doby',
            xaxis_title='Godzina',
            yaxis_title='Zużycie (kWh)',
            height=400
        )
        
        # 3. Wykres stref G12 (musimy dodać dane w analyze_usage)
        # Dla uproszczenia tworzymy proste wykresy kołowe
        # Musisz dodać odpowiednie dane w funkcji analyze_usage
        
        # Tymczasowe dane - zastąp prawdziwymi z analyze_usage
        fig_g12 = go.Figure(go.Pie(
            labels=['Dzień', 'Noc'],
            values=[60, 40],  # Przykładowe wartości
            marker_colors=['#FFA07A', '#20B2AA']
        ))
        fig_g12.update_layout(title='Podział zużycia G12', height=300)
        
        fig_g12w = go.Figure(go.Pie(
            labels=['Dzień', 'Noc+Weekend'],
            values=[50, 50],  # Przykładowe wartości
            marker_colors=['#FFA07A', '#20B2AA']
        ))
        fig_g12w.update_layout(title='Podział zużycia G12w', height=300)
        
        fig_g13 = go.Figure(go.Pie(
            labels=['Przedpołudnie', 'Popołudnie', 'Pozostałe'],
            values=[30, 25, 45],  # Przykładowe wartości
            marker_colors=['#FFD700', '#FF6347', '#32CD32']
        ))
        fig_g13.update_layout(title='Podział zużycia G13', height=300)
        
        # Konwersja do dict (nie do JSON string!)
        res['charts'] = {
            'costs_comparison': fig_costs.to_dict(),
            'hourly_usage': fig_hourly.to_dict(),
            'g12_usage': fig_g12.to_dict(),
            'g12w_usage': fig_g12w.to_dict(),
            'g13_usage': fig_g13.to_dict()
        }
    
    return jsonify(res)

if __name__ == '__main__':
    app.run(debug=True, port=5000)