# Power Automate Flow — Aussteller Scraper

## Trigger
**SharePoint — Wenn eine Datei erstellt wird (nur Eigenschaften)**
- Site: [SharePoint site URL]
- Ordner: `/Shared Documents/Scrape Requests/`
- Kullanıcı `.txt` dosyası attığında tetiklenir (içinde scrape edilecek URL var)

## Flow Adımları

### 1. Dateiinhalt abrufen (Get file content)
- **Connector:** SharePoint
- **Aktion:** Dateiinhalt abrufen
- **Dateibezeichner:** Trigger'dan gelen `ID`
- **Sonuç:** `.txt` dosyasının içeriği (URL string)

### 2. Verfassen — URL temizle
- **Aktion:** Verfassen (Compose)
- **Eingabe:** `trim(body('Dateiinhalt_abrufen'))`

### 3. HTTP POST /scrape — Job başlat
- **PA Action adı:** `HTTP`
- **Methode:** POST
- **URI:** `https://<SERVER_IP>:8000/scrape`
- **Headers:**
  - `Content-Type`: `application/json`
  - `X-API-Key`: `99qYW3_SiP4K7wJdZ_xlQ83mtYCjVfJjckVsO-y3V0U`
- **Body:**
```json
{
  "url": "@{outputs('Verfassen')}",
  "format": "excel",
  "limit": 0
}
```
- **Sonuç:** `body('HTTP')?['job_id']`

### 4. Variable initialisieren — status
- **Name:** `status`
- **Typ:** Zeichenfolge (String)
- **Wert:** `queued`

### 5. Wiederholen bis (Do Until) — Polling loop
- **Bedingung:** `variables('status')` ist gleich `completed`

#### 5a. Verzögerung (Delay)
- **Dauer:** 15 Sekunden

#### 5b. HTTP GET /status
- **PA Action adı:** `HTTP_1`
- **Methode:** GET
- **URI:** `https://<SERVER_IP>:8000/scrape/@{body('HTTP')?['job_id']}/status`
- **Headers:**
  - `X-API-Key`: `99qYW3_SiP4K7wJdZ_xlQ83mtYCjVfJjckVsO-y3V0U`

#### 5c. Variable festlegen — status aktualisieren
- **Name:** `status`
- **Wert (fx):** `body('HTTP_1')?['status']`

### 6. Bedingung — Başarılı mı?
- **If:** `variables('status')` ist gleich `completed`

#### 6a. Ja (Başarılı) →

##### 6a-1. HTTP GET /download
- **PA Action adı:** `HTTP_2`
- **Methode:** GET
- **URI:** `https://<SERVER_IP>:8000/scrape/@{body('HTTP')?['job_id']}/download`
- **Headers:**
  - `X-API-Key`: `99qYW3_SiP4K7wJdZ_xlQ83mtYCjVfJjckVsO-y3V0U`

##### 6a-2. SharePoint — Datei erstellen
- **Site:** [SharePoint site URL]
- **Ordnerpfad:** `/Shared Documents/Exhibitor Lists/`
- **Dateiname (fx):** `body('HTTP_1')?['file_name']`
- **Dateiinhalt (fx):** `body('HTTP_2')`

#### 6b. Nein (Başarısız) →

##### 6b-1. Teams — Nachricht posten
- **Posten als:** Flow-Bot
- **Posten in:** Kanal
- **Nachricht:**
```
Scrape fehlgeschlagen!
URL: @{outputs('Verfassen')}
Fehler: @{body('HTTP_1')?['error']}
```

---

## Durum
- [x] Trigger: SharePoint - Wenn eine Datei erstellt wird
- [x] Dateiinhalt abrufen
- [x] Verfassen — URL temizle
- [x] HTTP POST /scrape
- [x] Variable — status = queued
- [x] Do Until polling loop (Delay + HTTP GET status + Variable festlegen)
- [x] Bedingung — completed/failed
- [x] Ja: HTTP download → SharePoint'e kaydet
- [x] Nein: Teams hata bildirimi

## Kalan İşler
- [ ] Server deploy — `<SERVER_IP>` placeholder'larını gerçek IP ile değiştir
- [ ] SharePoint klasörlerini oluştur: `Scrape Requests/` ve `Exhibitor Lists/`
- [ ] Test: klasöre `.txt` dosyası atıp flow'u kontrol et
- [ ] (Opsiyonel) İşlenen .txt dosyalarını arşivle veya sil

## Server Konfiguration (.env)
```
API_KEY=99qYW3_SiP4K7wJdZ_xlQ83mtYCjVfJjckVsO-y3V0U
ALLOWED_ORIGINS=*
```

## SharePoint Klasör Yapısı
```
/Shared Documents/
├── Scrape Requests/       ← .txt dosyaları buraya atılır (trigger)
│   └── Archiv/            ← işlenen dosyalar buraya taşınır
└── Exhibitor Lists/       ← scrape sonuçları (Excel/CSV) buraya kaydedilir
```

## Notlar
- Server deploy edildikten sonra `<SERVER_IP>` değeri güncellenecek
- .txt dosyasında sadece tek bir URL satırı olmalı
- Polling 15sn aralıklarla
- Do Until loop'ta sadece `completed` kontrolü var; `failed` durumunda loop timeout ile çıkar, Bedingung'da Nein dalına düşer
