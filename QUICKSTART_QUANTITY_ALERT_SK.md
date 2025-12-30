# Systém Upozornenia na Nízku Zásobu - Rýchly Návod na Nastavenie

## 🚀 Inštalácia a Nastavenie

### Krok 1: Aktivácia Modulu
1. V Odoo prejdite na **Aplikácie → Modulov Inštaluj Modul**
2. Vyhľadajte "Recurring - Contracts Management"
3. Kliknutie na **Nainštalovať**

### Krok 2: Konfigurácia Emaily Spoločnosti
1. Prejdite na **Nastavenia → Spoločnosti**
2. Otvorte svoju spoločnosť
3. V sekcii "Upozornenie na Zásoby" zadajte email(-y):
   - Príklad: `obrunovsky7@gmail.com,oliver.brunovsky@novem.sk,tomas.juricek@novem.sk`

### Krok 3: Aktivácia Upozornenia na Produkte
1. Prejdite na **Produkty → Produkty**
2. Otvorte produkt, pre ktorý chcete aktivovať upozornenia
3. Prejdite na záložku **"Upozornenie na zásoby"**
4. Nastavte:
   - ✅ **Zapnúť upozornenia na zásoby**: Začiarknite
   - 🔢 **Minimálna zásoby**: 2 (alebo vaša požadovaná hodnota)
   - 📅 **Frekvencia upozornenia za týždeň**: 1 (raz za týždeň)
5. Kliknutie **Uložiť**

## 📧 Ako Systém Funguje

- **Denná kontrola**: Cron job automaticky kontroluje zásoby každý deň o 08:00
- **Upozornenie**: Ak je zásoby nižšia ako minimum, pošle sa email
- **Bez opakovania**: Rovnaký produkt nezašle email každý deň - respektuje frekvenciu
- **Resetovanie**: Počítadlo upozornení sa resetuje každú nedeľu

## 📋 Email Upozornenie Obsahuje

```
Upozornenie: Nízka zásoby produktu

Produkt: Názov produktu
Aktuálna zásoby: 1
Minimálna zásoby: 2
Dátum kontroly: 2024-12-30
Prosím, zvážte objednanie tohto produktu.
```

## 🔧 Príklady Nastavenia

### Príklad 1: Mobilný Telefón
- **Minimálna zásoby**: 5
- **Frekvencia**: 1 raz za týždeň
- Ak je skladová zásoby telefónov < 5, dostanete email max. raz za týždeň

### Príklad 2: Kritický Díl
- **Minimálna zásoby**: 10
- **Frekvencia**: 2 krát za týždeň
- Ak je skladová < 10, dostanete email max. 2x za týždeň

### Príklad 3: Vypnúť Upozornenia
- Otvorte produkt a **odčiarknite** "Zapnúť upozornenia na zásoby"
- Žiadne email nebudú poslané

## 🐛 Riešenie Problémov

**Neposílajú sa emaily?**
- Skontrolujte, či je email v nastaviach spoločnosti
- Skontrolujte, či je upozornenie zapnuté na produkte
- Skontrolujte, či je zásoby nižšia ako minimum
- Skontrolujte, či je e-mailový server v Odoo správne nastavený

**Príliš veľa emailov?**
- Znížte frekvenciu (namiesto 2, nastavte 1)
- Zvýšte minimálnu zásobu (aby sa aktivovalo menej často)

**Malo emailov?**
- Zvýšte frekvenciu na 2 alebo viac
- Znížte minimálnu zásobu

## 📞 Podpora

Všetky texty sú v slovenčine.
Všetok kód je v angličtine.
Pre más technické detaily pozri: PRODUCT_QUANTITY_ALERT.md

---

✅ **Systém je teraz pripravený!** Upozornenia budú posielané automaticky každý deň.
