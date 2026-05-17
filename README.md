# Poznávačka rostlin

Statická webová aplikace pro trénování poznávání divokých rostlin z obrázků. Běží bez backendu a bez npm buildu, takže ji lze hostovat přímo přes GitHub Pages.

## Lokální spuštění

Spusť v kořeni repozitáře:

```sh
python3 -m http.server
```

Potom otevři:

```text
http://localhost:8000
```

Aplikaci je potřeba spouštět přes statický server, protože prohlížeče často blokují načítání JSON souborů přes `file://`.

## GitHub Pages

1. Nahraj tento adresář do GitHub repozitáře.
2. V nastavení repozitáře otevři `Settings -> Pages`.
3. Zvol deploy z větve `main` a složku `/root`.
4. Otevři publikovanou Pages URL.

Není potřeba GitHub Actions ani build krok.

## Instalace jako aplikace

Aplikace obsahuje PWA manifest, service worker a ikony. Na GitHub Pages poběží přes HTTPS, takže ji lze v mobilním prohlížeči přidat na plochu.

- Android/Chrome: otevři stránku a použij nabídku `Instalovat aplikaci` nebo `Přidat na plochu`.
- iPhone/Safari: otevři stránku, použij sdílení a zvol `Přidat na plochu`.

Při lokálním spuštění přes `http://localhost:8000` lze PWA testovat, ale skutečná instalace na telefonu dává smysl hlavně z publikované GitHub Pages URL.

## Data

- Seznam rostlin je v `seznam-divokych-rostlin.md`.
- Aplikace načítá `data/plant-images.json`.
- Obrázky jsou ve složce `images/<slug>/`.
- Lokální progres se ukládá v prohlížeči do `localStorage` pod klíčem `biologiePoznavackaProgressV1`.

Nové nebo opravené obrázky stačí přidat do `images` a aktualizovat `data/plant-images.json`.
