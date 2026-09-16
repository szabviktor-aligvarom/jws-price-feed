# jws-store.de ar- es keszletfeed

Heti automatikus ar- es keszletfeed a [jws-store.de](https://www.jws-store.de) oldalarol.
A termek azonosito kulcsa a **cikkszam (SKU / Artikelnummer)**, ami a feedben egyedi.

## Allando linkek

Ezek a linkek frissites utan sem valtoznak:

- **JSON:** https://raw.githubusercontent.com/szabviktor-aligvarom/jws-price-feed/main/feed/feed.json
- **CSV:** https://raw.githubusercontent.com/szabviktor-aligvarom/jws-price-feed/main/feed/feed.csv
- Utolso futas riportja: https://raw.githubusercontent.com/szabviktor-aligvarom/jws-price-feed/main/feed/last_run.json

## Fontos tudnivalok az adatrol

- **Penznem:** EUR (a JSON `meta.currency`, a CSV fejleckommentjeben is szerepel).
- **Az arak brutto kiskereskedelmi arak** — a shop nyilvanos, vegfelhasznaloi arai
  (inkl. MwSt.), szallitasi koltseg nelkul. Ezek **nem** nettó beszallitoi arak.
- **A keszlet igaz/hamis, nem darabszam.** A shop nem publikal keszletmennyiseget,
  csak szoveges jelzest ("Sofort versandfertig" / "Produkt vergriffen"), ezert az
  `in_stock` logikai ertek. A `stock_type` mezo ezt explicit jelzi (`boolean`).
- **Listaar / akcio:** ahol a shop athuzott arat mutat, ott az `on_sale` `true`, es a
  `list_price` az athuzott ar. Figyelem: ez a shopban tipikusan **UVP**
  (gyartoi ajanlott fogyasztoi ar), nem a termek korabbi shop-ara — tehat a
  `discount_percent` az UVP-hez kepesti kedvezmeny. A tobbi terméknél a shop nem ad
  athuzott arat, ott `on_sale: false` es `list_price` = `price`.
- Nehany terméknél a shop maga hirdet 0,00 EUR-t (hianyos termekadat a shop oldalan,
  jellemzoen variansos padlofutes-terméknél, es ez naponta valtozhat).
  **Ezek a sorok nem kerulnek a fo feedbe**, mert betoltve ingyen adnanak ki termeket.
  Kulon fajlban lathatok, hogy a shopnal jelezni lehessen:
  https://raw.githubusercontent.com/szabviktor-aligvarom/jws-price-feed/main/feed/excluded_zero_price.json

## Feed mezok

| mezo | jelentes |
|---|---|
| `sku` | cikkszam, egyedi azonosito kulcs |
| `name` | termeknev |
| `price` | aktualis ar (brutto, EUR) |
| `list_price` | listaar / athuzott ar (ha nincs, = `price`) |
| `on_sale` | akcios-e (`true`/`false`) |
| `discount_percent` | kedvezmeny szazalek |
| `in_stock` | keszleten van-e (`true`/`false`) |
| `stock_type` | `boolean` — jelzi, hogy nincs darabszam |
| `availability_text` | a shop sajat keszletjelzese (eredeti nemet szoveg) |
| `delivery_days` | varhato szallitasi napok a shop szerint |
| `weight_g` | suly grammban |
| `url` | termekoldal URL |
| `image_url` | fokep URL |
| `category` | kategoria utvonal |
| `brand` | gyarto |
| `was_duplicate` | duplikalt cikkszambol lett-e osszevonva |
| `duplicate_count` | hany sor tartozott ehhez a cikkszamhoz |
| `last_modified` | a sor generalasanak idopontja |

## Duplikalt cikkszamok

Ha ugyanaz a cikkszam tobbszor szerepel a shopban, a feed **egy** sort tart meg:
elsodlegesen a keszleten levo valtozatot, azon belul a dragabbat. Az ilyen sorokon
`was_duplicate: true`, es a `duplicate_count` mutatja, hany forrassor volt.

## Adatforras

A shop Plentymarkets alapu. Nincs publikus termek-API (`/products.json`,
WooCommerce Store API vagy hirdetett Google/Heureka feed nem elerheto), ezert a
generator a `sitemap.xml`-bol indul, es a termekoldalakon talalhato **strukturalt
adatot** olvassa (JSON-LD + a beagyazott Ceres termek-JSON), nem a HTML megjeleneset.

Ket ismert csapdat a generator kulon kezel:

1. A Plentymarkets `isSalable` flag ezen a shopon megbizhatatlan (rendelheto
   termeket is `false`-nak jelol), ezert a keszletet a shop szoveges
   keszletjelzesebol vezetjuk le.
2. A JSON-LD variansos termekeknel 0 arat adhat; ilyenkor a beagyazott
   `prices.default.price.value` az erveny.

## Csendes hiba elleni vedelem

A generator nem-nulla exit koddal leall, es **nem irja felul a jo adatot**, ha:

- a termekszam 1200 ala esik,
- a termekek 20%-anal tobbnel nincs ar,
- a katalogus az elozo futashoz kepest 35%-nal tobbet zuhan,
- az atlagar az elozo futashoz kepest 35%-nal tobbet mozdul
  (arformatum-hiba, pl. "60,90" -> "6090" elleni vedelem),
- a letoltesek 10%-anal tobb elbukik (blokkolas, halozati hiba),
- a termekoldalak 15%-anal tobb 404-et ad (URL-szerkezet valtozas a shopban).

A sitemapban maradt, mar torolt termekek (404) normalis esetben egyszeruen kimaradnak
a feedbol; csak akkor allitjak le a futast, ha az aranyuk atlepi a fenti kuszobot.

Ilyenkor a workflow elbukik, a commit nem tortenik meg, es a linkeken
**az utolso jo feed marad kint**. A hiba oka a `feed/last_run.json`-ban
es az Actions-futas osszefoglalojaban olvashato.

## Utemezes

GitHub Actions, **hetente hetfon** `30 5 * * 1` (UTC) = **07:30 budapesti nyari ido**.
Figyelem: a GitHub UTC-ben utemez, ezert teli idoszamitasban (CET) ez 06:30-kor fut.
Kezi inditas barmikor: Actions ful -> "Heti ar- es keszletfeed" -> "Run workflow".

A kuszobok egy hetnyi termeszetes valtozast elnezenek (ezert 35%, nem 30%),
de a formatum- vagy forrashiba jellegu elteresre megallnak.

Publikus repoban a GitHub Actions hasznalata ingyenes, igy a feed uzemeltetesi
koltsege nulla.

## Arnaplo

Minden frissites egy commit, igy a commit-tortenet ingyenes arnaplot ad:
barmely korabbi heti feed visszaolvashato a repo tortenetebol.
