# p0stsoc

[English](README.md) · Italiano

Cerca notizie su Google News per keyword (con esclusioni), le mette in un DB
SQLite e le posta sui social. Facebook è il primo; altri network possono
arrivare dopo. Coda di moderazione di default, full-auto o ibrido per
keyword quando ti fidi del filtro.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
p0stsoc initdb          # crea il DB e importa config.yaml
```

Configura keyword e settings (`default_mode`, `fb_page_id`, `post_template`)
in [`config.yaml`](config.yaml) **oppure** dalla web console.
Il token Facebook NON va nel file: si passa via env var (sotto).

## Uso

```bash
p0stsoc fetch                 # scarica le notizie (da mettere in cron)
p0stsoc post --dry-run        # mostra cosa posterebbe, senza chiamare FB
p0stsoc serve                 # console su http://127.0.0.1:8000
p0stsoc export                # DB -> config.yaml
p0stsoc import                # config.yaml -> DB (upsert; --replace allinea le keyword)
p0stsoc prune --days 90       # cancella posted/skipped più vecchi di N giorni (retention)
python -m p0stsoc.test_filters  # self-check del filtro esclusioni
python -m p0stsoc.test_post     # self-check claim anti double-post
python -m p0stsoc.test_config   # self-check import sync + template post
```

Coda: apri la console, per ogni notizia `new` scegli **Posta ora / Approva / Scarta**.
Gli articoli `approved` vengono pubblicati al prossimo `p0stsoc post`.

## Modalità di moderazione

- Default = **review**: si approva a mano (`default_mode: review`).
- **Full auto**: metti `default_mode: auto` → tutto ciò che passa il filtro va in coda `approved`.
- **Ibrido**: `mode: auto` sulla singola keyword, il resto resta in review.

## Facebook: il token (la parte scomoda)

Il codice è banale; la frizione è ottenere un **Page Access Token long-lived**.

1. Crea un'app su https://developers.facebook.com/ (tipo "Business").
2. Aggiungi il prodotto **Facebook Login** e chiedi i permessi
   `pages_manage_posts` e `pages_read_engagement`.
3. Nel **Graph API Explorer** seleziona l'app e la tua Pagina, genera un
   *User Token*, poi scambialo per un token di Pagina **long-lived**
   (`/oauth/access_token` con `grant_type=fb_exchange_token`, poi `/me/accounts`).
4. Prendi l'`id` della Pagina → mettilo in `fb_page_id` (config.yaml, oppure
   console → Keyword → Impostazioni).
5. Esporta il token:

```bash
export P0STSOC_FB_TOKEN="EAAB...il-tuo-page-token"
p0stsoc post           # ora pubblica davvero
```

> Per la produzione (pubblicare su una Pagina non tua o con più utenti) Meta
> richiede **App Review** + verifica business. In dev, sulla tua Pagina, il
> token dal Graph API Explorer basta.

## Cron

```cron
*/15 * * * *  cd /path/p0stsoc && P0STSOC_FB_TOKEN=$TOK .venv/bin/p0stsoc fetch
*/15 * * * *  cd /path/p0stsoc && P0STSOC_FB_TOKEN=$TOK .venv/bin/p0stsoc post
```

Il secondo job serve solo se usi keyword in `auto`/`default_mode: auto`.
In pura review posti dalla console e ti basta il job di `fetch`.

`fetch` e `post` hanno un **lockfile interno** (`$TMPDIR/p0stsoc-{fetch,post}.lock`):
se un run è ancora attivo, quello successivo esce subito — niente run
sovrapposti. In più ogni articolo viene **claimato** (`status=posting`) prima
della chiamata a Facebook: cron e “Posta ora” non possono pubblicare due volte
la stessa riga.

## Limiti noti

- Al momento del post il redirect di Google News viene **risolto nell'URL
  dell'editore** (libreria `googlenewsdecoder`, endpoint interno di Google).
  È un meccanismo fragile per definizione: se il decoder fallisce o Google
  cambia formato, si posta il redirect come fallback — Facebook ne mostra
  comunque un'anteprima.
- `p0stsoc export` **riscrive `config.yaml` perdendo i commenti** (è un dump del DB).
  Se usi il file commentato come documentazione, esporta su un altro path
  (`p0stsoc export config.exported.yaml`) o tieni i commenti altrove.
- `p0stsoc import` **aggiorna** settings e keyword (upsert). Per cancellare
  dal DB le keyword che non sono nel yaml: `p0stsoc import --replace`.
  `initdb` non cancella mai le keyword già presenti.
- Il filtro esclusioni è per **sottostringa** (title+snippet). Una frase
  corta tipo `ai` matcha anche dentro altre parole; usa esclusioni specifiche.
- La console **non ha autenticazione**: servila solo su localhost o dietro un
  reverse-proxy che gestisce l'accesso.

## Licenza

[MIT](LICENSE) © 2026 N1kola Di Bari
